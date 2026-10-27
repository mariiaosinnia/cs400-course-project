#!/usr/bin/env python3
"""Run front-end fixtures and compile, link, and execute every valid program."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import resource

ROOT = Path(__file__).resolve().parent
COMPILER = ROOT / "compiler.py"


def describe_failure(name: str, phase: str, result: subprocess.CompletedProcess[bytes]) -> str:
    return (f"{name}: {phase} failed (exit {result.returncode})\n"
            f"  stdout: {result.stdout.decode('utf-8', errors='replace')!r}\n"
            f"  stderr: {result.stderr.decode('utf-8', errors='replace')!r}")


def check_valid(path: Path, work: Path) -> list[str]:
    failures: list[str] = []
    expected_ast = path.with_suffix(".expected")
    if expected_ast.is_file():
        ast_result = subprocess.run(
            [sys.executable, str(COMPILER), "--ast", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        actual_ast = ast_result.stdout.rstrip(b"\n")
        if ast_result.returncode != 0 or ast_result.stderr or actual_ast != expected_ast.read_bytes().rstrip(b"\n"):
            failures.append(describe_failure(path.name, "AST", ast_result))

    expected_output_path = path.with_suffix(".out")
    trap_path = path.with_suffix(".trap")
    if not expected_output_path.is_file() and not trap_path.is_file():
        return failures

    ir_path = work / f"{path.stem}.ll"
    compile_result = subprocess.run(
        [sys.executable, str(COMPILER), str(path), str(ir_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if compile_result.returncode != 0 or compile_result.stderr or not ir_path.is_file():
        failures.append(describe_failure(path.name, "compile", compile_result))
        return failures

    object_path = work / f"{path.stem}.o"
    binary_path = work / path.stem
    for command, phase in (
        (["llc", "-filetype=obj", str(ir_path), "-o", str(object_path)], "llc"),
        (["clang", "-no-pie", str(object_path), "-o", str(binary_path)], "clang"),
    ):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            failures.append(describe_failure(path.name, phase, result))
            return failures

    input_path = path.with_suffix(".in")
    program_input = input_path.read_bytes() if input_path.is_file() else b""
    try:
        run_result = subprocess.run(
            [str(binary_path)], input=program_input, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        return [f"{path.name}: program exceeded the {error.timeout}-second timeout"]
    if trap_path.is_file():
        trap_expectation = trap_path.read_bytes().strip()
        if trap_expectation != b"nonzero":
            failures.append(f"{path.name}: .trap must contain 'nonzero'")
            return failures
        if run_result.returncode == 0 or run_result.stdout:
            failures.append(
                f"{path.name}: expected a runtime trap\n"
                f"  exit: {run_result.returncode}\n"
                f"  stdout: {run_result.stdout!r}\n"
                f"  stderr: {run_result.stderr.decode('utf-8', errors='replace')!r}"
            )
        return failures
    expected_output = expected_output_path.read_bytes()
    if run_result.returncode != 0 or run_result.stderr or run_result.stdout != expected_output:
        failures.append(
            f"{path.name}: run output mismatch (exit {run_result.returncode})\n"
            f"  expected: {expected_output!r}\n"
            f"  actual:   {run_result.stdout!r}\n"
            f"  stderr:   {run_result.stderr.decode('utf-8', errors='replace')!r}"
        )
    return failures


def check_invalid(path: Path) -> list[str]:
    expected_path = path.with_suffix(".err")
    if not expected_path.is_file():
        return [f"{path.name}: missing {expected_path.name}"]
    with tempfile.TemporaryDirectory(prefix="sprout-invalid-") as directory:
        ir_path = Path(directory) / "output.ll"
        result = subprocess.run(
            [sys.executable, str(COMPILER), str(path), str(ir_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        output_exists = ir_path.exists()
    expected = expected_path.read_bytes().rstrip(b"\n")
    actual = result.stderr.rstrip(b"\n")
    if result.returncode == 0 or result.stdout or actual != expected or output_exists:
        return [f"{path.name}: error mismatch\n"
                f"  exit: {result.returncode}\n"
                f"  stdout: {result.stdout!r}\n"
                f"  expected: {expected!r}\n"
                f"  actual:   {actual!r}\n"
                f"  output exists: {output_exists}"]
    return []


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    missing = [tool for tool in ("llc", "clang") if shutil.which(tool) is None]
    if missing:
        print(f"FAIL: missing required tool(s): {', '.join(missing)}")
        return 1
    valid = sorted((ROOT / "tests" / "valid").glob("*.txt"))
    errors = sorted((ROOT / "tests" / "errors").glob("*.txt"))
    end_to_end = sum(path.with_suffix(".out").is_file() for path in valid)
    if len(valid) < 20 or len(errors) < 20 or end_to_end < 10:
        print(f"FAIL: expected at least 20 valid, 20 invalid, and 10 end-to-end "
              f"tests; found {len(valid)}, {len(errors)}, and {end_to_end}")
        return 1

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sprout-tests-") as directory:
        work = Path(directory)
        for path in valid:
            case_failures = check_valid(path, work)
            if case_failures:
                failures.extend(case_failures)
                print(f"FAIL: {path.relative_to(ROOT)}")
            else:
                print(f"PASS: {path.relative_to(ROOT)}")
        for path in errors:
            case_failures = check_invalid(path)
            if case_failures:
                failures.extend(case_failures)
                print(f"FAIL: {path.relative_to(ROOT)}")
            else:
                print(f"PASS: {path.relative_to(ROOT)}")
    passed = len(valid) + len(errors) - len(failures)
    print(f"{passed}/{len(valid) + len(errors)} tests passed "
          f"({end_to_end} end-to-end valid programs)")
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
