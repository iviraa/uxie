"""Sandbox entrypoint: run one pytest file, print a JSON RunOutcome. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import shutil
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


GO_COMPILE = re.compile(r"\.go:\d+:\d+: .+|cannot find package|no required module|setup failed")


def _go_module_root(start: str) -> str:
    d = os.path.abspath(os.path.dirname(start) or ".")
    while d != "/" and not os.path.exists(os.path.join(d, "go.mod")):
        d = os.path.dirname(d)
    return d if os.path.exists(os.path.join(d, "go.mod")) else os.path.abspath(".")


def _go_reached(profile: str, target: str, module_root: str) -> list[int] | None:
    """Covered lines of the target file from a -coverprofile (blocks with count > 0)."""
    if not target:
        return None
    rel = os.path.relpath(os.path.abspath(target), module_root)
    lines: set[int] = set()
    try:
        with open(profile) as fh:
            for row in fh:
                if row.startswith("mode:") or " " not in row:
                    continue
                spec, _stmts, count = row.rsplit(" ", 2)
                name, rng = spec.rsplit(":", 1)
                if int(count) > 0 and name.endswith("/" + rel):
                    start, end = rng.split(",")
                    lines.update(range(int(start.split(".")[0]), int(end.split(".")[0]) + 1))
    except (OSError, ValueError):
        return []
    return sorted(lines)


def run_go(test_path: str, timeout: int) -> dict:
    root = _go_module_root(test_path)
    pkg = "./" + os.path.relpath(os.path.dirname(os.path.abspath(test_path)), root)
    profile = "/tmp/cover.out"
    cache = os.environ.get("GOCACHE", "/tmp/gocache")
    if os.path.isdir("/opt/gocache") and not os.path.exists(cache):
        shutil.copytree("/opt/gocache", cache)  # prebuilt stdlib; must be writable for go
    cmd = [
        "go",
        "test",
        "-json",
        "-count=1",
        "-run",
        "^TestUxie",
        "-covermode=set",
        f"-coverprofile={profile}",
        pkg,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=root)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else ""
        return {"outcome": "timeout", "exit_code": -1, "failure": "", "output_tail": out}
    tests: dict[str, str] = {}
    logs: dict[str, list[str]] = {}
    text: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            text.append(line)
            continue
        name, action = ev.get("Test"), ev.get("Action")
        if ev.get("Output"):
            text.append(ev["Output"].rstrip("\n"))
            if name:
                logs.setdefault(name, []).append(ev["Output"].strip())
        if name and action in ("pass", "fail", "skip"):
            tests[name] = action
    out = "\n".join(text) + proc.stderr
    failed = [t for t, a in tests.items() if a == "fail"]
    if failed:
        # the assertion message is usually the "file_test.go:NN: ..." line of the failing test
        detail = next((m for m in logs.get(failed[0], []) if "_test.go:" in m), "")
        panic = next((m for m in logs.get(failed[0], []) if m.startswith("panic:")), "")
        outcome, failure = "failed", (panic or detail or f"{failed[0]} failed")
    elif tests and all(a == "pass" for a in tests.values()):
        outcome, failure = "passed", ""
    else:  # build errors, no matching tests: the test never ran
        err = GO_COMPILE.search(out)
        outcome, failure = "error", (err.group(0) if err else "no test ran")
    reached = _go_reached(profile, os.environ.get("UXIE_TARGET_FILE", ""), root)
    return {
        "outcome": outcome,
        "exit_code": proc.returncode,
        "failure": failure[:300],
        "output_tail": out,
        "reached": reached if outcome != "error" else [],
    }


RUNNERS = {"python": run_python, "go": run_go}


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
