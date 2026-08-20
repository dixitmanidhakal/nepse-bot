#!/usr/bin/env python3
"""
NEPSE Trading Bot — unified test runner.

Replaces the legacy `run_day2_tests.py` … `run_day5_tests.py` scripts with a
single entry point that delegates to the professional pytest suite under
``tests/`` (unit / api / integration).

Usage
-----
    python run_tests.py                 # run everything
    python run_tests.py unit            # unit tests only
    python run_tests.py api             # api layer tests only
    python run_tests.py integration     # end-to-end tests only
    python run_tests.py -k keyword      # pytest -k filter
    python run_tests.py -v              # verbose, any extra args forwarded

Anything unrecognised is forwarded straight to pytest, so the full pytest
CLI is available (e.g. ``python run_tests.py tests/unit -x --maxfail=1``).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"

SUITES: dict[str, list[str]] = {
    "unit": ["tests/unit"],
    "api": ["tests/api"],
    "integration": ["tests/integration"],
    "all": ["tests"],
}


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _resolve_args(argv: list[str]) -> list[str]:
    """Translate CLI shortcuts into pytest args."""
    if not argv:
        return SUITES["all"]

    first = argv[0].lower()
    if first in SUITES:
        return SUITES[first] + argv[1:]
    # Not a named suite — forward as-is to pytest.
    return argv


def main() -> int:
    if not TESTS_DIR.exists():
        print(f"✗ tests/ directory not found at {TESTS_DIR}", file=sys.stderr)
        return 1

    args = _resolve_args(sys.argv[1:])

    _banner("NEPSE Trading Bot — Test Suite")
    print(f"  Root : {ROOT}")
    print(f"  Args : {' '.join(args) or '(default)'}\n")

    cmd = [sys.executable, "-m", "pytest", *args]
    try:
        result = subprocess.run(cmd, cwd=ROOT)
    except FileNotFoundError:
        print(
            "✗ pytest is not installed. Activate your venv and run:\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    _banner("RESULT")
    if result.returncode == 0:
        print("  ✓ All tests passed")
    else:
        print(f"  ✗ pytest exited with code {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
