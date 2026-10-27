# Sprout

Sprout is a small expression language whose declarations and control-flow keywords use emoji. Stage 1 implements a byte-oriented hand-written lexer, recursive-descent parser, and indented AST dump. It validates integer literal ranges in integer declarations and assignments; other type checking, constant reassignment checks, and evaluation are deferred to the next stage. The type of an `if` condition is checked in Stage 2.

The implementation is split across `lexer.py`, `ast_nodes.py`, and `parser.py`; `compiler.py` provides the command-line entry point. `🪴(expr)` marks an overflow-checked expression syntactically; runtime arithmetic overflow behavior is deferred to a later stage.

## Syntax

| Syntax | Meaning |
| --- | --- |
| `🪵` | mutable variable |
| `🌿` | immutable variable |
| `🧊` | named constant declaration |
| `🌰` | signed 32-bit integer (`i32`) |
| `🌳` | signed 64-bit integer (`i64`) |
| `🌸` | boolean |
| `⚖` / `⚖️`, `✂` / `✂️` | equality / inequality; variation selector is optional |
| `🪴(expr)` | overflow-check expression |
| `🌤` / `🌤️`, `🌧` / `🌧️` | if / optional else; variation selector is optional |
| `🍎` / `🍏` | true / false |
| `🪡` | required between statements on the same line; optional at line ends |

`+` and `-` have lower precedence than `*` and `/`; operators at each arithmetic level associate left-to-right. A comparison is optional and can occur once at the outer expression level. Each program contains one or more statements. Blocks also require at least one statement. A line break separates statements; two statements on the same line must have `🪡` between them. Identifiers use ASCII letters or `_` initially, followed by ASCII letters, digits, or `_`. Spaces, tabs, carriage returns, and line feeds separate tokens; comments are not part of the language. A UTF-8 BOM is ignored only at the beginning of the source and is excluded from source columns. A BOM elsewhere is an error. Parentheses group expressions but do not add an AST node. A `BinaryExprNode` position is the operator token's position; other positions count UTF-8 bytes within each line.

Examples:

```text
🪵🌰 count💧1 + 2 * 3
🌿🌳 limit💧9223372036854775807
🌿🌸 ready💧🍎
🧊🌰 hard_limit💧99🪡
🌿🌸 same💧count⚖️7
🌿🌸 different💧count✂️0
count💧🪴(count + 1)
🌤️ count⚖️7 { count💧8 } 🌧️ { count💧0 }
🌤 count⚖7 { count💧8 } 🌧 { count💧0 }
```

The complete grammar is in [`grammar.ebnf`](grammar.ebnf).

## Build and run

Requires Python 3.10 or newer; there are no third-party dependencies.

```sh
python3 compiler.py --ast input.txt
```

On success, the compiler writes a stable, indented AST to stdout. On a lexical or syntax error it writes one `compilation error: line L:C: message` diagnostic to stderr, exits non-zero, and leaves stdout empty. Positions count source bytes within each line, matching the byte-oriented lexer.

## Tests

```sh
python3 run_tests.py
```

The runner executes the compiler against each named fixture in `tests/valid/` and `tests/errors/`. Every `tests/valid/name.txt` has a matching `name.expected` AST dump; every `tests/errors/name.txt` has a matching `name.err` diagnostic. Test inputs are read as bytes so malformed UTF-8 is covered too. The suite currently checks 24 valid programs and 35 lexical, syntax, or integer-literal range errors.
