# Sprout

Sprout is an emoji-based expression language. Stage 2 keeps the Stage 1 syntax
and adds typed functions, structures, member functions, `read`, `print`, semantic
analysis, and LLVM IR generation. The byte-oriented hand-written lexer and
recursive-descent parser are in `lexer.py` and `parser.py`; `semantic.py`
validates the AST before `codegen.py` uses `llvmlite.ir` to build the module.

## Syntax at a glance

| Syntax | Meaning |
| --- | --- |
| `🪵` / `🌿` / `🧊` | mutable / immutable / constant binding |
| `🌰` / `🌳` / `🌸` / `string` | signed i32 / signed i64 / bool / UTF-8 string |
| `🌤` / `🌤️`, `🌧` / `🌧️` | if / optional else |
| `⚖` / `⚖️`, `✂` / `✂️` | equality / inequality |
| `🍎` / `🍏` | true / false |
| `🪴(expr)` | check integer arithmetic for overflow at runtime |
| `🪡` | separates same-line statements; optional at line ends |
| `fn name(x: 🌰) -> 🌰 { ... }` | typed function with value-returning `return` |
| `struct Name { 🪵 🌰 field }` | named structure with ordered fields |
| `Name{field: value}` | initialize every structure field by name |
| `object.field`, `object.method(args)` | field or member access/call |
| `read()` / `print(expr)` | integer input / output |
| `while n { ... }` | repeat while an integer is non-zero |
| `for i in start..end { ... }` | half-open integer range loop |

Functions, including forward references and recursion:

```text
fn twice(value: 🌰) -> 🌰 {
return value * 2
}
print(twice(6))
```

Structures, fields, and a member function:

```text
struct Point {
🪵 🌰 x
🌿 🌰 y
fn x_value() -> 🌰 {
return x
}
}
🪵 Point p💧Point{x: 4, y: 9}
print(p.x_value())
```

Input and output:

```text
🪵🌰 count💧read()
print(count)
print("done")
```

Stage 3 loops use ordinary ASCII contextual words. `while` accepts an `i32` or
`i64` condition. A `for` loop counts from its inclusive start to its exclusive
end; its immutable index is scoped to the body.

Loop example 1, a countdown:

```text
🪵🌰 n💧read()
while n {
print(n)
n💧n-1
}
```

Loop example 2, a sum inside a function:

```text
fn sum(limit: 🌰) -> 🌰 {
🪵🌰 result💧0
for i in 0..limit { result💧result+i }
return result
}
print(sum(4))
```

The full EBNF, including lexical and layout rules, is in
[`grammar.ebnf`](grammar.ebnf).

The ASCII words `fn`, `struct`, `return`, `read`, and `print` are interpreted
contextually rather than reserved by the lexer, so Stage 1 identifiers with
those spellings remain usable in name positions. `read()` and `print(...)` are
the built-ins; user functions cannot shadow those call names. `string` denotes
the built-in type only where a type is expected.

## Semantic rules

These are enforced before IR generation; calls and assignments are not left for
LLVM to diagnose.

* **Types and calls.** Primitive types are `i32`, `i64`, `bool`, and `string`;
  a declared structure name is also a type. Arguments and returned values must have the
  exact declared type. Integer literals can take the expected integer width if
  in range; there are no implicit conversions between variables of different
  widths. `if` requires `bool`; arithmetic requires matching integer operands;
  comparisons require matching integer or boolean operands. Functions have a result type,
  must return that type on every path, and can call themselves or any function
  declared in the module, including a later declaration. `read()` takes no
  arguments and yields `i32`; `print` takes one printable value.
  `function_call.txt` and `recursive_call.txt` follow these rules;
  `too_many_arguments.txt`, `too_few_arguments.txt`,
  `wrong_argument_type.txt`, `wrong_return_type.txt`, and
  `non_boolean_condition.txt` violate them. Parameters are immutable bindings;
  `parameter_assignment.txt` violates that rule. `read()` takes no arguments;
  `wrong_read_arity.txt` is rejected. `print` cannot be nested as a value,
  covered by the accepted `print_types.txt` and rejected `print_void.txt`.
* **Scopes and name lookup.** A variable must be declared before use and cannot
  be declared twice in one scope. Function bodies see their parameters and
  locals (parameters are immutable), then (for a member function) the
  structure's fields, then globals declared before that function's containing
  declaration. Thus a parameter or
  local shadows a field, and a field shadows a global of the same name.
  Inside a member function the reserved name `self` denotes the receiver;
  fields can also be accessed directly by name.
  An unqualified call inside a member function resolves a same-named member
  method before a global function, but a parameter, local, or field with that
  name shadows the callable and makes the call an error.
  `member_name_lookup.txt` demonstrates parameter-over-field and
  field-over-global lookup; `use_before_declaration.txt`,
  `duplicate_local.txt`, and `function_local_not_global.txt` violate visibility
  or scope rules. `keyword_identifiers.txt` checks that the new introducer
  words do not invalidate Stage 1 identifier names. The
  `parameter_shadows_method.txt` case checks callable shadowing.
* **Fields and mutability.** An object initializer must supply every field
  exactly once, and a structure must have at least one field. By-value
  recursive structure layouts are rejected (use finite nested values instead).
  Access must name an existing field. Assignment through a chain
  is allowed only when the root object and every field along the chain are
  mutable. A member function is mutating when it assigns a receiver field
  directly or calls another mutating member on the receiver; such a function can only be called on a mutable object, including
  through a chain. `field_chain.txt` and `mutable_field_chain.txt` follow these
  rules; `unknown_field.txt`, `immutable_field_chain.txt`, and
  `mutating_method_on_constant.txt` and
  `transitive_mutating_method_on_constant.txt` violate them. The initializer requires all
  fields exactly once: `structure_print.txt` follows this rule, while
  `missing_object_field.txt` and `duplicate_object_field.txt` violate it.
  `empty_structure.txt` and `recursive_structure.txt` cover the layout
  restrictions.
* **Overflow.** Integer literals must fit their expected signed width.
  Arithmetic outside `🪴(...)` uses LLVM's ordinary integer operations.
  Arithmetic inside it checks signed add/subtract/multiply overflow and
  division by zero or the signed-minimum divided by `-1`; a failed check calls
  the LLVM trap intrinsic. `overflow_check_run.txt` is the non-overflowing
  case and `overflow_check_trap.txt`, `overflow_divide_zero.txt`, and
  `overflow_divide_minimum.txt` check runtime traps, while the Stage 1
  `i32_literal_overflow.txt`,
  `i64_literal_overflow.txt`, and `i32_assignment_literal_overflow.txt`
  fixtures cover rejected literal ranges.
* **Printing.** `i32` uses `%d`, `i64` uses `%lld`, booleans print as `true` or
  `false`, and strings print their decoded contents. A structure prints in
  declaration field order as `Type{field=value, ...}`; nested structures use
  the same representation. Every `print` appends exactly one newline, including
  object output. `print_types.txt`, `structure_print.txt`, and
  `field_chain.txt` check the values and exact newline behavior. `read()` reads
  one decimal `i32` from standard input without echoing it; `read_input.txt`
  supplies its data in `read_input.in`.

The syntax errors and semantic failures above are paired with expected
diagnostics; the valid examples exercise the corresponding accepted cases.

## Build and run

Python 3.10+, `llvmlite`, LLVM `llc`, and `clang` are required:

```sh
python3 -m pip install -r requirements.txt
python3 compiler.py --ast input.txt
python3 compiler.py input.txt output.ll
python3 compiler.py --optimize input.txt optimized.ll
llc -filetype=obj output.ll -o output.o
clang -no-pie output.o -o program
./program
```

`--ast` prints the indented AST, including types, field mutability, call
arguments, and member-call chain flags. Compilation writes only the builder's
printed module to the requested output file. Lexical, syntax, and semantic
errors produce one `compilation error: line L:C: message` on stderr, a
non-zero exit status, and no new IR output file. Positions count UTF-8 bytes
within each line; a leading UTF-8 BOM is ignored.

## Tests

Run the complete front-end and end-to-end suite with:

```sh
python3 run_tests.py
python3 run_tests.py --optimize
```

The runner preserves Stage 1 AST snapshots, checks every `.err` diagnostic,
then compiles, assembles, links, executes, and byte-compares every valid program
with a `.out` file; `.trap` cases assert an expected runtime trap. A neighboring
`.in` file is passed as standard input.
End-to-end tests cover functions (including forward calls and recursion),
structures and member functions, nested fields, checked arithmetic, scalar and
object output, and input.

## Stage 3 design and optimizer

`while condition { ... }` repeats while an integer condition is non-zero.
`for i in start..end { ... }` evaluates integer bounds once and visits the
half-open range `[start, end)`; `i` is immutable and scoped to the loop body.
Blocks may contain nested loops, declarations, assignments, calls, and
structures. A loop condition of the wrong type and mismatched bound types are
compile-time errors. These rules are demonstrated in
`tests/valid/stage3_loops.txt`.

With `--optimize`, the compiler repeats a conservative tree pass until stable:
unused pure declarations are removed, unused functions are removed, and a
non-recursive function with exactly one call and one direct `return` expression
is inlined. Calls, `read`, member calls, and overflow checks are never treated
as dead pure initializers. Functions with branches, multiple exits, recursion,
or more than one call are left alone. For a direct call in a declaration or
`print`, each parameter becomes a fresh immutable local declaration initialized
with the argument; references in the copied return expression use those fresh
names. Calls nested in arbitrary expressions are left alone, and recursive
functions are never inlined.

The loop lowering produces ordinary basic-block back edges. A representative
excerpt from the builder-generated IR is:

```llvm
br label %"while.condition"
while.condition:
  %".3" = load i32, i32* %"n.addr"
  %".4" = icmp ne i32 %".3", 0
  br i1 %".4", label %"while.body", label %"while.end"
while.body:
  %".5" = sub i32 %".3", 1
  store i32 %".5", i32* %"n.addr"
  br label %"while.condition"
while.end:
```

The condition block branches to the body or exit. The final branch is the
back edge; `for` uses the same shape with an induction-variable increment.
The body loads and updates the loop variable, while the exit block continues
with the statements after the loop.

For inlining, the builder emits the same operations before and after the
transformation; only the call boundary changes. Before:

```llvm
%".4" = call i32 @"sprout_fn_inc"(i32 4)
```

After:

```llvm
%"__inline_1_value.addr" = alloca i32
store i32 4, i32* %"__inline_1_value.addr"
%".4" = load i32, i32* %"__inline_1_value.addr"
%".5" = add i32 %".4", 1
```

The temporary local preserves the parameter's declared type. The copied
return expression then replaces the call result without evaluating an
effectful argument twice.

The real program is `programs/countdown.txt`: it prompts for a number, reads
it, constructs a `Counter`, calls its member function, calls the top-level
`decrement` function, and visibly uses a `while` loop to print a countdown.
Build and run it with:

```sh
python3 compiler.py programs/countdown.txt countdown.ll
llc -filetype=obj countdown.ll -o countdown.o
clang -no-pie countdown.o -o countdown
./countdown < programs/countdown.in
./countdown < programs/countdown_second.in
```

The exact outputs for those two sessions are in
`programs/countdown.expected` and `programs/countdown_second.expected`.
Session 1 uses input `3` and prints `3`, `2`, `1`; session 2 uses input `5`
and prints `5`, `4`, `3`, `2`, `1`. Both sessions include the prompt
`Enter a starting number:` and are run by the test script.

The optimizer proof fixtures are in `tests/optimizer/`: two unused-variable
cases, two unused-function cases, and two single-call inlining cases. Their
`.expected` files are optimized AST snapshots and their `.out` files are
native-program outputs; the test runner includes them in both modes.
