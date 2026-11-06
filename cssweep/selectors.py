"""Pull class names, ids and custom properties out of selectors.

A selector like ``.card.featured > #main a:hover`` contributes the
classes ``card`` and ``featured`` and the id ``main``. Pseudo-classes,
pseudo-elements, combinators and element names carry no identity of
their own, so they are ignored — a rule made only of those can never
be reported as unused.
"""

from __future__ import annotations

import re

_CLASS_RE = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
_ID_RE = re.compile(r"#(-?[A-Za-z_][A-Za-z0-9_-]*)")
_ATTRIBUTE_RE = re.compile(r"\[[^\]]*\]")
_CUSTOM_PROP_DEF_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
_VAR_USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")


def selector_names(selector: str) -> tuple[set[str], set[str]]:
    """Return ``(class names, id names)`` used by one selector string.

    Attribute selectors are stripped first: ``[data-state="open"]``
    names no class, and a ``.`` inside a quoted attribute value is not
    a class selector. (That also means ``[class~="active"]`` is not
    recognised as a use — see the README limitations.)
    """
    cleaned = _ATTRIBUTE_RE.sub("", selector)
    classes = set(_CLASS_RE.findall(cleaned))
    ids = set(_ID_RE.findall(cleaned))
    return classes, ids


def custom_props_defined(declarations: str) -> set[str]:
    """Custom properties (``--name: ...``) defined in a declaration block."""
    return set(_CUSTOM_PROP_DEF_RE.findall(declarations))


def custom_props_used(text: str) -> set[str]:
    """Custom properties referenced as ``var(--name)`` anywhere in ``text``."""
    return set(_VAR_USE_RE.findall(text))
