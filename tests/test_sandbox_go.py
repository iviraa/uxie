"""Go sandbox against real containers (Docker and k3s). Skipped without the image/cluster."""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from uxie.sandbox.envs import GoDeps
from uxie.sandbox.executor import DockerExecutor, verify
from uxie.sandbox.languages import GO
from uxie.sandbox.spec import CONFIRMED, REFUTED, VerifyJob

JOBS = Path.home() / ".uxie" / "jobs"


def _ok(*cmd: str) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


HAVE_GO = _ok("docker", "image", "inspect", "uxie-sandbox-go:dev")
HAVE_K8S = HAVE_GO and _ok("kubectl", "get", "namespace", "uxie-sandbox")
pytestmark = pytest.mark.skipif(not HAVE_GO, reason="uxie-sandbox-go:dev image missing")


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
    d = JOBS / f"gotest-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _module(root: Path, clamp_body: str) -> Path:
    (root / "mathx").mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/lib\n\ngo 1.22\n")
    (root / "mathx" / "clamp.go").write_text(
        "package mathx\n\n// Clamp limits x to [lo, hi].\n"
        f"func Clamp(x, lo, hi int) int {{\n\t{clamp_body}\n}}\n"
    )
    return root


def _job(code: str) -> VerifyJob:
    return VerifyJob("g", "", "", "", "mathx/uxie_verify_test.go", code, timeout_s=120)


TEST = (
    'package mathx\n\nimport "testing"\n\nfunc TestUxieClamp(t *testing.T) {{\n'
    "\tif got := Clamp({x}, 0, 10); got != {want} {{\n"
    '\t\tt.Fatalf("Clamp = %d, want {want}", got)\n\t}}\n}}\n'
)


def test_regression_is_confirmed_and_fast(ex, work):
    base = _module(work / "base", "return max(lo, min(x, hi))")
    head = _module(work / "head", "return max(lo, min(x, lo))")
    t0 = time.monotonic()
    v = verify(_job(TEST.format(x=5, want=5)), base, head, work / "w", ex, lang=GO)
    assert v.verdict == CONFIRMED, (v.head, v.base)
    assert "want 5" in v.head.failure
    assert time.monotonic() - t0 < 60  # warm stdlib cache keeps two runs well under a minute


def test_false_claim_is_refuted(ex, work):
    base = _module(work / "base", "return max(lo, min(x, hi))")
    head = _module(work / "head", "return max(lo, min(x, hi))")
    v = verify(_job(TEST.format(x=-5, want=0)), base, head, work / "w", ex, lang=GO)
    assert v.verdict == REFUTED


def test_compile_error_is_not_a_failure(ex, work):
    head = _module(work / "head", "return max(lo, min(x, hi))")
    code = TEST.format(x=5, want=5).replace("Clamp(", "Clampp(")
    v = verify(_job(code), None, head, work / "w", ex, lang=GO)
    assert v.head.outcome == "error" and "undefined" in v.head.failure


def test_network_is_blocked(ex, work):
    head = _module(work / "head", "return max(lo, min(x, hi))")
    code = (
        'package mathx\n\nimport (\n\t"net/http"\n\t"testing"\n)\n\n'
        "func TestUxieNet(t *testing.T) {\n"
        '\tif _, err := http.Get("http://1.1.1.1"); err != nil {\n\t\tt.Fatal(err)\n\t}\n}\n'
    )
    v = verify(_job(code), None, head, work / "w", ex, lang=GO)
    assert v.head.outcome == "failed"
    assert "http.Get" not in v.head.failure or "dial" in v.head.output_tail


def test_reachability_from_coverage(work):
    head = _module(work / "head", "return max(lo, min(x, hi))")
    (head / "mathx" / "other.go").write_text("package mathx\n\nfunc Other() int {\n\treturn 1\n}\n")
    ex = DockerExecutor()
    from uxie.sandbox.executor import stage

    job = _job(TEST.format(x=5, want=5))
    hit = ex.run(stage(head, job, work / "a", GO), job.test_path, 120, None, "mathx/clamp.go", GO)
    miss = ex.run(stage(head, job, work / "b", GO), job.test_path, 120, None, "mathx/other.go", GO)
    assert hit.reaches(5, 0) and miss.reached == []


def test_third_party_module_offline(work):
    mod = work / "dep"
    (mod / "cmpx").mkdir(parents=True)
    (mod / "go.mod").write_text(
        "module example.com/dep\n\ngo 1.22\n\nrequire github.com/google/go-cmp v0.6.0\n"
    )
    (mod / "cmpx" / "eq.go").write_text(
        'package cmpx\n\nimport "github.com/google/go-cmp/cmp"\n\n'
        "func Same(a, b []int) bool {\n\treturn cmp.Equal(a, b)\n}\n"
    )
    # trusted setup: produce go.sum the way a real repo would have it
    tidy = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{mod}:/m",
            "-w",
            "/m",
            "golang:1.27-bookworm",
            "go",
            "mod",
            "tidy",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert tidy.returncode == 0, tidy.stderr
    env = GoDeps(work / "envs", min_free_bytes=2 * 1024**3).ensure(mod, target="cmpx/eq.go")
    assert env is not None and (env / "gomod" / "github.com" / "google").is_dir()
    code = (
        'package cmpx\n\nimport "testing"\n\nfunc TestUxieSame(t *testing.T) {\n'
        '\tif !Same([]int{1}, []int{1}) {\n\t\tt.Fatal("not same")\n\t}\n}\n'
    )
    from uxie.sandbox.executor import stage

    job = VerifyJob("d", "", "", "", "cmpx/uxie_verify_test.go", code, timeout_s=120)
    out = DockerExecutor().run(
        stage(mod, job, work / "s", GO, True), job.test_path, 120, env, "cmpx/eq.go", GO
    )
    assert out.outcome == "passed", out.output_tail[-800:]
