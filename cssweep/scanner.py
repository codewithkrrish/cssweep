"""Find the stylesheets and templates inside a project directory."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

CSS_EXTS = {".css", ".scss", ".less"}
TEMPLATE_EXTS = {
    ".html",
    ".htm",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".svelte",
    ".astro",
    ".erb",
    ".ejs",
    ".hbs",
    ".jinja",
    ".jinja2",
    ".twig",
    ".php",
    ".razor",
    ".cshtml",
}

# cssweep's own cleaned output (style.swept.css, style.swept-2.css, ...)
# is never scanned — otherwise a second run would report on its output.
_SWEPT_OUTPUT_RE = re.compile(r"\.swept(-\d+)?\.[a-z]+$", re.IGNORECASE)

# Directories that are never worth scanning: build output, dependencies,
# caches. Hidden directories are skipped too — a project's own dotfiles
# are not where its templates live.
DEFAULT_SKIP_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    "coverage",
    ".tox",
    "target",
    ".idea",
}


def _is_excluded(rel_posix: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pattern) for pattern in patterns)


def find_files(
    root: Path,
    *,
    css_globs: list[str] | None = None,
    template_globs: list[str] | None = None,
    exclude: list[str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Return ``(css files, template files)`` under ``root``, sorted.

    When a glob list is given for a category it replaces the directory
    walk for that category; otherwise files are found by extension.
    """
    exclude = exclude or []
    css_files: list[Path] = []
    template_files: list[Path] = []

    if css_globs:
        for pattern in css_globs:
            css_files.extend(sorted(root.glob(pattern)))
    else:
        css_files = _walk(root, CSS_EXTS, exclude)

    if template_globs:
        for pattern in template_globs:
            template_files.extend(sorted(root.glob(pattern)))
    else:
        template_files = _walk(root, TEMPLATE_EXTS, exclude)

    # A file can only be one or the other; de-duplicate within each list.
    css_files = sorted(set(css_files))
    template_files = sorted(set(template_files) - set(css_files))
    return css_files, template_files


def _walk(root: Path, exts: set[str], exclude: list[str]) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in _walk_dirs(root):
        rel_dir = dirpath.relative_to(root).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in DEFAULT_SKIP_DIRS
            and not d.startswith(".")
            and not _is_excluded(f"{rel_dir}/{d}" if rel_dir != "." else d, exclude)
        ]
        for name in filenames:
            path = dirpath / name
            if path.suffix.lower() not in exts:
                continue
            if _SWEPT_OUTPUT_RE.search(name):
                # cssweep's own cleaned copies — never scan them, or a
                # second run would report on its own output.
                continue
            rel = path.relative_to(root).as_posix()
            if _is_excluded(rel, exclude):
                continue
            found.append(path)
    return sorted(found)


def _walk_dirs(root: Path):
    """os.walk, but yielding Path objects and pruning as it goes."""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        yield Path(dirpath), dirnames, filenames
