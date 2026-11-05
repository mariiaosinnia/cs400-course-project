from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ast_nodes import (
    AssignNode, BinaryExprNode, BoolNode, CallNode, ExprNode, ExprStmtNode,
    FieldAccessNode, FieldDeclNode, ForNode, FunctionDeclNode, IdentifierNode, IfNode,
    MemberCallNode, NumberNode, ObjectLiteralNode, OverflowCheckNode, ProgramNode,
    ReturnNode, StmtNode, StringNode, StructDeclNode, VarDeclNode, WhileNode,
)
from lexer import CompilerError, Token


@dataclass
class VariableInfo:
    type_name: str
    mutable: bool
    token: Token
    global_storage: bool = False


@dataclass
class StructInfo:
    node: StructDeclNode
    fields: dict[str, tuple[FieldDeclNode, int]]
    methods: dict[str, FunctionDeclNode]


class SemanticAnalyzer:
    def __init__(self, program: ProgramNode) -> None:
        self.program = program
        self.structs: dict[str, StructInfo] = {}
        self.functions: dict[str, FunctionDeclNode] = {}
        self.globals: dict[str, VariableInfo] = {}
        self.global_nodes: dict[int, str] = {}
        self.global_positions: dict[str, int] = {}
        self.function_positions: dict[int, int] = {}
        self.scopes: list[dict[str, VariableInfo]] = []
        self.visible_globals: set[str] = set()
        self.current_function: Optional[FunctionDeclNode] = None
        self.current_struct: Optional[StructInfo] = None
        self.expression_types: dict[int, str] = {}
        self.mutating_methods: set[int] = set()

    @staticmethod
    def fail(node: object, message: str) -> None:
        raise CompilerError(getattr(node, "line", 1), getattr(node, "column", 1), message)

    def analyze(self) -> "SemanticAnalyzer":
        self._collect_declarations()
        self._check_top_level()
        for info in self.structs.values():
            for method in info.node.methods:
                self._check_function(method, info)
        for function in self.functions.values():
            self._check_function(function, None)
        return self

    def _collect_declarations(self) -> None:
        for position, statement in enumerate(self.program.statements):
            if isinstance(statement, StructDeclNode):
                if statement.name == "string":
                    self.fail(statement, "'string' is a built-in type name")
                if statement.name in self.structs:
                    self.fail(statement, f"structure '{statement.name}' is already declared")
                if not statement.fields:
                    self.fail(statement, f"structure '{statement.name}' must declare at least one field")
                fields: dict[str, tuple[FieldDeclNode, int]] = {}
                methods: dict[str, FunctionDeclNode] = {}
                for index, field in enumerate(statement.fields):
                    if field.name == "self":
                        self.fail(field, "'self' is reserved inside member functions")
                    if field.name in fields or field.name in methods:
                        self.fail(field, f"duplicate member '{field.name}' in structure '{statement.name}'")
                    fields[field.name] = (field, index)
                for method in statement.methods:
                    if method.name == "self":
                        self.fail(method, "'self' is reserved inside member functions")
                    if method.name in methods or method.name in fields:
                        self.fail(method, f"duplicate member '{method.name}' in structure '{statement.name}'")
                    methods[method.name] = method
                self.structs[statement.name] = StructInfo(statement, fields, methods)
                for method in statement.methods:
                    self.function_positions[id(method)] = position
            elif isinstance(statement, FunctionDeclNode):
                if statement.name in ("read", "print"):
                    self.fail(statement, f"function name '{statement.name}' is reserved for a built-in")
                if statement.name in self.functions:
                    self.fail(statement, f"function '{statement.name}' is already declared")
                self.functions[statement.name] = statement
                self.function_positions[id(statement)] = position

        for name, info in self.structs.items():
            for field, _ in info.fields.values():
                self._require_type(field.type_name, field)
            for method in info.methods.values():
                self._check_signature(method)
        for function in self.functions.values():
            self._check_signature(function)
        self._check_structure_cycles()
        self._compute_mutation_flags()

        for position, statement in enumerate(self.program.statements):
            if isinstance(statement, VarDeclNode):
                if statement.name in self.globals:
                    self.fail(statement, f"'{statement.name}' is already declared in this scope")
                if statement.name in self.functions:
                    self.fail(statement, f"'{statement.name}' conflicts with a function")
                self.globals[statement.name] = VariableInfo(
                    statement.type_name, statement.storage == "mutable",
                    Token("IDENT", statement.name, statement.line, statement.column), True,
                )
                self.global_nodes[id(statement)] = statement.name
                self.global_positions[statement.name] = position

    def _check_signature(self, function: FunctionDeclNode) -> None:
        self._require_type(function.result_type, function)
        names: set[str] = set()
        for name, type_name, token in function.parameters:
            self._require_type(type_name, token)
            if function.owner is not None and name == "self":
                self.fail(token, "'self' is reserved inside member functions")
            if name in names:
                self.fail(token, f"duplicate parameter '{name}'")
            names.add(name)

    def _check_structure_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                self.fail(self.structs[name].node, f"structure '{name}' contains a recursive by-value field")
            if name in visited:
                return
            visiting.add(name)
            for field, _ in self.structs[name].fields.values():
                if field.type_name in self.structs:
                    visit(field.type_name)
            visiting.remove(name)
            visited.add(name)

        for name in self.structs:
            visit(name)

    def _compute_mutation_flags(self) -> None:
        for info in self.structs.values():
            for method in info.node.methods:
                if self._writes_receiver_field(method, info):
                    self.mutating_methods.add(id(method))
        changed = True
        while changed:
            changed = False
            for info in self.structs.values():
                for method in info.node.methods:
                    if id(method) not in self.mutating_methods and self._calls_mutating_receiver_method(method, info):
                        self.mutating_methods.add(id(method))
                        changed = True

    def _writes_receiver_field(self, function: FunctionDeclNode,
                               info: StructInfo) -> bool:
        def expression_writes_receiver(expression: ExprNode,
                                       locals_in_scope: set[str]) -> bool:
            if isinstance(expression, IdentifierNode):
                return expression.name == "self" or (
                    expression.name in info.fields and expression.name not in locals_in_scope
                )
            if isinstance(expression, FieldAccessNode):
                return expression_writes_receiver(expression.object, locals_in_scope)
            return False

        def expression_has_effect(expression: ExprNode,
                                  locals_in_scope: set[str]) -> bool:
            if isinstance(expression, MemberCallNode):
                receiver_type = self._receiver_field_type(
                    expression.object, info, locals_in_scope
                )
                if receiver_type is not None:
                    method = self.structs[receiver_type].methods.get(expression.member)
                    if method is not None and id(method) in self.mutating_methods:
                        return True
            elif isinstance(expression, CallNode):
                if (expression.callee not in locals_in_scope
                        and expression.callee not in info.fields):
                    method = info.methods.get(expression.callee)
                    if method is not None and id(method) in self.mutating_methods:
                        return True
            return any(
                expression_has_effect(child, locals_in_scope)
                for _, children in expression.child_groups()
                for child in children if isinstance(child, ExprNode)
            )

        def block_writes(statements: list[StmtNode],
                         outer_locals: set[str]) -> bool:
            locals_in_scope = set(outer_locals)
            for statement in statements:
                if isinstance(statement, AssignNode):
                    target = statement.target
                    if isinstance(target, IdentifierNode) and (
                        target.name == "self" or
                        (target.name in info.fields and target.name not in locals_in_scope)
                    ):
                        return True
                    if isinstance(target, FieldAccessNode) and expression_writes_receiver(
                            target.object, locals_in_scope):
                        return True
                    if expression_has_effect(statement.value, locals_in_scope):
                        return True
                elif isinstance(statement, VarDeclNode):
                    if expression_has_effect(statement.initializer, locals_in_scope):
                        return True
                    locals_in_scope.add(statement.name)
                elif isinstance(statement, ReturnNode):
                    if expression_has_effect(statement.value, locals_in_scope):
                        return True
                elif isinstance(statement, ExprStmtNode):
                    if expression_has_effect(statement.expression, locals_in_scope):
                        return True
                if isinstance(statement, IfNode) and (
                    expression_has_effect(statement.condition, locals_in_scope)
                    or block_writes(statement.then_body, locals_in_scope)
                    or (statement.else_body is not None
                        and block_writes(statement.else_body, locals_in_scope))
                ):
                    return True
                if isinstance(statement, WhileNode) and (
                    expression_has_effect(statement.condition, locals_in_scope)
                    or block_writes(statement.body, locals_in_scope)
                ):
                    return True
                if isinstance(statement, ForNode) and (
                    expression_has_effect(statement.start, locals_in_scope)
                    or expression_has_effect(statement.end, locals_in_scope)
                    or block_writes(statement.body, locals_in_scope | {statement.name})
                ):
                    return True
            return False

        parameter_names = {name for name, _, _ in function.parameters}
        return block_writes(function.body, parameter_names)

    def _receiver_field_type(self, expression: ExprNode, owner: StructInfo,
                             locals_in_scope: set[str]) -> Optional[str]:
        if isinstance(expression, IdentifierNode):
            if expression.name == "self":
                return owner.node.name
            if expression.name in locals_in_scope:
                return None
            field = owner.fields.get(expression.name)
            return field[0].type_name if field is not None else None
        if isinstance(expression, FieldAccessNode):
            parent_type = self._receiver_field_type(
                expression.object, owner, locals_in_scope
            )
            if parent_type is None:
                return None
            field = self.structs[parent_type].fields.get(expression.field)
            return field[0].type_name if field is not None else None
        return None

    def _calls_mutating_receiver_method(self, function: FunctionDeclNode,
                                        owner: StructInfo) -> bool:
        def expression_calls(expression: ExprNode,
                             locals_in_scope: set[str]) -> bool:
            if isinstance(expression, CallNode):
                method = (
                    owner.methods.get(expression.callee)
                    if expression.callee not in locals_in_scope
                    and expression.callee not in owner.fields
                    else None
                )
                if method is not None and id(method) in self.mutating_methods:
                    return True
            if isinstance(expression, MemberCallNode):
                target_type = self._receiver_field_type(
                    expression.object, owner, locals_in_scope
                )
                if target_type in self.structs:
                    method = self.structs[target_type].methods.get(expression.member)
                    if method is not None and id(method) in self.mutating_methods:
                        return True
            return any(
                expression_calls(child, locals_in_scope)
                for _, children in expression.child_groups()
                for child in children if isinstance(child, ExprNode)
            )

        def block_calls(statements: list[StmtNode],
                        outer_locals: set[str]) -> bool:
            locals_in_scope = set(outer_locals)
            for statement in statements:
                if isinstance(statement, AssignNode):
                    has_effect = expression_calls(statement.value, locals_in_scope)
                elif isinstance(statement, VarDeclNode):
                    has_effect = expression_calls(statement.initializer, locals_in_scope)
                    locals_in_scope.add(statement.name)
                elif isinstance(statement, ReturnNode):
                    has_effect = expression_calls(statement.value, locals_in_scope)
                elif isinstance(statement, ExprStmtNode):
                    has_effect = expression_calls(statement.expression, locals_in_scope)
                elif isinstance(statement, IfNode):
                    has_effect = (
                        expression_calls(statement.condition, locals_in_scope)
                        or block_calls(statement.then_body, locals_in_scope)
                        or (statement.else_body is not None
                            and block_calls(statement.else_body, locals_in_scope))
                    )
                elif isinstance(statement, WhileNode):
                    has_effect = (
                        expression_calls(statement.condition, locals_in_scope)
                        or block_calls(statement.body, locals_in_scope)
                    )
                elif isinstance(statement, ForNode):
                    has_effect = (
                        expression_calls(statement.start, locals_in_scope)
                        or expression_calls(statement.end, locals_in_scope)
                        or block_calls(statement.body, locals_in_scope | {statement.name})
                    )
                else:
                    has_effect = False
                if has_effect:
                    return True
            return False

        parameter_names = {name for name, _, _ in function.parameters}
        return block_calls(function.body, parameter_names)

    def _require_type(self, type_name: str, node: object) -> None:
        if type_name not in ("i32", "i64", "bool", "string") and type_name not in self.structs:
            self.fail(node, f"unknown type '{type_name}'")

    def _check_top_level(self) -> None:
        self.scopes = [{}]
        for statement in self.program.statements:
            if isinstance(statement, (StructDeclNode, FunctionDeclNode)):
                continue
            self._check_statement(statement, top_level=True)

    def _check_function(self, function: FunctionDeclNode,
                        struct: Optional[StructInfo]) -> None:
        self.current_function = function
        self.current_struct = struct
        previous_visible_globals = self.visible_globals
        function_position = self.function_positions[id(function)]
        self.visible_globals = {
            name for name, position in self.global_positions.items()
            if position < function_position
        }
        self.scopes = [{}]
        if struct is not None:
            self.scopes[0]["self"] = VariableInfo(struct.node.name, False,
                                                  Token("IDENT", "self", function.line, function.column))
        for name, type_name, token in function.parameters:
            self.scopes[0][name] = VariableInfo(type_name, False, token)
        self.scopes.append({})
        try:
            for statement in function.body:
                self._check_statement(statement)
            if not self._always_returns(function.body):
                self.fail(function, f"function '{function.name}' must return {function.result_type} on every path")
        finally:
            self.scopes.pop()
            self.current_function = None
            self.current_struct = None
            self.visible_globals = previous_visible_globals

    @staticmethod
    def _always_returns(statements: list[StmtNode]) -> bool:
        for statement in statements:
            if isinstance(statement, ReturnNode):
                return True
            if isinstance(statement, IfNode) and statement.else_body is not None:
                if (SemanticAnalyzer._always_returns(statement.then_body)
                        and SemanticAnalyzer._always_returns(statement.else_body)):
                    return True
        return False

    def _check_statement(self, statement: StmtNode, top_level: bool = False) -> None:
        if isinstance(statement, VarDeclNode):
            scope = self.scopes[-1]
            if self.current_struct is not None and statement.name == "self":
                self.fail(statement, "'self' is reserved inside member functions")
            if statement.name in scope:
                self.fail(statement, f"'{statement.name}' is already declared in this scope")
            self._require_type(statement.type_name, statement)
            value_type = self._check_expr(statement.initializer, statement.type_name)
            self._expect(value_type, statement.type_name, statement.initializer)
            variable = self.globals[statement.name] if top_level else VariableInfo(
                statement.type_name, statement.storage == "mutable",
                Token("IDENT", statement.name, statement.line, statement.column),
            )
            scope[statement.name] = variable
            if top_level:
                self.visible_globals.add(statement.name)
        elif isinstance(statement, AssignNode):
            target_type, mutable = self._check_lvalue(statement.target)
            if not mutable:
                self.fail(statement, "cannot assign to an immutable variable or field")
            value_type = self._check_expr(statement.value, target_type)
            self._expect(value_type, target_type, statement.value)
        elif isinstance(statement, IfNode):
            condition_type = self._check_expr(statement.condition)
            self._expect(condition_type, "bool", statement.condition)
            self._check_scoped_block(statement.then_body)
            if statement.else_body is not None:
                self._check_scoped_block(statement.else_body)
        elif isinstance(statement, WhileNode):
            condition_type = self._check_expr(statement.condition)
            if condition_type not in ("i32", "i64"):
                self.fail(statement.condition, "while condition must be an integer")
            self._check_scoped_block(statement.body)
        elif isinstance(statement, ForNode):
            start_type = self._check_expr(statement.start)
            end_type = self._check_expr(statement.end)
            if start_type not in ("i32", "i64") or end_type not in ("i32", "i64") \
                    or end_type != start_type:
                self.fail(statement, "for bounds must have the same integer type")
            self.scopes.append({statement.name: VariableInfo(
                start_type, False, Token("IDENT", statement.name, statement.line, statement.column))})
            try:
                for child in statement.body:
                    self._check_statement(child)
            finally:
                self.scopes.pop()
        elif isinstance(statement, ReturnNode):
            if self.current_function is None:
                self.fail(statement, "return is only valid inside a function")
            result = self._check_expr(statement.value, self.current_function.result_type)
            self._expect(result, self.current_function.result_type, statement.value)
        elif isinstance(statement, ExprStmtNode):
            value_type = self._check_expr(statement.expression)
            if not isinstance(statement.expression, (CallNode, MemberCallNode)) or value_type != "void":
                self.fail(statement, "only print calls may be used as standalone expressions")
        else:
            self.fail(statement, "invalid statement")

    def _check_scoped_block(self, statements: list[StmtNode]) -> None:
        self.scopes.append({})
        try:
            for statement in statements:
                self._check_statement(statement)
        finally:
            self.scopes.pop()

    def _expect(self, actual: str, expected: str, node: object) -> None:
        if actual != expected:
            self.fail(node, f"expected {expected}, found {actual}")

    def _lookup_variable(self, name: str, node: object) -> VariableInfo:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        if self.current_struct is not None and name in self.current_struct.fields:
            field, _ = self.current_struct.fields[name]
            return VariableInfo(field.type_name, field.mutable,
                                Token("IDENT", name, field.line, field.column))
        if name in self.globals and name in self.visible_globals:
            return self.globals[name]
        self.fail(node, f"unknown variable '{name}'")

    def _check_expr(self, expression: ExprNode, expected: Optional[str] = None) -> str:
        if isinstance(expression, NumberNode):
            number = int(expression.value)
            if expected in ("i32", "i64"):
                if number > {"i32": 2**31 - 1, "i64": 2**63 - 1}[expected]:
                    self.fail(expression, f"integer literal is out of range for {expected}")
                result = expected
            elif number <= 2**31 - 1:
                result = "i32"
            elif number <= 2**63 - 1:
                result = "i64"
            else:
                self.fail(expression, "integer literal is out of range for i64")
        elif isinstance(expression, BoolNode):
            result = "bool"
        elif isinstance(expression, StringNode):
            result = "string"
        elif isinstance(expression, IdentifierNode):
            result = self._lookup_variable(expression.name, expression).type_name
        elif isinstance(expression, BinaryExprNode):
            is_comparison = expression.operator in ("⚖", "⚖️", "✂", "✂️")
            numeric_context = expected if not is_comparison and expected in ("i32", "i64") else None
            if (numeric_context is None
                    and self._is_integer_literal_expression(expression.left)):
                right = self._check_expr(expression.right)
                left = self._check_expr(
                    expression.left, right if right in ("i32", "i64") else None
                )
            else:
                left = self._check_expr(expression.left, numeric_context)
                right = self._check_expr(expression.right, left)
            self._expect(right, left, expression.right)
            if is_comparison:
                if left not in ("i32", "i64", "bool"):
                    self.fail(expression, "equality is only defined for scalar values")
                result = "bool"
            else:
                if left not in ("i32", "i64"):
                    self.fail(expression, "arithmetic operands must be integers")
                result = left
        elif isinstance(expression, OverflowCheckNode):
            result = self._check_expr(expression.expression, expected)
            if result not in ("i32", "i64"):
                self.fail(expression, "overflow check requires an integer expression")
        elif isinstance(expression, CallNode):
            if expression.callee == "read":
                if expression.arguments:
                    self.fail(expression, "read expects 0 arguments")
                result = "i32"
            elif expression.callee == "print":
                if len(expression.arguments) != 1:
                    self.fail(expression, "print expects 1 argument")
                printed_type = self._check_expr(expression.arguments[0])
                if printed_type not in ("i32", "i64", "bool", "string") and printed_type not in self.structs:
                    self.fail(expression.arguments[0], f"cannot print value of type '{printed_type}'")
                result = "void"
            else:
                if any(expression.callee in scope for scope in self.scopes):
                    self.fail(expression, f"'{expression.callee}' is not callable")
                if self.current_struct is not None and expression.callee in self.current_struct.fields:
                    self.fail(expression, f"'{expression.callee}' is not callable")
                function = (self.current_struct.methods.get(expression.callee)
                            if self.current_struct is not None else None)
                if function is None:
                    function = self.functions.get(expression.callee)
                if function is None:
                    self.fail(expression, f"unknown function '{expression.callee}'")
                self._check_arguments(expression.arguments, function.parameters, expression)
                result = function.result_type
        elif isinstance(expression, FieldAccessNode):
            object_type = self._check_expr(expression.object)
            info = self.structs.get(object_type)
            if info is None:
                self.fail(expression, f"type '{object_type}' has no fields")
            if expression.field not in info.fields:
                self.fail(expression, f"unknown field '{expression.field}' in '{object_type}'")
            result = info.fields[expression.field][0].type_name
        elif isinstance(expression, MemberCallNode):
            object_type = self._check_expr(expression.object)
            info = self.structs.get(object_type)
            if info is None or expression.member not in info.methods:
                self.fail(expression, f"unknown member function '{expression.member}' in '{object_type}'")
            function = info.methods[expression.member]
            self._check_arguments(expression.arguments, function.parameters, expression)
            if self._method_mutates(function, info) and not self._is_mutable_place(expression.object):
                self.fail(expression, "mutating member function requires a mutable object")
            result = function.result_type
        elif isinstance(expression, ObjectLiteralNode):
            info = self.structs.get(expression.type_name)
            if info is None:
                self.fail(expression, f"unknown structure '{expression.type_name}'")
            supplied: set[str] = set()
            for name, value in expression.fields:
                if name in supplied:
                    self.fail(value, f"field '{name}' is initialized more than once")
                supplied.add(name)
                field_info = info.fields.get(name)
                if field_info is None:
                    self.fail(value, f"unknown field '{name}' in '{expression.type_name}'")
                value_type = self._check_expr(value, field_info[0].type_name)
                self._expect(value_type, field_info[0].type_name, value)
            missing = set(info.fields) - supplied
            if missing:
                self.fail(expression, f"missing field '{sorted(missing)[0]}' in '{expression.type_name}'")
            result = expression.type_name
        else:
            self.fail(expression, "invalid expression")
        if expected is not None and result != expected:
            self.fail(expression, f"expected {expected}, found {result}")
        self.expression_types[id(expression)] = result
        return result

    @staticmethod
    def _is_integer_literal_expression(expression: ExprNode) -> bool:
        if isinstance(expression, NumberNode):
            return True
        if isinstance(expression, OverflowCheckNode):
            return SemanticAnalyzer._is_integer_literal_expression(expression.expression)
        if isinstance(expression, BinaryExprNode):
            return (
                expression.operator in ("+", "-", "*", "/")
                and SemanticAnalyzer._is_integer_literal_expression(expression.left)
                and SemanticAnalyzer._is_integer_literal_expression(expression.right)
            )
        return False

    def _check_arguments(self, arguments: list[ExprNode],
                         parameters: list[tuple[str, str, Token]], node: object) -> None:
        if len(arguments) != len(parameters):
            self.fail(node, f"expected {len(parameters)} arguments, found {len(arguments)}")
        for argument, (_, expected, _) in zip(arguments, parameters):
            actual = self._check_expr(argument, expected)
            self._expect(actual, expected, argument)

    def _check_lvalue(self, target: ExprNode) -> tuple[str, bool]:
        if isinstance(target, IdentifierNode):
            variable = self._lookup_variable(target.name, target)
            return variable.type_name, variable.mutable
        if isinstance(target, FieldAccessNode):
            field_type = self._check_expr(target)
            info = self.structs[self._check_expr(target.object)]
            mutable = info.fields[target.field][0].mutable and self._is_mutable_place(target.object)
            return field_type, mutable
        self.fail(target, "assignment target must be a variable or field")

    def _is_mutable_place(self, expression: ExprNode) -> bool:
        if isinstance(expression, IdentifierNode):
            if self.current_struct is not None and expression.name == "self":
                return True
            return self._lookup_variable(expression.name, expression).mutable
        if isinstance(expression, FieldAccessNode):
            object_type = self._check_expr(expression.object)
            info = self.structs[object_type]
            return info.fields[expression.field][0].mutable and self._is_mutable_place(expression.object)
        return False

    def _method_mutates(self, function: FunctionDeclNode, info: StructInfo) -> bool:
        return id(function) in self.mutating_methods


def analyze(program: ProgramNode) -> SemanticAnalyzer:
    return SemanticAnalyzer(program).analyze()
