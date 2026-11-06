"""Error types for cssweep.

Every failure a user can hit gets its own type so the CLI can print a
plain-English message and a proper exit code instead of a traceback.
"""

from __future__ import annotations


class CssweepError(Exception):
    """Base class for all errors cssweep reports to the user."""


class PathError(CssweepError):
    """The project path is missing, not a directory, or unreadable."""


class CssParseError(CssweepError):
    """A stylesheet could not be parsed."""

    def __init__(self, filename: str, message: str, line: int | None = None) -> None:
        self.filename = filename
        self.line = line
        where = f" (line {line})" if line is not None else ""
        super().__init__(f"{filename}: {message}{where}")


class ConfigError(CssweepError):
    """The .cssweep.json config file is invalid."""


class WriteError(CssweepError):
    """A cleaned stylesheet could not be written."""
