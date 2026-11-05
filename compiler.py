from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from lexer import CompilerError
from parser import compile_source
from semantic import analyze


def main(arguments: Sequence[str]) -> int:
    optimize = "--optimize" in arguments
    arguments = [a for a in arguments if a != "--optimize"]
    if len(arguments) == 2 and arguments[0] == "--ast":
        ast_mode = True
        input_path = arguments[1]
        output_path = None
    elif len(arguments) == 2:
        ast_mode = False
        input_path, output_path = arguments
    else:
        print("usage: python3 compiler.py --ast input.txt | python3 compiler.py input.txt output.ll",
              file=sys.stderr)
        return 2
    try:
        source = Path(input_path).read_bytes()
        program = compile_source(source)
    except CompilerError as error:
        print(f"compilation error: line {error.line}:{error.column}: {error.message}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"compilation error: line 1:1: {error.strerror or 'could not read input file'}", file=sys.stderr)
        return 1
    try:
        analysis = analyze(program)
        if optimize:
            from optimizer import optimize as optimize_program
            program = optimize_program(program)
            analysis = analyze(program)
        if ast_mode:
            print("\n".join(program.format_tree()))
            return 0
        from codegen import generate_ir
        ir_text = generate_ir(program, analysis)
        Path(output_path).write_text(ir_text, encoding="utf-8")
    except CompilerError as error:
        print(f"compilation error: line {error.line}:{error.column}: {error.message}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"compilation error: line 1:1: {error.strerror or 'could not write output file'}",
              file=sys.stderr)
        return 1
    except ImportError as error:
        print(f"compilation error: line 1:1: LLVM builder dependency is unavailable: {error}",
              file=sys.stderr)
        return 1
    except (AssertionError, KeyError, RuntimeError, TypeError, ValueError) as error:
        print(f"compilation error: line 1:1: code generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
