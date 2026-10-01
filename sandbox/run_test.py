"""Sandbox entrypoint: run one pytest file, print a JSON RunOutcome. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
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


TSX = "file:///usr/local/lib/node_modules/tsx/dist/loader.mjs"
# a test that couldn't load or call the code proves nothing (vs. the code misbehaving)
TS_CANT_RUN = re.compile(
    r"Cannot find module|ERR_MODULE_NOT_FOUND|does not provide an export named|"
    r"SyntaxError|TransformError|ReferenceError: \w+ is not defined|Cannot find package"
)


def _ts_reached(lcov: str, target: str) -> list[int] | None:
    """Executed lines of the target from Node's own coverage (lcov DA:line,count)."""
    if not target:
        return None
    lines: list[int] = []
    try:
        with open(lcov) as fh:
            for row in fh:
                if row.startswith("DA:"):
                    line, count = row[3:].strip().split(",")[:2]
                    if int(count) > 0:
                        lines.append(int(line))
    except (OSError, ValueError):
        return []
    return sorted(lines)


def _ts_classify(report: str, test_path: str, output: str) -> tuple[str, str]:
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError):
        return "error", "no test report (the test file did not load)"
    cases = root.findall(".//testcase")
    if cases and all(c.get("name") == test_path for c in cases):
        # node reports a file that failed to load as one test named after the file
        why = TS_CANT_RUN.search(output)
        line = next((ln for ln in output.splitlines() if why and why.group(0) in ln), "")
        return "error", (line.strip() or "the test file failed to load")[:300]
    failures = [f for c in cases for f in c.findall("failure")]
    if not cases:
        return "error", "no tests ran"
    if failures:
        node = failures[0]
        text = (node.get("message") or node.text or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        first = lines[0] if lines else "test failed"
        if first.endswith(":") and len(lines) > 1:  # node puts the values below the header
            if "+ actual - expected" in lines:  # diff form: "+ <actual>" / "- <expected>"
                got = next((ln[2:] for ln in lines if ln.startswith("+ ") and ln != lines[1]), "")
                want = next((ln[2:] for ln in lines if ln.startswith("- ")), "")
                first = f"{first[:-1]}: got {got}, want {want}"
            else:
                first = f"{first} {lines[1]}"
        body = text + (node.text or "")
        return ("error" if TS_CANT_RUN.search(body) else "failed"), first[:300]
    return "passed", ""


def run_ts(test_path: str, timeout: int) -> dict:
    report, lcov = "/tmp/report.xml", "/tmp/lcov.info"
    target = os.environ.get("UXIE_TARGET_FILE", "")
    cmd = ["node", "--import", TSX]
    if test_path.endswith((".tsx", ".jsx")):  # components need a DOM before imports run
        cmd += ["--import", "file:///opt/uxie/tools/dom-setup.mjs"]
    cmd += ["--test", "--test-reporter=junit", f"--test-reporter-destination={report}"]
    if target:  # node's own coverage maps through tsx's source maps line-accurately
        cmd += [
            "--experimental-test-coverage",
            f"--test-coverage-include={target}",
            "--test-reporter=lcov",
            f"--test-reporter-destination={lcov}",
        ]
    cmd.append(test_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else ""
        return {"outcome": "timeout", "exit_code": -1, "failure": "", "output_tail": out}
    outcome, failure = _ts_classify(report, test_path, proc.stdout + proc.stderr)
    reached = _ts_reached(lcov, target)
    return {
        "outcome": outcome,
        "exit_code": proc.returncode,
        "failure": failure,
        "output_tail": proc.stdout + proc.stderr,
        "reached": reached if outcome != "error" else [],
    }


RUNNERS = {"python": run_python, "go": run_go, "typescript": run_ts}


def _egress_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_isolation(probe: str = "1.1.1.1:80", limit_s: float = 15.0) -> float | None:
    """Block until outbound traffic is cut off; None if it never is.

    Kubernetes applies the deny-all NetworkPolicy a moment after a pod starts, so for
    about a second the network can still be open. No test code runs before this passes.
    """
    host, port = probe.rsplit(":", 1)
    t0 = time.monotonic()
    blocked = 0
    while time.monotonic() - t0 < limit_s:
        if _egress_open(host, int(port)):
            blocked = 0
            time.sleep(0.25)
        else:
            blocked += 1
            if blocked >= 2:  # two misses in a row, not a single dropped packet
                return round(time.monotonic() - t0, 2)
    return None


def main() -> None:
    test_path, timeout = sys.argv[1], int(os.environ.get("TEST_TIMEOUT", "60"))
    waited = wait_for_isolation(os.environ.get("UXIE_EGRESS_PROBE", "1.1.1.1:80"))
    if waited is None:  # fail closed: never run untrusted code with a live network
        print(
            json.dumps(
                {
                    "outcome": "error",
                    "exit_code": -1,
                    "duration_s": 0.0,
                    "failure": "sandbox network was not isolated; test not run",
                    "output_tail": "",
                }
            )
        )
        return
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
