#!/usr/bin/env python3
"""Run named compiler fixtures and compare exact output or diagnostics."""

from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
COMPILER = ROOT / "compiler.py"


def run_case(path: Path, expected_path: Path, expect_success: bool) -> str | None:
    if not expected_path.is_file():
        return f"{path.name}: missing {expected_path.name}"
    expected = expected_path.read_text(encoding="utf-8").rstrip("\n")
    with tempfile.NamedTemporaryFile("wb", suffix=".txt") as input_file:
        input_file.write(path.read_bytes())
        input_file.flush()
        result = subprocess.run(
            [sys.executable, str(COMPILER), "--ast", input_file.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    stdout = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
    stderr = result.stderr.decode("utf-8", errors="replace").rstrip("\n")
    if expect_success:
        if result.returncode != 0 or stderr or stdout != expected:
            return (f"{path.name}: AST mismatch\n"
                    f"  exit: {result.returncode}\n"
                    f"  stderr: {stderr!r}\n"
                    f"  expected:\n{expected}\n"
                    f"  actual:\n{stdout}")
    else:
        if result.returncode == 0 or stdout or stderr != expected:
            return (f"{path.name}: error mismatch\n"
                    f"  exit: {result.returncode}\n"
                    f"  stdout: {stdout!r}\n"
                    f"  expected: {expected!r}\n"
                    f"  actual: {stderr!r}")
    return None


def main() -> int:
    valid = [(path, path.with_suffix(".expected"), True)
             for path in sorted((ROOT / "tests" / "valid").glob("*.txt"))]
    errors = [(path, path.with_suffix(".err"), False)
              for path in sorted((ROOT / "tests" / "errors").glob("*.txt"))]
    cases = valid + errors
    failures = []
    for path, expected_path, success in cases:
        error = run_case(path, expected_path, success)
        if error is None:
            print(f"PASS: {path.relative_to(ROOT)}")
        else:
            failures.append(error)
            print(f"FAIL: {error}")
    passed = len(cases) - len(failures)
    print(f"{passed}/{len(cases)} tests passed")
    if len(valid) < 15 or len(errors) < 15:
        print("FAIL: at least 15 valid and 15 error cases are required")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
