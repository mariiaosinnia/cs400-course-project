"""Conservative tree optimizer used by the Stage 3 --optimize flag."""
from __future__ import annotations

import copy

from ast_nodes import (
    AssignNode, BinaryExprNode, CallNode, ExprNode, ExprStmtNode,
    FieldAccessNode, ForNode, FunctionDeclNode, IdentifierNode, IfNode,
    MemberCallNode, ObjectLiteralNode, OverflowCheckNode, ProgramNode,
    ReturnNode, StmtNode, VarDeclNode, WhileNode,
)
from lexer import Token


def _pure(expression: ExprNode) -> bool:
    if isinstance(expression, (CallNode, MemberCallNode)):
        return False
    if isinstance(expression, IdentifierNode):
        return True
    if isinstance(expression, FieldAccessNode):
        return _pure(expression.object)
    if isinstance(expression, OverflowCheckNode):
        return False
    if isinstance(expression, BinaryExprNode):
        return _pure(expression.left) and _pure(expression.right)
    if isinstance(expression, ObjectLiteralNode):
        return all(_pure(value) for _, value in expression.fields)
    return True


def _read_count(node: object, name: str, assignment_target: bool = False) -> int:
    if isinstance(node, IdentifierNode):
        return int(not assignment_target and node.name == name)
    if isinstance(node, AssignNode):
        return (_read_count(node.target, name, True)
                + _read_count(node.value, name))
    if isinstance(node, VarDeclNode):
        return _read_count(node.initializer, name)
    if isinstance(node, CallNode):
        return sum(_read_count(argument, name) for argument in node.arguments)
    if isinstance(node, (IfNode, WhileNode)):
        bodies = [node.then_body] if isinstance(node, IfNode) else [node.body]
        if isinstance(node, IfNode):
            bodies.append(node.else_body or [])
        return (_read_count(node.condition, name)
                + sum(_read_count(statement, name) for body in bodies for statement in body))
    if isinstance(node, ForNode):
        return (_read_count(node.start, name) + _read_count(node.end, name)
                + sum(_read_count(statement, name) for statement in node.body))
    if isinstance(node, ReturnNode):
        return _read_count(node.value, name)
    if isinstance(node, ExprStmtNode):
        return _read_count(node.expression, name)
    if isinstance(node, BinaryExprNode):
        return _read_count(node.left, name) + _read_count(node.right, name)
    if isinstance(node, FieldAccessNode):
        return _read_count(node.object, name)
    if isinstance(node, MemberCallNode):
        return (_read_count(node.object, name)
                + sum(_read_count(argument, name) for argument in node.arguments))
    if isinstance(node, ObjectLiteralNode):
        return sum(_read_count(value, name) for _, value in node.fields)
    if isinstance(node, OverflowCheckNode):
        return _read_count(node.expression, name)
    if isinstance(node, FunctionDeclNode):
        return sum(_read_count(statement, name) for statement in node.body)
    if isinstance(node, ProgramNode):
        return sum(_read_count(statement, name) for statement in node.statements)
    return 0


def _call_count(node: object, name: str) -> int:
    if isinstance(node, CallNode):
        return int(node.callee == name) + sum(_call_count(a, name) for a in node.arguments)
    if isinstance(node, (AssignNode, VarDeclNode)):
        return _call_count(node.value if isinstance(node, AssignNode) else node.initializer, name)
    if isinstance(node, (IfNode, WhileNode)):
        bodies = [node.then_body] if isinstance(node, IfNode) else [node.body]
        if isinstance(node, IfNode):
            bodies.append(node.else_body or [])
        return _call_count(node.condition, name) + sum(
            _call_count(s, name) for body in bodies for s in body
        )
    if isinstance(node, ForNode):
        return (_call_count(node.start, name) + _call_count(node.end, name)
                + sum(_call_count(s, name) for s in node.body))
    if isinstance(node, ReturnNode):
        return _call_count(node.value, name)
    if isinstance(node, ExprStmtNode):
        return _call_count(node.expression, name)
    if isinstance(node, BinaryExprNode):
        return _call_count(node.left, name) + _call_count(node.right, name)
    if isinstance(node, FieldAccessNode):
        return _call_count(node.object, name)
    if isinstance(node, MemberCallNode):
        return _call_count(node.object, name) + sum(_call_count(a, name) for a in node.arguments)
    if isinstance(node, ObjectLiteralNode):
        return sum(_call_count(v, name) for _, v in node.fields)
    if isinstance(node, OverflowCheckNode):
        return _call_count(node.expression, name)
    if isinstance(node, FunctionDeclNode):
        return sum(_call_count(s, name) for s in node.body)
    if isinstance(node, ProgramNode):
        return sum(_call_count(s, name) for s in node.statements)
    return 0


def _substitute(expression: ExprNode, values: dict[str, ExprNode]) -> ExprNode:
    if isinstance(expression, IdentifierNode) and expression.name in values:
        return copy.deepcopy(values[expression.name])
    if isinstance(expression, BinaryExprNode):
        expression.left = _substitute(expression.left, values)
        expression.right = _substitute(expression.right, values)
    elif isinstance(expression, (CallNode, MemberCallNode)):
        if isinstance(expression, MemberCallNode):
            expression.object = _substitute(expression.object, values)
        expression.arguments = [_substitute(a, values) for a in expression.arguments]
    elif isinstance(expression, FieldAccessNode):
        expression.object = _substitute(expression.object, values)
    elif isinstance(expression, ObjectLiteralNode):
        expression.fields = [(n, _substitute(v, values)) for n, v in expression.fields]
    elif isinstance(expression, OverflowCheckNode):
        expression.expression = _substitute(expression.expression, values)
    return expression


def _transform_expr(expression: ExprNode, inline: dict[str, FunctionDeclNode]) -> ExprNode:
    if isinstance(expression, CallNode):
        expression.arguments = [_transform_expr(a, inline) for a in expression.arguments]
    elif isinstance(expression, BinaryExprNode):
        expression.left = _transform_expr(expression.left, inline)
        expression.right = _transform_expr(expression.right, inline)
    elif isinstance(expression, MemberCallNode):
        expression.object = _transform_expr(expression.object, inline)
        expression.arguments = [_transform_expr(a, inline) for a in expression.arguments]
    elif isinstance(expression, FieldAccessNode):
        expression.object = _transform_expr(expression.object, inline)
    elif isinstance(expression, ObjectLiteralNode):
        expression.fields = [(n, _transform_expr(v, inline)) for n, v in expression.fields]
    elif isinstance(expression, OverflowCheckNode):
        expression.expression = _transform_expr(expression.expression, inline)
    return expression


def _inline_direct_call(expression: ExprNode,
                        inline: dict[str, FunctionDeclNode],
                        serial: list[int]) -> tuple[list[StmtNode], ExprNode]:
    if not isinstance(expression, CallNode) or expression.callee not in inline:
        return [], _transform_expr(expression, {})
    function = inline[expression.callee]
    if not all(_pure(argument) for argument in expression.arguments):
        return [], expression
    bindings: list[StmtNode] = []
    values: dict[str, ExprNode] = {}
    serial[0] += 1
    for parameter, argument in zip(function.parameters, expression.arguments):
        parameter_name, parameter_type, _ = parameter
        local_name = f"__inline_{function.name}_{serial[0]}_{parameter_name}"
        token = Token("IDENT", local_name, expression.line, expression.column)
        bindings.append(VarDeclNode(
            token, "immutable", parameter_type, local_name, argument
        ))
        values[parameter_name] = IdentifierNode(token)
    return bindings, _substitute(copy.deepcopy(function.body[0].value), values)


def _transform_statements(statements: list[StmtNode],
                          inline: dict[str, FunctionDeclNode],
                          serial: list[int]) -> list[StmtNode]:
    result: list[StmtNode] = []
    for statement in statements:
        if isinstance(statement, VarDeclNode):
            bindings, replacement = _inline_direct_call(statement.initializer, inline, serial)
            result.extend(bindings)
            statement.initializer = replacement if bindings else _transform_expr(
                statement.initializer, inline
            )
        elif isinstance(statement, AssignNode):
            statement.target = _transform_expr(statement.target, inline)
            statement.value = _transform_expr(statement.value, inline)
        elif isinstance(statement, ExprStmtNode):
            if (isinstance(statement.expression, CallNode)
                    and statement.expression.callee == "print"
                    and statement.expression.arguments):
                bindings, replacement = _inline_direct_call(
                    statement.expression.arguments[0], inline, serial
                )
                result.extend(bindings)
                statement.expression.arguments[0] = replacement if bindings else _transform_expr(
                    statement.expression.arguments[0], inline
                )
            else:
                statement.expression = _transform_expr(statement.expression, inline)
        elif isinstance(statement, ReturnNode):
            statement.value = _transform_expr(statement.value, inline)
        elif isinstance(statement, IfNode):
            statement.condition = _transform_expr(statement.condition, inline)
            statement.then_body = _transform_statements(statement.then_body, inline, serial)
            if statement.else_body is not None:
                statement.else_body = _transform_statements(statement.else_body, inline, serial)
        elif isinstance(statement, WhileNode):
            statement.condition = _transform_expr(statement.condition, inline)
            statement.body = _transform_statements(statement.body, inline, serial)
        elif isinstance(statement, ForNode):
            statement.start = _transform_expr(statement.start, inline)
            statement.end = _transform_expr(statement.end, inline)
            statement.body = _transform_statements(statement.body, inline, serial)
        elif isinstance(statement, FunctionDeclNode):
            statement.body = _transform_statements(statement.body, inline, serial)
        result.append(statement)
    return result


def _remove_dead_declarations(statements: list[StmtNode]) -> tuple[list[StmtNode], bool]:
    changed = False
    for statement in statements:
        if isinstance(statement, IfNode):
            statement.then_body, a = _remove_dead_declarations(statement.then_body)
            changed |= a
            if statement.else_body is not None:
                statement.else_body, a = _remove_dead_declarations(statement.else_body)
                changed |= a
        elif isinstance(statement, (WhileNode, ForNode)):
            statement.body, a = _remove_dead_declarations(statement.body)
            changed |= a
        elif isinstance(statement, FunctionDeclNode):
            statement.body, a = _remove_dead_declarations(statement.body)
            changed |= a
    removed_names: set[str] = set()
    result: list[StmtNode] = []
    for index, statement in enumerate(statements):
        if isinstance(statement, VarDeclNode):
            later = statements[index + 1:]
            reads = sum(_read_count(other, statement.name) for other in later)
            writes = [other for other in later
                      if isinstance(other, AssignNode)
                      and isinstance(other.target, IdentifierNode)
                      and other.target.name == statement.name]
            if reads == 0 and _pure(statement.initializer) and all(_pure(w.value) for w in writes):
                removed_names.add(statement.name)
                changed = True
                continue
        if (isinstance(statement, AssignNode)
                and isinstance(statement.target, IdentifierNode)
                and statement.target.name in removed_names
                and _pure(statement.value)):
            changed = True
            continue
        result.append(statement)
    return result, changed


def optimize(program: ProgramNode) -> ProgramNode:
    for _ in range(32):
        before = repr(program.format_tree())
        functions = {
            statement.name: statement for statement in program.statements
            if isinstance(statement, FunctionDeclNode)
        }
        inline = {
            name: function for name, function in functions.items()
            if _call_count(program, name) == 1
            and len(function.body) == 1
            and isinstance(function.body[0], ReturnNode)
            and _call_count(function.body[0], name) == 0
        }
        program.statements = _transform_statements(program.statements, inline, [0])
        called = {name for name in functions if _call_count(program, name)}
        program.statements = [
            statement for statement in program.statements
            if not (isinstance(statement, FunctionDeclNode)
                    and statement.name not in called)
        ]
        program.statements, changed = _remove_dead_declarations(program.statements)
        if not changed and repr(program.format_tree()) == before:
            break
    return program
