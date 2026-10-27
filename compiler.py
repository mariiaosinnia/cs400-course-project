from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from lexer import CompilerError
from parser import compile_source


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "--ast":
        print("usage: python3 compiler.py --ast input.txt", file=sys.stderr)
        return 2
    try:
        source = Path(arguments[1]).read_bytes()
        program = compile_source(source)
    except CompilerError as error:
        print(f"compilation error: line {error.line}:{error.column}: {error.message}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"compilation error: line 1:1: {error.strerror or 'could not read input file'}", file=sys.stderr)
        return 1
    print("\n".join(program.format_tree()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
