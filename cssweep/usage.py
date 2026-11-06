"""Work out which class/id names a project actually uses.

The check is deliberately literal: a class or id counts as used when
its exact name shows up as a word in any template or script file —
``class="btn"``, ``className="btn"``, ``el.classList.add('btn')``,
``:class="{ btn: active }"`` in Vue, and so on all match, because the
name itself is what is searched for.

That keeps the tool honest about its blind spot instead of pretending
to be clever: a class assembled at runtime (``'btn-' + size``) or
injected by a framework will not be found, so it shows up as unused.
The README says so, and ``--ignore`` exists for exactly those names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .selectors import custom_props_used

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


@dataclass
class Usage:
    """What the non-CSS side of a project references."""

    names: set[str]  # class/id tokens found in templates and scripts
    custom_props: set[str]  # --vars referenced via var() in any file


def _read_text(path: Path) -> str:
    # A template file with a stray bad byte should not kill the run;
    # cssweep only needs the identifiers in it.
    return path.read_text(encoding="utf-8", errors="ignore")


def collect_usage(template_files: list[Path], css_files: list[Path]) -> Usage:
    """Scan templates for used names and all files for var(--x) references."""
    names: set[str] = set()
    for path in template_files:
        names.update(_IDENT_RE.findall(_read_text(path)))

    props: set[str] = set()
    for path in list(template_files) + list(css_files):
        props.update(custom_props_used(_read_text(path)))
    return Usage(names=names, custom_props=props)
