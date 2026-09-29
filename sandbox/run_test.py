"""Sandbox entrypoint: run one pytest file, print a JSON RunOutcome. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPORT = "/tmp/report.xml"

# failures that mean the test couldn't exercise the code (API absent or called wrong),
# as opposed to the code misbehaving. NoneType attribute errors are real bugs, so excluded.
_CANT_RUN = re.compile(
    r"^(ImportError|ModuleNotFoundError|NameError)\b"
    r"|^TypeError: .*(unexpected keyword argument|positional argument|takes \d+ positional)"
    r"|^AttributeError: (module|type object) .* has no attribute"
)


def cant_run(message: str) -> bool:
    return bool(_CANT_RUN.search(message.strip()))


def classify(report_path: str, exit_code: int) -> tuple[str, str]:
    """(outcome, first line of the failure message)."""
    try:
        root = ET.parse(report_path).getroot()
    except (OSError, ET.ParseError):
        return "error", ""
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return "error", ""
    tests = int(suite.get("tests", 0))
    errors, failures = int(suite.get("errors", 0)), int(suite.get("failures", 0))
    if tests == 0 or errors:
        return "error", ""  # collection/import errors mean the test never really ran
    if failures:
        node = suite.find(".//failure")
        msg = (node.get("message", "") if node is not None else "").strip().splitlines()
        first = msg[0] if msg else ""
        return ("error" if cant_run(first) else "failed"), first[:300]
    return ("passed" if exit_code == 0 else "error"), ""


def main() -> None:
    test_path, timeout = sys.argv[1], int(os.environ.get("TEST_TIMEOUT", "60"))
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "no:cacheprovider",
        f"--junitxml={REPORT}",
        "--rootdir=.",
        test_path,
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        (outcome, failure), code = classify(REPORT, proc.returncode), proc.returncode
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        outcome, code, failure = "timeout", -1, ""
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else ""
    print(
        json.dumps(
            {
                "outcome": outcome,
                "exit_code": code,
                "duration_s": round(time.monotonic() - t0, 2),
                "failure": failure,
                "output_tail": out[-3000:],
            }
        )
    )


if __name__ == "__main__":
    main()
