"""Small, safe presentation primitives for Goal Native's interactive terminal."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from typing import TextIO


def terminal_text(value: str) -> str:
    """Remove terminal controls without changing ordinary text or line breaks."""
    return "".join(char for char in value if char in "\n\t" or (
        ord(char) >= 32 and not 127 <= ord(char) <= 159
    ))


def color_enabled(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty()) and "NO_COLOR" not in os.environ and os.environ.get("TERM", "") not in {"", "dumb", "unknown"}
    except (AttributeError, OSError):
        return False


class Terminal:
    """Inline colored output; user/model text is always sanitized and unstyled."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        self.color = color_enabled(self.stream)

    def style(self, text: str, color: str) -> str:
        if not self.color:
            return text
        codes = {"cyan": "36", "dim": "2", "yellow": "33", "red": "31", "green": "32"}
        return f"\x1b[{codes[color]}m{text}\x1b[0m"

    def write(self, text: str = "", *, color: str | None = None, end: str = "\n") -> None:
        safe = terminal_text(text)
        if not hasattr(self.stream, "send"):
            safe = self.wrap(safe)
        self.stream.write(self.style(safe, color) if color else safe)
        self.stream.write(end)
        self.stream.flush()

    def field(self, label: str, value: str, *, color: str = "dim") -> None:
        # Only the trusted label gets ANSI; metadata remains unstyled.
        self.write(label, color=color)
        self.write(value)

    def wrap(self, text: str) -> str:
        """Hard-wrap without dropping whitespace or wide/combining characters."""
        result = []
        column = 0
        width = self.width()
        def cells(char: str) -> int:
            return 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        for token in re.findall(r"\n|[^\S\n]+|[^\s]+", text.expandtabs(4)):
            size = sum(cells(char) for char in token)
            if not token.isspace() and size <= width and column and column + size > width:
                result.append("\n")
                column = 0
            for char in token:
                if char == "\n":
                    result.append(char)
                    column = 0
                    continue
                size = cells(char)
                if column + size > width:
                    result.append("\n")
                    column = 0
                result.append(char)
                column += size
        return "".join(result)

    def prompt(self, text: str) -> str:
        # readline counts only the printable prompt span as cursor width.
        if not self.color:
            return text
        return f"\001\x1b[36m\002{text}\001\x1b[0m\002"

    def width(self, minimum: int = 1) -> int:
        try:
            return max(minimum, shutil.get_terminal_size((80, 24)).columns)
        except OSError:
            return 80


__all__ = ["Terminal", "color_enabled", "terminal_text"]
