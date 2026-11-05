from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional


INVISIBLE_CATEGORIES = {"Cc", "Cf", "Mn", "Zl", "Zp", "Zs"}


def describe_character(fragment: str) -> str:
    if fragment and all(unicodedata.category(char) in INVISIBLE_CATEGORIES for char in fragment):
        return " ".join(f"U+{ord(char):04X}" for char in fragment)
    return repr(fragment)


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int
    column: int


class CompilerError(Exception):
    def __init__(self, line: int, column: int, message: str) -> None:
        super().__init__(message)
        self.line = line
        self.column = column
        self.message = message


EMOJI_TOKENS = {
    "🪵".encode("utf-8"): "MUTABLE",
    "🌿".encode("utf-8"): "IMMUTABLE",
    "🧊".encode("utf-8"): "CONST",
    "🌰".encode("utf-8"): "I32",
    "🌳".encode("utf-8"): "I64",
    "🌸".encode("utf-8"): "BOOL_TYPE",
    "💧".encode("utf-8"): "ASSIGN",
    "🌤".encode("utf-8"): "IF",
    "🌤️".encode("utf-8"): "IF",
    "🌧".encode("utf-8"): "ELSE",
    "🌧️".encode("utf-8"): "ELSE",
    "⚖".encode("utf-8"): "EQUAL",
    "⚖️".encode("utf-8"): "EQUAL",
    "✂".encode("utf-8"): "NOT_EQUAL",
    "✂️".encode("utf-8"): "NOT_EQUAL",
    "🪴".encode("utf-8"): "OVERFLOW",
    "🍎".encode("utf-8"): "TRUE",
    "🍏".encode("utf-8"): "FALSE",
    "🪡".encode("utf-8"): "TERMINATOR",
}


def build_trie(table: dict[bytes, str]) -> dict:
    root: dict = {}
    for spelling, kind in table.items():
        node = root
        for byte in spelling:
            node = node.setdefault(byte, {})
        node[None] = kind
    return root

SINGLE_BYTE_TOKENS = {
    ord("+"): "PLUS",
    ord("-"): "MINUS",
    ord("*"): "STAR",
    ord("/"): "SLASH",
    ord("{"): "LBRACE",
    ord("}"): "RBRACE",
    ord("("): "LPAREN",
    ord(")"): "RPAREN",
    ord(":"): "COLON",
    ord(","): "COMMA",
    ord("."): "DOT",
}


class Lexer:
    """Recognize tokens by walking UTF-8 source bytes without regular expressions."""

    def __init__(self, source: bytes) -> None:
        self.source = source
        self.offset = 3 if source.startswith(b"\xef\xbb\xbf") else 0
        self.line = 1
        self.column = 1
        self.emoji_trie = build_trie(EMOJI_TOKENS)

    def advance(self, count: int = 1) -> None:
        for _ in range(count):
            byte = self.source[self.offset]
            self.offset += 1
            if byte == ord("\n"):
                self.line += 1
                self.column = 1
            else:
                self.column += 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.offset < len(self.source):
            byte = self.source[self.offset]
            if byte in (ord(" "), ord("\t"), ord("\r"), ord("\n")):
                self.advance()
                continue

            start_line, start_column = self.line, self.column
            if ord("0") <= byte <= ord("9"):
                start = self.offset
                while self.offset < len(self.source) and ord("0") <= self.source[self.offset] <= ord("9"):
                    self.advance()
                if self.offset < len(self.source) and self._is_identifier_start(self.source[self.offset]):
                    raise CompilerError(start_line, start_column, "identifier cannot start with a digit")
                text = self.source[start:self.offset].decode("ascii")
                tokens.append(Token("NUMBER", text, start_line, start_column))
                continue

            if self._is_identifier_start(byte):
                start = self.offset
                while self.offset < len(self.source) and self._is_identifier_part(self.source[self.offset]):
                    self.advance()
                text = self.source[start:self.offset].decode("ascii")
                tokens.append(Token("IDENT", text, start_line, start_column))
                continue

            if byte == ord("-") and self.source[self.offset:self.offset + 2] == b"->":
                self.advance(2)
                tokens.append(Token("ARROW", "->", start_line, start_column))
                continue
            if self.source[self.offset:self.offset + 2] == b"..":
                self.advance(2)
                tokens.append(Token("RANGE", "..", start_line, start_column))
                continue

            if byte == ord('"'):
                tokens.append(self._string_token(start_line, start_column))
                continue

            if byte in SINGLE_BYTE_TOKENS:
                self.advance()
                tokens.append(Token(SINGLE_BYTE_TOKENS[byte], chr(byte), start_line, start_column))
                continue

            node = self.emoji_trie
            cursor = self.offset
            matched: Optional[tuple[int, str]] = None
            while cursor < len(self.source) and self.source[cursor] in node:
                node = node[self.source[cursor]]
                cursor += 1
                if None in node:
                    matched = (cursor, node[None])
            if matched is not None:
                end, kind = matched
                text = self.source[self.offset:end].decode("utf-8")
                self.advance(end - self.offset)
                tokens.append(Token(kind, text, start_line, start_column))
                continue

            bad_byte = self.source[self.offset]
            if bad_byte >= 0x80:
                width = 1
                if bad_byte & 0xE0 == 0xC0:
                    width = 2
                elif bad_byte & 0xF0 == 0xE0:
                    width = 3
                elif bad_byte & 0xF8 == 0xF0:
                    width = 4
                try:
                    fragment = self.source[self.offset:self.offset + width].decode("utf-8")
                except UnicodeDecodeError:
                    message = f"invalid UTF-8 byte 0x{bad_byte:02x}"
                else:
                    message = f"unexpected character {describe_character(fragment)}"
            else:
                message = f"unexpected character {describe_character(chr(bad_byte))}"
            raise CompilerError(start_line, start_column, message)

        tokens.append(Token("EOF", "", self.line, self.column))
        return tokens

    def _string_token(self, line: int, column: int) -> Token:
        self.advance()
        chars: list[str] = []
        while self.offset < len(self.source):
            byte = self.source[self.offset]
            if byte == ord('"'):
                self.advance()
                return Token("STRING", "".join(chars), line, column)
            if byte in (ord("\n"), ord("\r")):
                raise CompilerError(line, column, "newline in string literal")
            if byte == ord("\\"):
                self.advance()
                if self.offset >= len(self.source):
                    break
                escaped = self.source[self.offset]
                replacements = {
                    ord("n"): "\n",
                    ord("t"): "\t",
                    ord('"'): '"',
                    ord("\\"): "\\",
                }
                if escaped not in replacements:
                    raise CompilerError(self.line, self.column, "invalid string escape")
                chars.append(replacements[escaped])
                self.advance()
                continue
            width = 1 if byte < 0x80 else (
                2 if byte & 0xE0 == 0xC0 else
                3 if byte & 0xF0 == 0xE0 else
                4 if byte & 0xF8 == 0xF0 else 1
            )
            try:
                fragment = self.source[self.offset:self.offset + width].decode("utf-8")
            except UnicodeDecodeError:
                raise CompilerError(self.line, self.column, f"invalid UTF-8 byte 0x{byte:02x}")
            chars.append(fragment)
            self.advance(width)
        raise CompilerError(line, column, "unterminated string literal")

    @staticmethod
    def _is_identifier_start(byte: int) -> bool:
        return (ord("a") <= byte <= ord("z") or ord("A") <= byte <= ord("Z")
                or byte == ord("_"))

    @classmethod
    def _is_identifier_part(cls, byte: int) -> bool:
        return cls._is_identifier_start(byte) or ord("0") <= byte <= ord("9")
