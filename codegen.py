from __future__ import annotations

from typing import Optional

from llvmlite import ir
from llvmlite import binding

from ast_nodes import (
    AssignNode, BinaryExprNode, BoolNode, CallNode, ExprNode, ExprStmtNode,
    FieldAccessNode, FunctionDeclNode, IdentifierNode, IfNode, MemberCallNode,
    NumberNode, ObjectLiteralNode, OverflowCheckNode, ProgramNode, ReturnNode,
    StmtNode, StringNode, StructDeclNode, VarDeclNode,
)
from semantic import SemanticAnalyzer


class CodeGenerator:
    def __init__(self, program: ProgramNode, analysis: SemanticAnalyzer) -> None:
        self.program = program
        self.analysis = analysis
        self.module = ir.Module(name="sprout")
        self.module.triple = binding.get_default_triple()
        self.i1 = ir.IntType(1)
        self.i8 = ir.IntType(8)
        self.i32 = ir.IntType(32)
        self.i64 = ir.IntType(64)
        self.i8p = self.i8.as_pointer()
        self.void = ir.VoidType()
        self.llvm_types: dict[str, ir.Type] = {
            "i32": self.i32, "i64": self.i64, "bool": self.i1,
            "string": self.i8p,
        }
        self.struct_types: dict[str, ir.IdentifiedStructType] = {}
        self.functions: dict[int, ir.Function] = {}
        self.global_values: dict[str, ir.GlobalVariable] = {}
        self.printf: Optional[ir.Function] = None
        self.scanf: Optional[ir.Function] = None
        self.trap: Optional[ir.Function] = None
        self.builder: Optional[ir.IRBuilder] = None
        self.scopes: list[dict[str, tuple[ir.Value, str]]] = []
        self.current_struct: Optional[str] = None
        self.self_pointer: Optional[ir.Value] = None
        self.string_counter = 0

    def generate(self) -> str:
        for name in self.analysis.structs:
            self.struct_types[name] = self.module.context.get_identified_type(f"sprout.{name}")
            self.llvm_types[name] = self.struct_types[name]
        for name, info in self.analysis.structs.items():
            fields = [self.llvm_types[field.type_name] for field in info.node.fields]
            self.struct_types[name].set_body(*fields)
        vararg_type = ir.FunctionType(self.i32, [self.i8p], var_arg=True)
        self.printf = ir.Function(self.module, vararg_type, name="printf")
        self.scanf = ir.Function(self.module, vararg_type, name="scanf")
        self.trap = ir.Function(self.module, ir.FunctionType(self.void, []), name="llvm.trap")

        for name, info in self.analysis.globals.items():
            global_value = ir.GlobalVariable(self.module, self.llvm_types[info.type_name],
                                             name=f"sprout_global_{name}")
            global_value.initializer = ir.Constant(self.llvm_types[info.type_name], None)
            self.global_values[name] = global_value

        for function in self.analysis.functions.values():
            self._declare_function(function, None)
        for structure, info in self.analysis.structs.items():
            for method in info.node.methods:
                self._declare_function(method, structure)

        for function in self.analysis.functions.values():
            self._emit_function(function, None)
        for structure, info in self.analysis.structs.items():
            for method in info.node.methods:
                self._emit_function(method, structure)
        self._emit_entry()
        return str(self.module)

    def _llvm_function_name(self, function: FunctionDeclNode,
                            owner: Optional[str]) -> str:
        return f"sprout_method_{owner}_{function.name}" if owner else f"sprout_fn_{function.name}"

    def _declare_function(self, function: FunctionDeclNode, owner: Optional[str]) -> None:
        parameters = []
        if owner is not None:
            parameters.append(self.struct_types[owner].as_pointer())
        parameters.extend(self.llvm_types[type_name] for _, type_name, _ in function.parameters)
        function_type = ir.FunctionType(self.llvm_types[function.result_type], parameters)
        llvm_function = ir.Function(
            self.module, function_type, name=self._llvm_function_name(function, owner)
        )
        self.functions[id(function)] = llvm_function

    def _emit_function(self, function: FunctionDeclNode,
                       owner: Optional[str]) -> None:
        llvm_function = self.functions[id(function)]
        block = llvm_function.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        self.scopes = [{}]
        self.current_struct = owner
        arg_index = 0
        self.self_pointer = None
        if owner is not None:
            self.self_pointer = llvm_function.args[0]
            self.self_pointer.name = "self"
            self.scopes[0]["self"] = (self.self_pointer, owner)
            arg_index = 1
        for arg, (name, type_name, _) in zip(
                llvm_function.args[arg_index:], function.parameters):
            arg.name = name
            slot = self.builder.alloca(self.llvm_types[type_name], name=f"{name}.addr")
            self.builder.store(arg, slot)
            self.scopes[0][name] = (slot, type_name)
        for statement in function.body:
            if self.builder.block.is_terminated:
                break
            self._emit_statement(statement)
        if not self.builder.block.is_terminated:
            self.builder.ret(self._zero(self.llvm_types[function.result_type]))
        self.current_struct = None
        self.self_pointer = None

    def _emit_entry(self) -> None:
        function_type = ir.FunctionType(self.i32, [])
        main = ir.Function(self.module, function_type, name="main")
        self.builder = ir.IRBuilder(main.append_basic_block("entry"))
        self.scopes = [{}]
        for statement in self.program.statements:
            if self.builder.block.is_terminated:
                break
            if isinstance(statement, (StructDeclNode, FunctionDeclNode)):
                continue
            self._emit_statement(statement, global_scope=True)
        if not self.builder.block.is_terminated:
            self.builder.ret(ir.Constant(self.i32, 0))

    @staticmethod
    def _zero(llvm_type: ir.Type) -> ir.Constant:
        return ir.Constant(llvm_type, None)

    def _emit_statement(self, statement: StmtNode, global_scope: bool = False) -> None:
        assert self.builder is not None
        if isinstance(statement, VarDeclNode):
            llvm_type = self.llvm_types[statement.type_name]
            if global_scope:
                pointer = self.global_values[statement.name]
            else:
                pointer = self.builder.alloca(llvm_type, name=f"{statement.name}.addr")
                self.scopes[-1][statement.name] = (pointer, statement.type_name)
            value = self._emit_expr(statement.initializer, statement.type_name)
            self.builder.store(value, pointer)
        elif isinstance(statement, AssignNode):
            pointer, type_name = self._emit_place(statement.target)
            value = self._emit_expr(statement.value, type_name)
            self.builder.store(value, pointer)
        elif isinstance(statement, IfNode):
            condition = self._emit_expr(statement.condition, "bool")
            then_block = self.builder.function.append_basic_block("if.then")
            else_block = self.builder.function.append_basic_block("if.else")
            merge_block = self.builder.function.append_basic_block("if.end")
            self.builder.cbranch(condition, then_block, else_block if statement.else_body else merge_block)
            self.builder.position_at_end(then_block)
            self._emit_scoped_block(statement.then_body)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_block)
            if statement.else_body is not None:
                self.builder.position_at_end(else_block)
                self._emit_scoped_block(statement.else_body)
                if not self.builder.block.is_terminated:
                    self.builder.branch(merge_block)
            self.builder.position_at_end(merge_block)
        elif isinstance(statement, ReturnNode):
            self.builder.ret(self._emit_expr(statement.value))
        elif isinstance(statement, ExprStmtNode):
            self._emit_expr(statement.expression)

    def _emit_scoped_block(self, statements: list[StmtNode]) -> None:
        self.scopes.append({})
        try:
            for statement in statements:
                if self.builder is not None and self.builder.block.is_terminated:
                    break
                self._emit_statement(statement)
        finally:
            self.scopes.pop()

    def _emit_expr(self, expression: ExprNode, expected_type: Optional[str] = None,
                   checked: bool = False) -> ir.Value:
        assert self.builder is not None
        type_name = self.analysis.expression_types.get(id(expression), expected_type)
        if isinstance(expression, NumberNode):
            return ir.Constant(self.llvm_types[type_name or "i32"], int(expression.value))
        if isinstance(expression, BoolNode):
            return ir.Constant(self.i1, int(expression.value))
        if isinstance(expression, StringNode):
            return self._string_constant(expression.value)
        if isinstance(expression, IdentifierNode):
            pointer, _ = self._lookup(expression.name)
            return self.builder.load(pointer, name=expression.name)
        if isinstance(expression, FieldAccessNode):
            pointer, _ = self._emit_place(expression)
            return self.builder.load(pointer, name=expression.field)
        if isinstance(expression, ObjectLiteralNode):
            llvm_type = self.struct_types[expression.type_name]
            value: ir.Value = ir.Constant(llvm_type, ir.Undefined)
            fields = self.analysis.structs[expression.type_name].fields
            for name, field_expr in expression.fields:
                index = fields[name][1]
                field_type = fields[name][0].type_name
                field_value = self._emit_expr(field_expr, field_type)
                value = self.builder.insert_value(value, field_value, index)
            return value
        if isinstance(expression, OverflowCheckNode):
            return self._emit_expr(expression.expression, expected_type, checked=True)
        if isinstance(expression, BinaryExprNode):
            left = self._emit_expr(expression.left, checked=checked)
            right = self._emit_expr(expression.right, checked=checked)
            op = expression.operator
            if op in ("+", "-", "*"):
                return self._emit_checked_arithmetic(op, left, right) if checked else {
                    "+": self.builder.add, "-": self.builder.sub, "*": self.builder.mul,
                }[op](left, right)
            if op == "/":
                if checked:
                    self._guard_division(left, right)
                return self.builder.sdiv(left, right)
            if op in ("⚖", "⚖️"):
                return self.builder.icmp_signed("==", left, right) if left.type in (self.i32, self.i64) else self.builder.icmp_unsigned("==", left, right)
            return self.builder.icmp_signed("!=", left, right) if left.type in (self.i32, self.i64) else self.builder.icmp_unsigned("!=", left, right)
        if isinstance(expression, CallNode):
            if expression.callee == "read":
                slot = self.builder.alloca(self.i32, name="read.addr")
                scan_result = self.builder.call(
                    self.scanf, [self._string_constant("%d"), slot]
                )
                read_failed = self.builder.icmp_signed(
                    "!=", scan_result, ir.Constant(self.i32, 1)
                )
                self._guard(read_failed, "input.error")
                return self.builder.load(slot, name="read.value")
            if expression.callee == "print":
                value = self._emit_expr(expression.arguments[0])
                value_type = self.analysis.expression_types[id(expression.arguments[0])]
                self._emit_print(value, value_type)
                return ir.Constant(self.i32, 0)
            method = (
                self.analysis.structs[self.current_struct].methods.get(expression.callee)
                if self.current_struct is not None else None
            )
            if method is not None:
                args = [self.self_pointer]
                args.extend(self._emit_expr(argument, type_name)
                            for argument, (_, type_name, _) in zip(expression.arguments, method.parameters))
                return self.builder.call(self.functions[id(method)], args)
            function = self.analysis.functions[expression.callee]
            args = [self._emit_expr(argument, type_name)
                    for argument, (_, type_name, _) in zip(expression.arguments, function.parameters)]
            return self.builder.call(self.functions[id(function)], args)
        if isinstance(expression, MemberCallNode):
            object_pointer, object_type = self._emit_place(expression.object)
            method = self.analysis.structs[object_type].methods[expression.member]
            args = [object_pointer]
            args.extend(self._emit_expr(argument, type_name)
                        for argument, (_, type_name, _) in zip(expression.arguments, method.parameters))
            return self.builder.call(self.functions[id(method)], args)
        raise TypeError(f"unhandled expression {type(expression).__name__}")

    def _emit_checked_arithmetic(self, operator: str,
                                 left: ir.Value, right: ir.Value) -> ir.Value:
        assert self.builder is not None
        opcode = {"+": "sadd", "-": "ssub", "*": "smul"}[operator]
        result_type = ir.LiteralStructType([left.type, self.i1])
        intrinsic = self.module.globals.get(f"llvm.{opcode}.with.overflow.i{left.type.width}")
        if intrinsic is None:
            intrinsic = ir.Function(
                self.module,
                ir.FunctionType(result_type, [left.type, left.type]),
                name=f"llvm.{opcode}.with.overflow.i{left.type.width}",
            )
        pair = self.builder.call(intrinsic, [left, right])
        value = self.builder.extract_value(pair, 0)
        overflow = self.builder.extract_value(pair, 1)
        self._guard(overflow)
        return value

    def _guard_division(self, left: ir.Value, right: ir.Value) -> None:
        assert self.builder is not None
        is_zero = self.builder.icmp_signed("==", right, ir.Constant(right.type, 0))
        minimum = ir.Constant(left.type, -(2 ** (left.type.width - 1)))
        minus_one = ir.Constant(right.type, -1)
        overflow = self.builder.and_(
            self.builder.icmp_signed("==", left, minimum),
            self.builder.icmp_signed("==", right, minus_one),
        )
        self._guard(self.builder.or_(is_zero, overflow))

    def _guard(self, condition: ir.Value, failure_name: str = "overflow") -> None:
        assert self.builder is not None and self.trap is not None
        function = self.builder.function
        failure = function.append_basic_block(failure_name)
        continuation = function.append_basic_block("no.overflow")
        self.builder.cbranch(condition, failure, continuation)
        self.builder.position_at_end(failure)
        self.builder.call(self.trap, [])
        self.builder.unreachable()
        self.builder.position_at_end(continuation)

    def _emit_place(self, expression: ExprNode) -> tuple[ir.Value, str]:
        assert self.builder is not None
        if isinstance(expression, IdentifierNode):
            return self._lookup(expression.name)
        if isinstance(expression, FieldAccessNode):
            object_pointer, object_type = self._emit_place(expression.object)
            field, index = self.analysis.structs[object_type].fields[expression.field]
            pointer = self.builder.gep(
                object_pointer,
                [ir.Constant(self.i32, 0), ir.Constant(self.i32, index)],
                inbounds=True, name=f"{expression.field}.addr",
            )
            return pointer, field.type_name
        value = self._emit_expr(expression)
        type_name = self.analysis.expression_types[id(expression)]
        slot = self.builder.alloca(self.llvm_types[type_name], name="object.tmp")
        self.builder.store(value, slot)
        return slot, type_name

    def _lookup(self, name: str) -> tuple[ir.Value, str]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        if self.current_struct is not None and name in self.analysis.structs[self.current_struct].fields:
            field, index = self.analysis.structs[self.current_struct].fields[name]
            pointer = self.builder.gep(
                self.self_pointer,
                [ir.Constant(self.i32, 0), ir.Constant(self.i32, index)],
                inbounds=True, name=f"{name}.addr",
            )
            return pointer, field.type_name
        return self.global_values[name], self.analysis.globals[name].type_name

    def _string_constant(self, text: str) -> ir.Value:
        assert self.builder is not None
        encoded = text.encode("utf-8") + b"\0"
        value_type = ir.ArrayType(self.i8, len(encoded))
        name = f".str.{self.string_counter}"
        self.string_counter += 1
        global_value = ir.GlobalVariable(self.module, value_type, name=name)
        global_value.linkage = "internal"
        global_value.global_constant = True
        global_value.initializer = ir.Constant(value_type, bytearray(encoded))
        return self.builder.bitcast(global_value, self.i8p)

    def _printf(self, format_string: str, *arguments: ir.Value) -> None:
        assert self.builder is not None and self.printf is not None
        self.builder.call(self.printf, [self._string_constant(format_string), *arguments])

    def _emit_print(self, value: ir.Value, type_name: str) -> None:
        if type_name == "i32":
            self._print_scalar(value, type_name, True)
        elif type_name == "i64":
            self._print_scalar(value, type_name, True)
        elif type_name == "bool":
            self._print_scalar(value, type_name, True)
        elif type_name == "string":
            self._print_scalar(value, type_name, True)
        else:
            slot = self.builder.alloca(self.llvm_types[type_name], name="print.object")
            self.builder.store(value, slot)
            self._print_object(slot, type_name)
            self._printf("}\n")

    def _print_object(self, pointer: ir.Value, type_name: str) -> None:
        assert self.builder is not None
        self._printf(f"{type_name}{{")
        info = self.analysis.structs[type_name]
        for index, field in enumerate(info.node.fields):
            if index:
                self._printf(", ")
            field_pointer = self.builder.gep(
                pointer,
                [ir.Constant(self.i32, 0), ir.Constant(self.i32, index)],
                inbounds=True,
            )
            field_value = self.builder.load(field_pointer)
            if field.type_name in self.analysis.structs:
                self._printf(f"{field.name}=")
                self._print_object(field_pointer, field.type_name)
                self._printf("}")
            else:
                self._print_scalar(field_value, field.type_name, False,
                                   prefix=f"{field.name}=")

    def _print_scalar(self, value: ir.Value, type_name: str, newline: bool,
                      prefix: str = "") -> None:
        suffix = "\n" if newline else ""
        assert self.builder is not None
        if type_name == "i32":
            self._printf(f"{prefix}%d{suffix}", value)
        elif type_name == "i64":
            self._printf(f"{prefix}%lld{suffix}", value)
        elif type_name == "bool":
            text = self.builder.select(value, self._string_constant("true"),
                                       self._string_constant("false"))
            self._printf(f"{prefix}%s{suffix}", text)
        elif type_name == "string":
            self._printf(f"{prefix}%s{suffix}", value)


def generate_ir(program: ProgramNode, analysis: SemanticAnalyzer) -> str:
    return CodeGenerator(program, analysis).generate()
