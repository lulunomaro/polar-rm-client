"""Local text-editor key policy, independent of Qt and the window.

The input adapter decides which editor owns a key. This module sees only key
meaning and text; it cannot inspect overlays, admit robot controls, or change a
binding. Chat effects use its existing editor port and one explicit close
callback. Search returns a field update without retaining mutable window state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Protocol


class TextKey(Enum):
    OTHER = auto()
    CANCEL = auto()
    SUBMIT = auto()
    BACKSPACE = auto()
    UP = auto()
    DOWN = auto()
    PAGE_UP = auto()
    PAGE_DOWN = auto()


class ChatEditor(Protocol):
    def send_input(self) -> bool: ...
    def backspace(self) -> None: ...
    def history_older(self) -> None: ...
    def history_newer(self) -> None: ...
    def scroll(self, lines: int) -> None: ...
    def insert(self, text: str) -> None: ...


@dataclass(frozen=True)
class ChatEdit:
    key: TextKey
    text: str = ""

    def apply(self, editor: ChatEditor, *, close: Callable[[], None]) -> None:
        """Apply ordered editor effects with the original send/close contract.

        A false send result still closes chat. A raised send exception stops
        before close; it propagates so the Qt adapter does not refresh or accept
        the event after an earlier editor action failed.
        """
        if self.key is TextKey.SUBMIT:
            editor.send_input()
            close()
        elif self.key is TextKey.CANCEL:
            close()
        elif self.key is TextKey.BACKSPACE:
            editor.backspace()
        elif self.key is TextKey.UP:
            editor.history_older()
        elif self.key is TextKey.DOWN:
            editor.history_newer()
        elif self.key is TextKey.PAGE_UP:
            editor.scroll(5)
        elif self.key is TextKey.PAGE_DOWN:
            editor.scroll(-5)
        elif self.text:
            editor.insert(self.text)


def chat_edit(key: TextKey, text: str) -> ChatEdit:
    """All chat key presses, including autorepeat, remain owned by the editor."""
    return ChatEdit(key, text if key is TextKey.OTHER and text.isprintable() else "")


@dataclass(frozen=True)
class SearchEdit:
    key: TextKey
    text: str = ""

    @property
    def finish(self) -> bool:
        return self.key in (TextKey.SUBMIT, TextKey.CANCEL)

    @property
    def changes_query(self) -> bool:
        return self.key is TextKey.BACKSPACE or bool(self.text)

    def apply(self, query: str) -> str:
        return query[:-1] if self.key is TextKey.BACKSPACE else query + self.text


def search_edit(key: TextKey, text: str) -> SearchEdit:
    """Plan only; closing search must not eagerly read or rewrite its query.

    Chat-only navigation keys may still insert printable text in search.
    """
    inserts = key not in (TextKey.BACKSPACE, TextKey.SUBMIT, TextKey.CANCEL)
    return SearchEdit(key, text if inserts and text.isprintable() else "")
