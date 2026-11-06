"""Tie parsing and usage together: which selectors are dead, and where."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .cssparse import Node, StyleRule, iter_style_rules, parse_css
from .errors import CssParseError
from .selectors import custom_props_defined, selector_names
from .usage import Usage


@dataclass
class UnusedSelector:
    """One class, id or custom property nothing references."""

    name: str  # ".card", "#nav", "--accent" — as it appears in the CSS
    kind: str  # "class" | "id" | "custom-property"
    selector: str  # the full selector it was found in
    file: Path
    line: int
    at_context: tuple[str, ...] = ()


@dataclass
class DuplicateSelector:
    """The same selector string defined in more than one rule."""

    selector: str
    locations: list[tuple[Path, int]]  # (file, line) for each definition


@dataclass
class FileReport:
    file: Path
    rules: int
    selectors: int
    unused: list[UnusedSelector] = field(default_factory=list)
    # Per-rule cleaning decisions, filled in by the cleaner (rule -> action).
    rules_info: list["RuleDecision"] = field(default_factory=list)


@dataclass
class RuleDecision:
    """What cleaning would do to one style rule."""

    rule: StyleRule
    action: str  # "keep" | "trim" | "drop"
    kept_selectors: list[str] = field(default_factory=list)
    dropped_props: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    root: Path
    files: list[FileReport]
    duplicates: list[DuplicateSelector]
    css_files: int
    template_files: int

    @property
    def total_rules(self) -> int:
        return sum(f.rules for f in self.files)

    @property
    def total_selectors(self) -> int:
        return sum(f.selectors for f in self.files)

    @property
    def total_unused(self) -> int:
        return sum(len(f.unused) for f in self.files)

    @property
    def unused_names(self) -> set[str]:
        return {u.name for f in self.files for u in f.unused}


def _is_ignored(
    name: str, selector: str, ignore_res: list[re.Pattern[str]]
) -> bool:
    # Patterns are matched against the name with and without its leading
    # "." / "#" / "--", and against the whole selector — so an ignore
    # pattern like "^js-" works the way people write it.
    bare = name.lstrip(".#")
    return any(
        rx.search(name) or rx.search(bare) or rx.search(selector)
        for rx in ignore_res
    )


def analyze_file(
    path: Path,
    text: str,
    usage: Usage,
    ignore_res: list[re.Pattern[str]],
    skip_ids: bool,
) -> FileReport:
    """Analyse one stylesheet against the project's usage data."""
    nodes = parse_css(text, filename=str(path))
    rules = iter_style_rules(nodes)
    report = FileReport(file=path, rules=len(rules), selectors=0)

    for rule in rules:
        report.selectors += len(rule.selectors)
        kept: list[str] = []
        dropped_any = False
        for selector in rule.selectors:
            classes, ids = selector_names(selector)
            if not skip_ids:
                names = [(f".{c}", "class") for c in sorted(classes)] + [
                    (f"#{i}", "id") for i in sorted(ids)
                ]
            else:
                names = [(f".{c}", "class") for c in sorted(classes)]
            if not names:
                # Element-only selector (``a``, ``body``...): always kept.
                kept.append(selector)
                continue
            used = [n for n, _ in names if n[1:] in usage.names]
            ignored = [
                (n, kind)
                for n, kind in names
                if _is_ignored(n, selector, ignore_res)
            ]
            if used or ignored:
                kept.append(selector)
                continue
            # Every name in this selector is unused: the selector is dead.
            # (If a selector mixes a used and an unused name, the used one
            # saves it — dropping it could break the used class.)
            dropped_any = True
            for n, kind in names:
                if _is_ignored(n, selector, ignore_res):
                    continue
                # The same dead name in two selectors of one rule
                # (".a, .a .b") is reported once, not once per selector.
                if any(
                    u.name == n and u.line == rule.line for u in report.unused
                ):
                    continue
                report.unused.append(
                    UnusedSelector(
                        name=n,
                        kind=kind,
                        selector=selector,
                        file=path,
                        line=rule.line,
                        at_context=rule.at_context,
                    )
                )
        if not dropped_any:
            action = "keep"
        elif kept:
            action = "trim"
        else:
            action = "drop"

        # Custom properties: a ``--x`` defined in this rule and never
        # referenced with var(--x) anywhere is dead too. The rule itself
        # is only dropped when it has no selectors left *and* no used
        # declarations — handled by the cleaner via dropped_props.
        declarations = text[rule.prelude_end + 1 : rule.end - 1]
        defined = custom_props_defined(declarations)
        dropped_props = sorted(
            p
            for p in defined
            if p not in usage.custom_props
            and not _is_ignored(p, rule.selectors[0] if rule.selectors else "", ignore_res)
        )
        for prop in dropped_props:
            report.unused.append(
                UnusedSelector(
                    name=prop,
                    kind="custom-property",
                    selector=rule.selectors[0] if rule.selectors else "",
                    file=path,
                    line=rule.line,
                    at_context=rule.at_context,
                )
            )
        report.rules_info.append(
            RuleDecision(
                rule=rule,
                action=action,
                kept_selectors=kept,
                dropped_props=dropped_props,
            )
        )
    return report


def find_duplicates(files: list[FileReport]) -> list[DuplicateSelector]:
    """Selectors defined in more than one rule, across all files."""
    seen: dict[str, list[tuple[Path, int]]] = {}
    for report in files:
        for decision in report.rules_info:
            for selector in decision.rule.selectors:
                key = re.sub(r"\s+", " ", selector).strip()
                seen.setdefault(key, []).append((report.file, decision.rule.line))
    return [
        DuplicateSelector(selector=key, locations=locs)
        for key, locs in seen.items()
        if len(locs) > 1
    ]


def analyze(
    root: Path,
    css_texts: dict[Path, str],
    usage: Usage,
    ignore_res: list[re.Pattern[str]],
    skip_ids: bool,
) -> ScanResult:
    """Analyse every stylesheet in ``css_texts`` (already read)."""
    files = [
        analyze_file(path, text, usage, ignore_res, skip_ids)
        for path, text in sorted(css_texts.items())
    ]
    return ScanResult(
        root=root,
        files=files,
        duplicates=find_duplicates(files),
        css_files=len(css_texts),
        template_files=0,  # filled in by the caller, which owns the scan
    )
