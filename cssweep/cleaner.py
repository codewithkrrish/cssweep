"""Rewrite a stylesheet without its unused rules.

Cleaning works on text spans, not on re-serialised CSS, so everything
kept comes out byte-for-byte identical — formatting, comments, ordering
and all. Three kinds of edit:

* a rule whose selectors are all dead is removed wholesale;
* a rule with a mix of used and dead selectors keeps the used ones;
* an unused ``--custom-property`` declaration is cut out of a rule
  that is otherwise kept.

An at-rule block (``@media`` ...) is removed only when every rule
inside it is removed. Edits inside a removed span are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .analyze import FileReport, RuleDecision, ScanResult
from .cssparse import AtBlock, Node, StyleRule, iter_style_rules, parse_css


@dataclass
class CleanStats:
    removed_rules: int = 0
    trimmed_rules: int = 0
    removed_props: int = 0
    bytes_saved: int = 0


def _find_declaration_span(
    text: str, block_start: int, block_end: int, prop: str
) -> tuple[int, int] | None:
    """Span of ``--prop: value;`` inside a rule's declaration block.

    The scan honours strings and parentheses, so a ``;`` inside
    ``url(data:...;base64,...)`` does not end the declaration early.
    """
    pattern = re.compile(re.escape(prop) + r"\s*:")
    match = pattern.search(text, block_start, block_end)
    if match is None:
        return None
    i = match.end()
    depth = 0
    in_string: str | None = None
    while i < block_end:
        ch = text[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"'):
            in_string = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            return (match.start(), i + 1)
        i += 1
    # No semicolon: last declaration before the closing brace.
    return (match.start(), block_end)


def _edits_for_node(
    node: Node, decisions: dict[int, RuleDecision], edits: list[tuple[int, int, str]]
) -> bool:
    """Collect edits for one tree node. Returns True if it is fully removed."""
    if isinstance(node, StyleRule):
        decision = decisions[id(node)]
        if decision.action == "drop":
            edits.append((node.start, node.end, ""))
            return True
        if decision.action == "trim":
            edits.append(
                (
                    node.prelude_start,
                    node.prelude_end,
                    ", ".join(decision.kept_selectors),
                )
            )
        return False
    # AtBlock: recurse into every child first (a list comprehension,
    # not all() over a generator — all() would stop at the first kept
    # child and the remaining children would never be visited).
    child_results = [_edits_for_node(child, decisions, edits) for child in node.children]
    children_removed = bool(node.children) and all(child_results)
    if children_removed and node.children:
        # The child edits are inside this span — drop them, drop the block.
        edits[:] = [e for e in edits if not (e[0] >= node.start and e[1] <= node.end)]
        edits.append((node.start, node.end, ""))
        return True
    return False


def clean_text(text: str, report: FileReport) -> tuple[str, CleanStats]:
    """Return ``(cleaned text, stats)`` for one stylesheet."""
    nodes = parse_css(text, filename=str(report.file))
    # The text is re-parsed here, so the rule objects are new ones.
    # Pair them with the analysis decisions by source order — parsing
    # is deterministic, so the two walks line up exactly.
    parsed_rules = iter_style_rules(nodes)
    decisions = {
        id(rule): decision
        for rule, decision in zip(parsed_rules, report.rules_info)
    }
    stats = CleanStats()
    for decision in report.rules_info:
        if decision.action == "drop":
            stats.removed_rules += 1
        elif decision.action == "trim":
            stats.trimmed_rules += 1
        stats.removed_props += len(decision.dropped_props)

    edits: list[tuple[int, int, str]] = []
    for node in nodes:
        _edits_for_node(node, decisions, edits)

    # Custom-property declarations: cut out of rules that survive.
    for decision in report.rules_info:
        if not decision.dropped_props or decision.action == "drop":
            continue
        rule = decision.rule
        for prop in decision.dropped_props:
            span = _find_declaration_span(
                text, rule.prelude_end + 1, rule.end - 1, prop
            )
            if span is not None:
                edits.append((span[0], span[1], ""))

    # Apply from the end backwards so earlier offsets stay valid.
    # Edits inside an already-removed span are skipped.
    removed_ranges: list[tuple[int, int]] = []
    result = text
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        if any(start >= rs and end <= re_ for rs, re_ in removed_ranges):
            continue
        if replacement == "":
            removed_ranges.append((start, end))
        result = result[:start] + replacement + result[end:]
    stats.bytes_saved = len(text) - len(result)
    return result, stats


def clean_result(result: ScanResult, texts: dict) -> dict:
    """Clean every file in a scan result; returns {path: (text, stats)}."""
    cleaned: dict = {}
    for report in result.files:
        cleaned[report.file] = clean_text(texts[report.file], report)
    return cleaned
