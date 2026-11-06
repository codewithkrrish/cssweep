"""Thin CLI layer: parse flags, run the scan, print the result.

All real work lives in scanner / analyze / cleaner / report; this
module only wires them together and turns errors into messages.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze
from .cleaner import CleanStats, clean_result
from .config import find_config
from .errors import ConfigError, CssParseError, CssweepError, PathError, WriteError
from .report import render_json, render_text
from .scanner import find_files
from .usage import collect_usage

EXIT_OK = 0
EXIT_UNUSED_FOUND = 1  # only with --fail-on-unused
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cssweep",
        description=(
            "Find CSS selectors nothing in your project uses, "
            "and optionally clean them out."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="project directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--css",
        action="append",
        default=None,
        metavar="GLOB",
        help="stylesheet glob, relative to the project (repeatable; "
        "default: walk for .css/.scss/.less files)",
    )
    parser.add_argument(
        "--templates",
        action="append",
        default=None,
        metavar="GLOB",
        help="template/script glob, relative to the project (repeatable; "
        "default: walk for .html/.js/.jsx/.ts/.tsx/.vue/... files)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip paths matching this glob, relative to the project "
        "(repeatable; adds to .cssweep.json)",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="REGEX",
        help="never report selectors whose name matches this regex "
        "(repeatable; adds to .cssweep.json)",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="config file to use instead of .cssweep.json",
    )
    parser.add_argument(
        "--skip-ids",
        action="store_true",
        help="only check class selectors, leave #ids alone",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="report format (default: text)",
    )
    parser.add_argument(
        "--fail-on-unused",
        action="store_true",
        help="exit 1 when any unused selector is found (for CI)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the unused selectors from the stylesheets",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --clean, only say what would change",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --clean, rewrite the original files in place; "
        "without it, cleaned copies are written next to them",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="also list stylesheets with nothing unused",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cssweep {__version__}",
    )
    return parser


def _read_css_files(css_files: list[Path]) -> dict[Path, str]:
    texts: dict[Path, str] = {}
    for path in css_files:
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CssParseError(
                str(path), f"not valid UTF-8 ({exc}); skipping binary files?"
            ) from exc
        except OSError as exc:
            raise CssweepError(f"could not read {path}: {exc}") from exc
    return texts


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename."""
    tmp = path.with_name(f".{path.name}.cssweep-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise WriteError(f"could not write {path}: {exc}") from exc


def _clean_output_path(css_path: Path) -> Path:
    """Where a non-force clean writes: style.swept.css, auto-renamed
    with a counter if that file already exists."""
    candidate = css_path.with_name(f"{css_path.stem}.swept{css_path.suffix}")
    n = 2
    while candidate.exists():
        candidate = css_path.with_name(
            f"{css_path.stem}.swept-{n}{css_path.suffix}"
        )
        n += 1
    return candidate


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    word = singular if n == 1 else (plural or singular + "s")
    return f"{n} {word}"


def _run_clean(
    result, texts: dict[Path, str], *, dry_run: bool, force: bool
) -> int:
    """Apply cleaning and report what happened. Returns an exit code."""
    cleaned = clean_result(result, texts)
    total = CleanStats()
    changed: list[tuple[Path, Path, CleanStats]] = []
    for path, (new_text, stats) in cleaned.items():
        total.removed_rules += stats.removed_rules
        total.trimmed_rules += stats.trimmed_rules
        total.removed_props += stats.removed_props
        total.bytes_saved += stats.bytes_saved
        if stats.bytes_saved == 0:
            continue
        if dry_run:
            changed.append((path, path, stats))
            continue
        target = path if force else _clean_output_path(path)
        if force:
            _atomic_write(target, new_text)
        else:
            try:
                target.write_text(new_text, encoding="utf-8")
            except OSError as exc:
                raise WriteError(f"could not write {target}: {exc}") from exc
        changed.append((path, target, stats))

    if not changed:
        print("nothing to clean — no unused selectors found.")
        return EXIT_OK
    for source, target, stats in changed:
        summary = (
            f"{_plural(stats.removed_rules, 'rule')} removed, "
            f"{_plural(stats.trimmed_rules, 'rule')} trimmed, "
            f"{_plural(stats.removed_props, 'custom property', 'custom properties')} "
            f"dropped, ~{stats.bytes_saved} bytes saved"
        )
        if dry_run:
            print(f"would clean {source}: {summary}")
        elif target == source:
            print(f"cleaned {source} in place: {summary}")
        else:
            print(f"cleaned {source} -> {target}: {summary}")
    return EXIT_OK


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except CssweepError as exc:
        print(f"cssweep: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.exists():
        raise PathError(f"{args.path}: no such file or directory")
    if not root.is_dir():
        raise PathError(f"{args.path}: not a directory")
    if args.clean and args.dry_run and args.force:
        print(
            "cssweep: --dry-run with --force does nothing extra — "
            "dry run never writes",
            file=sys.stderr,
        )

    config = find_config(root, args.config)
    ignore_patterns = list(config.ignore) + list(args.ignore)
    try:
        ignore_res = [re.compile(p) for p in ignore_patterns]
    except re.error as exc:
        raise ConfigError(f"bad --ignore regex: {exc}") from exc
    exclude = list(config.exclude) + list(args.exclude)
    skip_ids = bool(args.skip_ids or config.skip_ids)

    css_files, template_files = find_files(
        root,
        css_globs=args.css,
        template_globs=args.templates,
        exclude=exclude,
    )
    if not css_files:
        print(f"cssweep: no stylesheets found under {root}")
        return EXIT_OK

    texts = _read_css_files(css_files)
    usage = collect_usage(template_files, css_files)
    result = analyze(root, texts, usage, ignore_res, skip_ids)
    result.template_files = len(template_files)

    if args.clean:
        return _run_clean(result, texts, dry_run=args.dry_run, force=args.force)

    if args.format == "json":
        print(render_json(result), end="")
    else:
        print(render_text(result, verbose=args.verbose), end="")
    if args.fail_on_unused and result.total_unused > 0:
        return EXIT_UNUSED_FOUND
    return EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
