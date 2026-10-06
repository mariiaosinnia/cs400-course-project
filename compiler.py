from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


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

    @staticmethod
    def _is_identifier_start(byte: int) -> bool:
        return (ord("a") <= byte <= ord("z") or ord("A") <= byte <= ord("Z")
                or byte == ord("_"))

    @classmethod
    def _is_identifier_part(cls, byte: int) -> bool:
        return cls._is_identifier_start(byte) or ord("0") <= byte <= ord("9")


class ASTNode:
    def __init__(self, token: Token) -> None:
        self.line = token.line
        self.column = token.column

    def description(self) -> str:
        raise NotImplementedError

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return ()

    def format_tree(self, depth: int = 0) -> list[str]:
        lines = ["  " * depth + self.description()]
        for label, nodes in self.child_groups():
            lines.append("  " * (depth + 1) + label + ":")
            for node in nodes:
                lines.extend(node.format_tree(depth + 2))
        return lines


class StmtNode(ASTNode):
    pass


class ExprNode(ASTNode):
    pass


class ProgramNode(ASTNode):
    def __init__(self, token: Token, statements: list[StmtNode]) -> None:
        super().__init__(token)
        self.statements = statements

    def description(self) -> str:
        return f"ProgramNode @{self.line}:{self.column}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("statements", self.statements),)


class VarDeclNode(StmtNode):
    def __init__(self, token: Token, storage: str, type_name: str, name: str,
                 initializer: ExprNode) -> None:
        super().__init__(token)
        self.storage = storage
        self.type_name = type_name
        self.name = name
        self.initializer = initializer

    def description(self) -> str:
        return (f"VarDeclNode @{self.line}:{self.column} storage={self.storage} "
                f"type={self.type_name} name={self.name}")

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("initializer", (self.initializer,)),)


class AssignNode(StmtNode):
    def __init__(self, token: Token, name: str, value: ExprNode) -> None:
        super().__init__(token)
        self.name = name
        self.value = value

    def description(self) -> str:
        return f"AssignNode @{self.line}:{self.column} name={self.name}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("value", (self.value,)),)


class IfNode(StmtNode):
    def __init__(self, token: Token, condition: ExprNode, then_body: list[StmtNode],
                 else_body: Optional[list[StmtNode]]) -> None:
        super().__init__(token)
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body

    def description(self) -> str:
        return f"IfNode @{self.line}:{self.column} has_else={str(self.else_body is not None).lower()}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        groups: list[tuple[str, Sequence[ASTNode]]] = [("condition", (self.condition,)),
                                                        ("then", self.then_body)]
        if self.else_body is not None:
            groups.append(("else", self.else_body))
        return groups


class BinaryExprNode(ExprNode):
    def __init__(self, token: Token, left: ExprNode, operator: str, right: ExprNode) -> None:
        super().__init__(token)
        self.left = left
        self.operator = operator
        self.right = right

    def description(self) -> str:
        return f"BinaryExprNode @{self.line}:{self.column} operator={self.operator}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("left", (self.left,)), ("right", (self.right,)))


class NumberNode(ExprNode):
    def __init__(self, token: Token) -> None:
        super().__init__(token)
        self.value = token.text

    def description(self) -> str:
        return f"NumberNode @{self.line}:{self.column} value={self.value}"


class BoolNode(ExprNode):
    def __init__(self, token: Token, value: bool) -> None:
        super().__init__(token)
        self.value = value

    def description(self) -> str:
        return f"BoolNode @{self.line}:{self.column} value={str(self.value).lower()}"


class IdentifierNode(ExprNode):
    def __init__(self, token: Token) -> None:
        super().__init__(token)
        self.name = token.text

    def description(self) -> str:
        return f"IdentifierNode @{self.line}:{self.column} name={self.name}"


class OverflowCheckNode(ExprNode):
    def __init__(self, token: Token, expression: ExprNode) -> None:
        super().__init__(token)
        self.expression = expression

    def description(self) -> str:
        return f"OverflowCheckNode @{self.line}:{self.column}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("expression", (self.expression,)),)


class Parser:
    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> Token:
        return self.tokens[self.index]

    def eat(self, kind: str, message: Optional[str] = None) -> Token:
        token = self.peek()
        if token.kind != kind:
            self.error(token, message or f"expected {kind.lower()}, found {self._found(token)}")
        self.index += 1
        return token

    def error(self, token: Token, message: str) -> None:
        raise CompilerError(token.line, token.column, message)

    @staticmethod
    def _found(token: Token) -> str:
        return "end of input" if token.kind == "EOF" else repr(token.text)

    def parse_program(self) -> ProgramNode:
        if self.peek().kind == "EOF":
            self.error(self.peek(), "expected a statement")
        first = self.peek()
        statements: list[StmtNode] = []
        while self.peek().kind != "EOF":
            statements.append(self.parse_stmt())
        return ProgramNode(first, statements)

    def parse_stmt(self) -> StmtNode:
        kind = self.peek().kind
        if kind in ("MUTABLE", "IMMUTABLE", "CONST"):
            statement = self.parse_var_decl()
        elif kind == "IDENT":
            statement = self.parse_assign()
        elif kind == "IF":
            statement = self.parse_if()
        elif kind == "ELSE":
            self.error(self.peek(), "else without matching if")
        else:
            self.error(self.peek(), f"expected a statement, found {self._found(self.peek())}")
        statement_end_line = self.tokens[self.index - 1].line
        has_terminator = self.peek().kind == "TERMINATOR"
        if has_terminator:
            self.index += 1
        following = self.peek()
        if (following.kind not in ("EOF", "RBRACE", "ELSE")
                and following.line == statement_end_line and not has_terminator):
            self.error(following, "statements on the same line must be separated by 🪡")
        return statement

    def parse_var_decl(self) -> VarDeclNode:
        mutability = self.peek()
        self.index += 1
        storage = {"MUTABLE": "mutable", "IMMUTABLE": "immutable", "CONST": "const"}[mutability.kind]
        type_token = self.peek()
        if type_token.kind not in ("I32", "I64", "BOOL_TYPE"):
            self.error(type_token, f"expected a type, found {self._found(type_token)}")
        self.index += 1
        name = self.eat("IDENT", "expected an identifier after the type")
        self.eat("ASSIGN", "expected 💧 after the identifier")
        initializer = self.parse_expr()
        type_name = {"I32": "i32", "I64": "i64", "BOOL_TYPE": "bool"}[type_token.kind]
        return VarDeclNode(mutability, storage, type_name, name.text, initializer)

    def parse_assign(self) -> AssignNode:
        name = self.eat("IDENT")
        self.eat("ASSIGN", "expected 💧 after the identifier")
        return AssignNode(name, name.text, self.parse_expr())

    def parse_if(self) -> IfNode:
        token = self.eat("IF")
        condition = self.parse_expr()
        self.eat("LBRACE", "expected '{' after the condition")
        then_body = self.parse_block()
        else_body: Optional[list[StmtNode]] = None
        if (self.peek().kind == "TERMINATOR"
                and self.index + 1 < len(self.tokens)
                and self.tokens[self.index + 1].kind == "ELSE"):
            self.error(self.peek(), "statement terminator is not allowed between '}' and else")
        if self.peek().kind == "ELSE":
            self.index += 1
            self.eat("LBRACE", "expected '{' after 🌧️")
            else_body = self.parse_block()
        return IfNode(token, condition, then_body, else_body)

    def parse_block(self) -> list[StmtNode]:
        if self.peek().kind == "RBRACE":
            self.error(self.peek(), "a block must contain at least one statement")
        statements: list[StmtNode] = []
        while self.peek().kind not in ("RBRACE", "EOF"):
            statements.append(self.parse_stmt())
        self.eat("RBRACE", "expected '}' to close the block")
        return statements

    def parse_expr(self) -> ExprNode:
        expression = self.parse_add_expr()
        if self.peek().kind in ("EQUAL", "NOT_EQUAL"):
            operator = self.peek()
            self.index += 1
            right = self.parse_add_expr()
            if self.peek().kind in ("EQUAL", "NOT_EQUAL"):
                self.error(self.peek(), "comparison operators cannot be chained")
            expression = BinaryExprNode(operator, expression, operator.text, right)
        return expression

    def parse_add_expr(self) -> ExprNode:
        expression = self.parse_term()
        while self.peek().kind in ("PLUS", "MINUS"):
            operator = self.peek()
            self.index += 1
            expression = BinaryExprNode(operator, expression, operator.text, self.parse_term())
        return expression

    def parse_term(self) -> ExprNode:
        expression = self.parse_factor()
        while self.peek().kind in ("STAR", "SLASH"):
            operator = self.peek()
            self.index += 1
            expression = BinaryExprNode(operator, expression, operator.text, self.parse_factor())
        return expression

    def parse_factor(self) -> ExprNode:
        token = self.peek()
        if token.kind == "NUMBER":
            self.index += 1
            return NumberNode(token)
        if token.kind in ("TRUE", "FALSE"):
            self.index += 1
            return BoolNode(token, token.kind == "TRUE")
        if token.kind == "IDENT":
            self.index += 1
            return IdentifierNode(token)
        if token.kind == "OVERFLOW":
            return self.parse_overflow_check()
        if token.kind == "LPAREN":
            self.index += 1
            expression = self.parse_expr()
            self.eat("RPAREN", "expected ')' after the expression")
            return expression
        self.error(token, f"expected an expression, found {self._found(token)}")

    def parse_overflow_check(self) -> OverflowCheckNode:
        token = self.eat("OVERFLOW")
        self.eat("LPAREN", "expected '(' after 🪴")
        expression = self.parse_expr()
        self.eat("RPAREN", "expected ')' after the overflow check")
        return OverflowCheckNode(token, expression)


def compile_source(source: bytes) -> ProgramNode:
    return Parser(Lexer(source).tokenize()).parse_program()


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "--ast":
        print("usage: python3 compiler.py --ast input.txt", file=sys.stderr)
        return 2
    try:
        source = Path(arguments[1]).read_bytes()
        program = compile_source(source)
    except CompilerError as error:
        print(f"compilation error: line {error.line}:{error.column}: {error.message}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"compilation error: line 1:1: {error.strerror or 'could not read input file'}", file=sys.stderr)
        return 1
    print("\n".join(program.format_tree()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
