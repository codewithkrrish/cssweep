"""Render a scan result as human-readable text or JSON."""

from __future__ import annotations

import json
from pathlib import Path

from .analyze import ScanResult


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def render_text(result: ScanResult, *, verbose: bool = False) -> str:
    """The default report: one section per stylesheet, then a summary."""
    lines: list[str] = []
    for report in result.files:
        rel = _rel(result.root, report.file)
        if not report.unused:
            if verbose:
                lines.append(
                    f"{rel}: clean — {report.selectors} selectors, "
                    f"{report.rules} rules, nothing unused"
                )
            continue
        lines.append(f"{rel} — {len(report.unused)} unused")
        for unused in report.unused:
            context = ""
            if unused.at_context:
                context = f"  [in {unused.at_context[-1]}]"
            lines.append(
                f"  line {unused.line}: {unused.name}  ({unused.kind}){context}"
            )
        lines.append("")

    if result.duplicates:
        lines.append("duplicate selectors (defined more than once):")
        for dup in result.duplicates:
            places = ", ".join(
                f"{_rel(result.root, f)}:{line}" for f, line in dup.locations
            )
            lines.append(f"  {dup.selector}  — {places}")
        lines.append("")

    lines.append(
        f"{result.total_unused} unused selectors in {result.total_selectors} "
        f"total across {result.css_files} stylesheet(s) "
        f"({result.template_files} template/script file(s) scanned)"
    )
    return "\n".join(lines).rstrip() + "\n"


def render_json(result: ScanResult) -> str:
    """Machine-readable report for CI and editor integrations."""
    payload = {
        "root": str(result.root),
        "css_files": result.css_files,
        "template_files": result.template_files,
        "total_rules": result.total_rules,
        "total_selectors": result.total_selectors,
        "total_unused": result.total_unused,
        "files": [
            {
                "file": _rel(result.root, report.file),
                "rules": report.rules,
                "selectors": report.selectors,
                "unused": [
                    {
                        "name": u.name,
                        "kind": u.kind,
                        "selector": u.selector,
                        "line": u.line,
                        "at_context": list(u.at_context),
                    }
                    for u in report.unused
                ],
            }
            for report in result.files
        ],
        "duplicates": [
            {
                "selector": dup.selector,
                "locations": [
                    {"file": _rel(result.root, f), "line": line}
                    for f, line in dup.locations
                ],
            }
            for dup in result.duplicates
        ],
    }
    return json.dumps(payload, indent=2) + "\n"
