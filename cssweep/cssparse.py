"""Parse CSS text into a small tree of style rules and at-rule blocks.

This is deliberately not a full CSS parser. cssweep only needs rule
boundaries (so cleaning can cut text out), selector lists, line numbers,
and the at-rule context a rule sits in. Declarations are skipped, not
understood — which is also why SCSS-style nesting inside a rule block is
not analysed (see README, Limitations).

Comments are blanked out before parsing so a stray ``{`` inside one
cannot confuse the brace matching. Offsets are preserved, so line
numbers still point at the right place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import CssParseError

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# At-rules whose blocks contain other style rules.
_CONTAINER_AT_RULES = ("@media", "@supports", "@layer", "@container", "@scope")
# At-rules whose blocks contain no style selectors at all (keyframes are
# from/to/percentages, @font-face is declarations only, and so on).
_SKIPPED_AT_RULES = (
    "@keyframes",
    "@-webkit-keyframes",
    "@-moz-keyframes",
    "@-o-keyframes",
    "@font-face",
    "@page",
    "@counter-style",
    "@property",
    "@font-feature-values",
)


@dataclass
class StyleRule:
    """One ``selector { ... }`` rule."""

    selectors: list[str]
    prelude_start: int  # offset where the selector list starts
    prelude_end: int  # offset where the selector list ends (before "{")
    start: int  # offset of the rule start (same as prelude_start)
    end: int  # offset just past the closing "}"
    line: int  # 1-based line the rule starts on
    at_context: tuple[str, ...] = ()  # enclosing at-rules, outermost first


@dataclass
class AtBlock:
    """An at-rule with a block, e.g. ``@media ... { ... }``."""

    prelude: str
    start: int
    end: int
    line: int
    children: list[Node] = field(default_factory=list)
    at_context: tuple[str, ...] = ()


Node = StyleRule | AtBlock


def _blank_comments(text: str) -> str:
    """Replace each comment with spaces, keeping newlines and offsets."""

    def _blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return _COMMENT_RE.sub(_blank, text)


def _find_matching_brace(text: str, open_index: int) -> int | None:
    """Index of the ``}`` matching the ``{`` at ``open_index``.

    Strings are honoured, so a brace inside ``content: "}"`` does not
    end the block early. Returns ``None`` when the block never closes.
    """
    depth = 0
    in_string: str | None = None
    i = open_index
    while i < len(text):
        ch = text[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"'):
            in_string = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _split_selectors(prelude: str) -> list[str]:
    """Split ``.a, .b:not(.c, .d)`` on top-level commas only."""
    parts: list[str] = []
    depth = 0
    in_string: str | None = None
    current: list[str] = []
    for ch in prelude:
        if in_string is not None:
            current.append(ch)
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"'):
            in_string = ch
            current.append(ch)
        elif ch in ("(", "["):
            depth += 1
            current.append(ch)
        elif ch in (")", "]"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [part for part in parts if part]


def _parse_block(
    text: str, start: int, end: int, filename: str, at_context: tuple[str, ...]
) -> list[Node]:
    """Parse the rules inside ``text[start:end]`` (a file or a block)."""
    nodes: list[Node] = []
    i = start
    while i < end:
        while i < end and text[i].isspace():
            i += 1
        if i >= end:
            break

        # Read the prelude: everything up to "{" or ";" at depth 0.
        j = i
        depth = 0
        in_string: str | None = None
        terminator = ""
        while j < end:
            ch = text[j]
            if in_string is not None:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_string:
                    in_string = None
            elif ch in ("'", '"'):
                in_string = ch
            elif ch in ("(", "["):
                depth += 1
            elif ch in (")", "]"):
                depth -= 1
            elif depth == 0 and ch in "{;":
                terminator = ch
                break
            j += 1

        prelude = text[i:j].strip()
        if not prelude:
            if terminator == "{":
                # A bare block with no selector — not valid CSS.
                raise CssParseError(
                    filename,
                    "found a block with no selector before it",
                    text.count("\n", 0, j) + 1,
                )
            i = j + 1
            continue
        if not terminator:
            raise CssParseError(
                filename,
                f"unclosed rule starting with {prelude[:40]!r}",
                text.count("\n", 0, i) + 1,
            )
        if terminator == ";":
            # @import, @charset and friends: no block, nothing to do.
            i = j + 1
            continue

        close = _find_matching_brace(text, j)
        if close is None:
            raise CssParseError(
                filename,
                f"unbalanced '{{' in rule starting with {prelude[:40]!r}",
                text.count("\n", 0, i) + 1,
            )
        line = text.count("\n", 0, i) + 1

        if prelude.startswith("@"):
            name = prelude.split(None, 1)[0].lower()
            if name in _CONTAINER_AT_RULES:
                children = _parse_block(
                    text, j + 1, close, filename, at_context + (prelude,)
                )
                nodes.append(
                    AtBlock(
                        prelude=prelude,
                        start=i,
                        end=close + 1,
                        line=line,
                        children=children,
                        at_context=at_context,
                    )
                )
            # Anything else with a block (@keyframes, @font-face, ...)
            # holds no style selectors, so it is skipped wholesale.
            i = close + 1
            continue

        nodes.append(
            StyleRule(
                selectors=_split_selectors(prelude),
                prelude_start=i,
                prelude_end=j,
                start=i,
                end=close + 1,
                line=line,
                at_context=at_context,
            )
        )
        i = close + 1
    return nodes


def parse_css(text: str, filename: str = "<css>") -> list[Node]:
    """Parse a stylesheet into top-level rules and at-rule blocks."""
    return _parse_block(_blank_comments(text), 0, len(text), filename, ())


def iter_style_rules(nodes: list[Node]) -> list[StyleRule]:
    """Every style rule in the tree, in source order."""
    rules: list[StyleRule] = []
    for node in nodes:
        if isinstance(node, StyleRule):
            rules.append(node)
        else:
            rules.extend(iter_style_rules(node.children))
    return rules
