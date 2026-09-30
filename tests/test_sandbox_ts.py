"""TypeScript sandbox against real containers (Docker and k3s). Skipped without the image."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from uxie.sandbox.envs import NodeDeps
from uxie.sandbox.executor import DockerExecutor, stage, verify
from uxie.sandbox.languages import TYPESCRIPT
from uxie.sandbox.spec import CONFIRMED, REFUTED, VerifyJob

JOBS = Path.home() / ".uxie" / "jobs"


def _ok(*cmd: str) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


HAVE_TS = _ok("docker", "image", "inspect", "uxie-sandbox-ts:dev")
HAVE_K8S = HAVE_TS and _ok("kubectl", "get", "namespace", "uxie-sandbox")
pytestmark = pytest.mark.skipif(not HAVE_TS, reason="uxie-sandbox-ts:dev image missing")


@pytest.fixture(
    params=[
        "docker",
        pytest.param("k8s", marks=pytest.mark.skipif(not HAVE_K8S, reason="no k3d cluster")),
    ]
)
def ex(request):
    if request.param == "docker":
        return DockerExecutor()
    from uxie.sandbox.k8s import K8sExecutor

    return K8sExecutor(JOBS)


@pytest.fixture
def work():
    d = JOBS / f"tstest-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _project(root: Path, op: str) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name": "lim", "type": "module"}\n')
    (root / "src" / "limits.ts").write_text(
        "// Inclusive range check.\n"
        "export function inRange(x: number, lo: number, hi: number): boolean {\n"
        f"  return x >= lo && x {op} hi;\n"
        "}\n\nexport function other(): number {\n  return 1;\n}\n"
    )
    return root


def _test(body: str, imports: str = 'import { inRange } from "./limits";') -> str:
    return (
        'import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
        f'{imports}\n\ntest("t", async () => {{\n  {body}\n}});\n'
    )


def _job(code: str) -> VerifyJob:
    return VerifyJob("t", "", "", "", "src/uxie_verify.test.ts", code, timeout_s=90)


def test_regression_is_confirmed(ex, work):
    base, head = _project(work / "base", "<="), _project(work / "head", "<")
    job = _job(_test("assert.equal(inRange(10, 0, 10), true);"))
    v = verify(job, base, head, work / "w", ex, lang=TYPESCRIPT)
    assert v.verdict == CONFIRMED, (v.head, v.base)


def test_false_claim_is_refuted(ex, work):
    base, head = _project(work / "base", "<="), _project(work / "head", "<=")
    job = _job(_test("assert.equal(inRange(11, 0, 10), false);"))
    assert verify(job, base, head, work / "w", ex, lang=TYPESCRIPT).verdict == REFUTED


def test_bad_import_is_not_a_failure(ex, work):
    head = _project(work / "head", "<=")
    job = _job(_test("assert.ok(nope());", 'import { nope } from "./missing";'))
    v = verify(job, None, head, work / "w", ex, lang=TYPESCRIPT)
    assert v.head.outcome == "error"


def test_network_is_blocked(ex, work):
    head = _project(work / "head", "<=")
    job = _job(_test('await fetch("http://1.1.1.1", { signal: AbortSignal.timeout(3000) });'))
    v = verify(job, None, head, work / "w", ex, lang=TYPESCRIPT)
    assert v.head.outcome == "failed"


def test_reachability_is_line_accurate(work):
    head = _project(work / "head", "<=")
    job = _job(_test("assert.equal(inRange(1, 0, 10), true);"))
    out = DockerExecutor().run(
        stage(head, job, work / "s", TYPESCRIPT),
        job.test_path,
        90,
        None,
        "src/limits.ts",
        TYPESCRIPT,
    )
    assert out.reaches(3, 0)  # inRange's body ran
    assert not out.reaches(7, 0)  # other() was never called


def test_npm_dependency_offline_without_install_scripts(work):
    proj = work / "dep"
    (proj / "src").mkdir(parents=True)
    (proj / "package.json").write_text(
        '{"name": "d", "type": "module", "dependencies": {"ms": "2.1.3"},'
        ' "scripts": {"postinstall": "touch /env/PWNED"}}\n'
    )
    (proj / "src" / "dur.ts").write_text(
        'import ms from "ms";\nexport const day = () => ms("1d");\n'
    )
    lock = subprocess.run(  # trusted setup: a real lockfile, as a repo would have
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{proj}:/p",
            "-w",
            "/p",
            "node:24-bookworm-slim",
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert lock.returncode == 0, lock.stderr
    env = NodeDeps(work / "envs", min_free_bytes=2 * 1024**3).ensure(proj, target="src/dur.ts")
    assert env is not None and (env / "mod" / "node_modules" / "ms").is_dir()
    assert not (env / "PWNED").exists()  # the package's own scripts never ran
    job = VerifyJob(
        "d",
        "",
        "",
        "",
        "src/uxie_verify.test.ts",
        _test("assert.equal(day(), 86400000);", 'import { day } from "./dur";'),
        90,
    )
    out = DockerExecutor().run(
        stage(proj, job, work / "s", TYPESCRIPT, True),
        job.test_path,
        90,
        env,
        "src/dur.ts",
        TYPESCRIPT,
    )
    assert out.outcome == "passed", out.output_tail[-800:]
