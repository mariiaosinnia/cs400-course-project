from __future__ import annotations

from typing import Optional, Sequence

from lexer import Token


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
