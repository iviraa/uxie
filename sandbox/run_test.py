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
REACH = "/tmp/reach.json"

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


def _reached() -> list[int] | None:
    if not os.environ.get("UXIE_TARGET_FILE"):
        return None
    try:
        with open(REACH) as fh:
            return list(json.load(fh))
    except (OSError, ValueError):
        return []  # tracing was on but nothing recorded: the file never ran


def run_python(test_path: str, timeout: int) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "no:cacheprovider",
        f"--junitxml={REPORT}",
        "-c",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "pytest.ini"),
        "--rootdir=.",
        test_path,
    ]
    env = dict(os.environ)
    if env.get("UXIE_TARGET_FILE"):  # trace which lines of the flagged file actually run
        cmd[3:3] = ["-p", "uxie_reach"]
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in env.get("PYTHONPATH", "").split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join(p for p in (here, env.get("PYTHONPATH")) if p)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else ""
        return {"outcome": "timeout", "exit_code": -1, "failure": "", "output_tail": out}
    outcome, failure = classify(REPORT, proc.returncode)
    return {
        "outcome": outcome,
        "exit_code": proc.returncode,
        "failure": failure,
        "output_tail": proc.stdout + proc.stderr,
        "reached": _reached(),
    }


RUNNERS = {"python": run_python}


def main() -> None:
    test_path, timeout = sys.argv[1], int(os.environ.get("TEST_TIMEOUT", "60"))
    lang = os.environ.get("UXIE_LANG", "python")
    t0 = time.monotonic()
    runner = RUNNERS.get(lang)
    result = (
        runner(test_path, timeout)
        if runner
        else {"outcome": "error", "exit_code": -1, "failure": f"no runner for {lang}"}
    )
    result["duration_s"] = round(time.monotonic() - t0, 2)
    result["output_tail"] = str(result.get("output_tail", ""))[-3000:]
    print(json.dumps(result))


if __name__ == "__main__":
    main()
