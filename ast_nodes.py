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
    def __init__(self, token: Token, name: str, value: ExprNode,
                 target: Optional[ExprNode] = None) -> None:
        super().__init__(token)
        self.name = name
        self.value = value
        self.target = target or IdentifierNode(token)

    def description(self) -> str:
        return f"AssignNode @{self.line}:{self.column} name={self.name}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        if isinstance(self.target, IdentifierNode):
            return (("value", (self.value,)),)
        return (("target", (self.target,)), ("value", (self.value,)))


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


class StringNode(ExprNode):
    def __init__(self, token: Token) -> None:
        super().__init__(token)
        self.value = token.text

    def description(self) -> str:
        return f"StringNode @{self.line}:{self.column} value={self.value!r}"


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


class ReturnNode(StmtNode):
    def __init__(self, token: Token, value: ExprNode) -> None:
        super().__init__(token)
        self.value = value

    def description(self) -> str:
        return f"ReturnNode @{self.line}:{self.column}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("value", (self.value,)),)


class ExprStmtNode(StmtNode):
    def __init__(self, token: Token, expression: ExprNode) -> None:
        super().__init__(token)
        self.expression = expression

    def description(self) -> str:
        return f"ExprStmtNode @{self.line}:{self.column}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("expression", (self.expression,)),)


class CallNode(ExprNode):
    def __init__(self, token: Token, callee: str, arguments: list[ExprNode]) -> None:
        super().__init__(token)
        self.callee = callee
        self.arguments = arguments

    def description(self) -> str:
        return f"CallNode @{self.line}:{self.column} callee={self.callee} arguments={len(self.arguments)}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("arguments", self.arguments),)


class FieldAccessNode(ExprNode):
    def __init__(self, token: Token, object_expr: ExprNode, field: str) -> None:
        super().__init__(token)
        self.object = object_expr
        self.field = field
        self.chain_ends_in_call = False

    def description(self) -> str:
        return (f"FieldAccessNode @{self.line}:{self.column} field={self.field} "
                f"chain_ends_in_call={str(self.chain_ends_in_call).lower()}")

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("object", (self.object,)),)


class MemberCallNode(ExprNode):
    def __init__(self, token: Token, object_expr: ExprNode, member: str,
                 arguments: list[ExprNode]) -> None:
        super().__init__(token)
        self.object = object_expr
        self.member = member
        self.arguments = arguments
        self.chain_ends_in_call = True

    def description(self) -> str:
        return (f"MemberCallNode @{self.line}:{self.column} member={self.member} "
                f"arguments={len(self.arguments)} "
                f"chain_ends_in_call={str(self.chain_ends_in_call).lower()}")

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("object", (self.object,)), ("arguments", self.arguments))


class ObjectLiteralNode(ExprNode):
    def __init__(self, token: Token, type_name: str,
                 fields: list[tuple[str, ExprNode]]) -> None:
        super().__init__(token)
        self.type_name = type_name
        self.fields = fields

    def description(self) -> str:
        names = ",".join(name for name, _ in self.fields)
        return f"ObjectLiteralNode @{self.line}:{self.column} type={self.type_name} fields={names}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return tuple((f"field {name}", (value,)) for name, value in self.fields)


class FunctionDeclNode(StmtNode):
    def __init__(self, token: Token, name: str, parameters: list[tuple[str, str, Token]],
                 result_type: str, body: list[StmtNode], owner: Optional[str] = None) -> None:
        super().__init__(token)
        self.name = name
        self.parameters = parameters
        self.result_type = result_type
        self.body = body
        self.owner = owner

    def description(self) -> str:
        params = ",".join(f"{name}:{type_name}" for name, type_name, _ in self.parameters)
        owner = f" owner={self.owner}" if self.owner else ""
        return (f"FunctionDeclNode @{self.line}:{self.column} name={self.name} "
                f"parameters=({params}) result={self.result_type}{owner}")

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("body", self.body),)


class FieldDeclNode(ASTNode):
    def __init__(self, token: Token, name: str, type_name: str, mutable: bool) -> None:
        super().__init__(token)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable

    def description(self) -> str:
        return (f"FieldDeclNode @{self.line}:{self.column} name={self.name} "
                f"type={self.type_name} mutable={str(self.mutable).lower()}")


class StructDeclNode(StmtNode):
    def __init__(self, token: Token, name: str, fields: list[FieldDeclNode],
                 methods: list[FunctionDeclNode]) -> None:
        super().__init__(token)
        self.name = name
        self.fields = fields
        self.methods = methods

    def description(self) -> str:
        return f"StructDeclNode @{self.line}:{self.column} name={self.name}"

    def child_groups(self) -> Sequence[tuple[str, Sequence[ASTNode]]]:
        return (("fields", self.fields), ("methods", self.methods))
