from __future__ import annotations

from typing import Optional, Sequence

from ast_nodes import (
    AssignNode, BinaryExprNode, BoolNode, ExprNode, IdentifierNode, IfNode,
    NumberNode, OverflowCheckNode, ProgramNode, StmtNode, VarDeclNode,
)
from lexer import CompilerError, Lexer, Token


INTEGER_MAXIMUMS = {"i32": 2**31 - 1, "i64": 2**63 - 1}


class Parser:
    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.type_scopes: list[dict[str, str]] = [{}]

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
        if type_name in INTEGER_MAXIMUMS:
            self.check_integer_literal_ranges(initializer, type_name)
        self.type_scopes[-1][name.text] = type_name
        return VarDeclNode(mutability, storage, type_name, name.text, initializer)

    def parse_assign(self) -> AssignNode:
        name = self.eat("IDENT")
        self.eat("ASSIGN", "expected 💧 after the identifier")
        value = self.parse_expr()
        type_name = self.lookup_type(name.text)
        if type_name in INTEGER_MAXIMUMS:
            self.check_integer_literal_ranges(value, type_name)
        return AssignNode(name, name.text, value)

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
        self.type_scopes.append({})
        try:
            while self.peek().kind not in ("RBRACE", "EOF"):
                statements.append(self.parse_stmt())
            self.eat("RBRACE", "expected '}' to close the block")
        finally:
            self.type_scopes.pop()
        return statements

    def lookup_type(self, name: str) -> Optional[str]:
        for scope in reversed(self.type_scopes):
            if name in scope:
                return scope[name]
        return None

    def check_integer_literal_ranges(self, expression: ExprNode, type_name: str) -> None:
        if isinstance(expression, NumberNode):
            maximum = str(INTEGER_MAXIMUMS[type_name])
            literal = expression.value
            if (len(literal) > len(maximum)
                    or (len(literal) == len(maximum) and literal > maximum)):
                self.error(
                    Token("NUMBER", expression.value, expression.line, expression.column),
                    f"integer literal is out of range for {type_name}",
                )
            return
        for _, children in expression.child_groups():
            for child in children:
                if isinstance(child, ExprNode):
                    self.check_integer_literal_ranges(child, type_name)

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
