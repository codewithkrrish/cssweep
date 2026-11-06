"""Load ``.cssweep.json`` — the optional per-project config file.

Example (lives at the project root, next to the stylesheets)::

    {
      "ignore": ["^js-", "is-.*", "no-js"],
      "exclude": ["vendor/**", "legacy/**"],
      "skip_ids": false
    }

``ignore`` is a list of regexes matched against class names, id names,
custom property names and whole selector strings. ``exclude`` is a
list of fnmatch globs matched against paths relative to the project
root. CLI flags add to the config values rather than replacing them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

CONFIG_FILENAME = ".cssweep.json"


@dataclass
class Config:
    ignore: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    skip_ids: bool = False

    def ignore_res(self) -> list[re.Pattern[str]]:
        return [re.compile(pattern) for pattern in self.ignore]


def load_config(path: Path) -> Config:
    """Read and validate a config file, raising ConfigError on problems."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: config must be a JSON object, not {type(raw).__name__}")

    config = Config()
    for key in ("ignore", "exclude"):
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{path}: {key!r} must be a list of strings")
        setattr(config, key, list(value))
    if not isinstance(raw.get("skip_ids", False), bool):
        raise ConfigError(f"{path}: 'skip_ids' must be true or false")
    config.skip_ids = bool(raw.get("skip_ids", False))

    unknown = sorted(set(raw) - {"ignore", "exclude", "skip_ids"})
    if unknown:
        raise ConfigError(f"{path}: unknown option(s): {', '.join(unknown)}")

    for pattern in config.ignore:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{path}: bad ignore regex {pattern!r}: {exc}") from exc
    return config


def find_config(root: Path, explicit: str | None) -> Config:
    """Load the explicit config, or the one at the project root if any."""
    if explicit is not None:
        return load_config(Path(explicit))
    candidate = root / CONFIG_FILENAME
    if candidate.is_file():
        return load_config(candidate)
    return Config()
