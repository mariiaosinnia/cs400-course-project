from __future__ import annotations

from typing import Optional, Sequence

from ast_nodes import (
    AssignNode, BinaryExprNode, BoolNode, CallNode, ExprNode, ExprStmtNode,
    FieldAccessNode, FieldDeclNode, ForNode, FunctionDeclNode, IdentifierNode, IfNode,
    MemberCallNode, NumberNode, ObjectLiteralNode, OverflowCheckNode, ProgramNode,
    ReturnNode, StmtNode, StringNode, StructDeclNode, VarDeclNode, WhileNode,
)
from lexer import CompilerError, Lexer, Token


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

    def _next_kind(self) -> str:
        return self.tokens[min(self.index + 1, len(self.tokens) - 1)].kind

    def _is_function_decl_start(self) -> bool:
        return (
            self.peek().kind == "IDENT" and self.peek().text == "fn"
            and self.index + 2 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "IDENT"
            and self.tokens[self.index + 2].kind == "LPAREN"
        )

    def _is_struct_decl_start(self) -> bool:
        return (
            self.peek().kind == "IDENT" and self.peek().text == "struct"
            and self.index + 2 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "IDENT"
            and self.tokens[self.index + 2].kind == "LBRACE"
        )

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
        elif kind == "IF":
            statement = self.parse_if()
        elif kind == "IDENT" and self.peek().text == "while" and self._next_kind() != "ASSIGN":
            statement = self.parse_while()
        elif kind == "IDENT" and self.peek().text == "for":
            statement = self.parse_for()
        elif self._is_function_decl_start():
            statement = self.parse_function()
        elif self._is_struct_decl_start():
            statement = self.parse_struct()
        elif (kind == "IDENT" and self.peek().text == "return"
              and self._next_kind() not in ("ASSIGN", "DOT")):
            token = self.eat("IDENT")
            statement = ReturnNode(token, self.parse_expr())
        elif kind == "ELSE":
            self.error(self.peek(), "else without matching if")
        elif kind == "IDENT":
            token = self.peek()
            expression = self.parse_postfix(self.parse_primary())
            if self.peek().kind == "ASSIGN":
                self.index += 1
                if not isinstance(expression, (IdentifierNode, FieldAccessNode)):
                    self.error(token, "assignment target must be a variable or field")
                value = self.parse_expr()
                if isinstance(expression, IdentifierNode):
                    target_type = self.lookup_type(expression.name)
                    if target_type in ("i32", "i64"):
                        self.check_integer_literal_ranges(value, target_type)
                target_name = (expression.name if isinstance(expression, IdentifierNode)
                               else expression.field)
                statement = AssignNode(token, target_name, value, expression)
            elif isinstance(expression, IdentifierNode):
                self.error(self.peek(), "expected 💧 after the identifier")
            else:
                statement = ExprStmtNode(token, expression)
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

    def parse_type(self) -> str:
        token = self.peek()
        primitive_types = {"I32": "i32", "I64": "i64", "BOOL_TYPE": "bool"}
        if token.kind in primitive_types:
            self.index += 1
            return primitive_types[token.kind]
        if token.kind == "IDENT":
            if token.text == "string":
                self.index += 1
                return "string"
            if token.text in ("i32", "i64", "bool"):
                self.error(token, f"expected a type, found {self._found(token)}")
            self.index += 1
            return token.text
        self.error(token, f"expected a type, found {self._found(token)}")

    def parse_var_decl(self) -> VarDeclNode:
        mutability = self.peek()
        self.index += 1
        if self.peek().kind == "IDENT" and self.index + 1 < len(self.tokens) and self.tokens[self.index + 1].kind == "ASSIGN":
            self.error(self.peek(), f"expected a type, found {self._found(self.peek())}")
        type_name = self.parse_type()
        name = self.eat("IDENT", "expected an identifier after the type")
        self.eat("ASSIGN", "expected 💧 after the identifier")
        initializer = self.parse_expr()
        if type_name in ("i32", "i64"):
            self.check_integer_literal_ranges(initializer, type_name)
        self.type_scopes[-1][name.text] = type_name
        return VarDeclNode(mutability, mutability.kind.lower(), type_name, name.text, initializer)

    def parse_function(self, owner: Optional[str] = None) -> FunctionDeclNode:
        token = self.eat("IDENT")
        name = self.eat("IDENT", "expected a function name")
        self.eat("LPAREN", "expected '(' after function name")
        parameters: list[tuple[str, str, Token]] = []
        if self.peek().kind != "RPAREN":
            while True:
                param = self.eat("IDENT", "expected a parameter name")
                self.eat("COLON", "expected ':' after parameter name")
                parameters.append((param.text, self.parse_type(), param))
                if self.peek().kind != "COMMA":
                    break
                self.index += 1
        self.eat("RPAREN", "expected ')' after parameters")
        self.eat("ARROW", "expected '->' before result type")
        result_type = self.parse_type()
        self.eat("LBRACE", "expected '{' before function body")
        self.type_scopes.append({param_name: param_type for param_name, param_type, _ in parameters})
        try:
            body = self.parse_block()
        finally:
            self.type_scopes.pop()
        return FunctionDeclNode(token, name.text, parameters, result_type, body, owner)

    def parse_struct(self) -> StructDeclNode:
        token = self.eat("IDENT")
        name = self.eat("IDENT", "expected a structure name")
        self.eat("LBRACE", "expected '{' after structure name")
        fields: list[FieldDeclNode] = []
        methods: list[FunctionDeclNode] = []
        while self.peek().kind not in ("RBRACE", "EOF"):
            if self._is_function_decl_start():
                methods.append(self.parse_function(name.text))
            elif self.peek().kind in ("MUTABLE", "IMMUTABLE", "CONST"):
                mutable = self.peek().kind == "MUTABLE"
                field_token = self.peek()
                self.index += 1
                type_name = self.parse_type()
                field_name = self.eat("IDENT", "expected a field name")
                fields.append(FieldDeclNode(field_token, field_name.text, type_name, mutable))
            else:
                self.error(self.peek(), "expected a field or member function")
            if self.peek().kind == "TERMINATOR":
                self.index += 1
            elif self.peek().kind not in ("RBRACE", "EOF"):
                previous = self.tokens[self.index - 1]
                if self.peek().line == previous.line:
                    self.error(self.peek(), "structure members on the same line must be separated by 🪡")
        self.eat("RBRACE", "expected '}' to close structure")
        return StructDeclNode(token, name.text, fields, methods)

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

    def parse_while(self) -> WhileNode:
        token = self.eat("IDENT")
        condition = self.parse_expr()
        self.eat("LBRACE", "expected '{' after the while condition")
        return WhileNode(token, condition, self.parse_block())

    def parse_for(self) -> ForNode:
        token = self.eat("IDENT")
        name = self.eat("IDENT", "expected a loop variable after 'for'")
        word = self.eat("IDENT", "expected 'in' after the loop variable")
        if word.text != "in":
            self.error(word, "expected 'in' after the loop variable")
        start = self.parse_expr()
        self.eat("RANGE", "expected '..' between loop bounds")
        end = self.parse_expr()
        self.eat("LBRACE", "expected '{' after the for bounds")
        self.type_scopes.append({name.text: "i32"})
        try:
            body = self.parse_block()
        finally:
            self.type_scopes.pop()
        return ForNode(token, name.text, start, end, body)

    def parse_block(self) -> list[StmtNode]:
        if self.peek().kind == "RBRACE":
            self.error(self.peek(), "a block must contain at least one statement")
        statements: list[StmtNode] = []
        self.type_scopes.append({})
        try:
            while self.peek().kind not in ("RBRACE", "EOF"):
                if self._is_function_decl_start() or self._is_struct_decl_start():
                    self.error(self.peek(), "function and structure declarations are only allowed at top level")
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
        maximum = {"i32": 2**31 - 1, "i64": 2**63 - 1}[type_name]
        if isinstance(expression, NumberNode):
            if int(expression.value) > maximum:
                self.error(Token("NUMBER", expression.value, expression.line, expression.column),
                           f"integer literal is out of range for {type_name}")
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
        if self.peek().kind == "OVERFLOW":
            token = self.eat("OVERFLOW")
            self.eat("LPAREN", "expected '(' after 🪴")
            expression = self.parse_expr()
            self.eat("RPAREN", "expected ')' after the overflow check")
            return OverflowCheckNode(token, expression)
        expression = self.parse_primary()
        return self.parse_postfix(expression)

    def parse_primary(self) -> ExprNode:
        token = self.peek()
        if token.kind == "NUMBER":
            self.index += 1
            return NumberNode(token)
        if token.kind in ("TRUE", "FALSE"):
            self.index += 1
            return BoolNode(token, token.kind == "TRUE")
        if token.kind == "STRING":
            self.index += 1
            return StringNode(token)
        if token.kind == "IDENT":
            self.index += 1
            if self._is_object_initializer():
                return self.parse_object_literal(token)
            return IdentifierNode(token)
        if token.kind == "LPAREN":
            self.index += 1
            expression = self.parse_expr()
            self.eat("RPAREN", "expected ')' after the expression")
            return expression
        self.error(token, f"expected an expression, found {self._found(token)}")

    def _is_object_initializer(self) -> bool:
        return (
            self.peek().kind == "LBRACE"
            and self.index + 2 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "IDENT"
            and self.tokens[self.index + 2].kind == "COLON"
        )

    def parse_object_literal(self, type_token: Token) -> ObjectLiteralNode:
        self.eat("LBRACE")
        fields: list[tuple[str, ExprNode]] = []
        if self.peek().kind != "RBRACE":
            while True:
                name = self.eat("IDENT", "expected a field name")
                self.eat("COLON", "expected ':' after field name")
                fields.append((name.text, self.parse_expr()))
                if self.peek().kind != "COMMA":
                    break
                self.index += 1
        self.eat("RBRACE", "expected '}' after object fields")
        return ObjectLiteralNode(type_token, type_token.text, fields)

    def parse_postfix(self, expression: ExprNode) -> ExprNode:
        while True:
            if self.peek().kind == "LPAREN" and isinstance(expression, IdentifierNode):
                token = self.eat("LPAREN")
                arguments = self.parse_arguments()
                expression = CallNode(token, expression.name, arguments)
            elif self.peek().kind == "DOT":
                self.index += 1
                member = self.eat("IDENT", "expected a field or method name after '.'")
                if self.peek().kind == "LPAREN":
                    token = self.eat("LPAREN")
                    expression = MemberCallNode(token, expression, member.text, self.parse_arguments())
                else:
                    expression = FieldAccessNode(member, expression, member.text)
            else:
                return expression

    def parse_arguments(self) -> list[ExprNode]:
        arguments: list[ExprNode] = []
        if self.peek().kind != "RPAREN":
            while True:
                arguments.append(self.parse_expr())
                if self.peek().kind != "COMMA":
                    break
                self.index += 1
        self.eat("RPAREN", "expected ')' after arguments")
        return arguments


def compile_source(source: bytes) -> ProgramNode:
    return Parser(Lexer(source).tokenize()).parse_program()
