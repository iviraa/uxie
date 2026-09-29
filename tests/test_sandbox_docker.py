"""Integration: real containers. Skipped when Docker or the sandbox image is unavailable."""

import subprocess

import pytest

from uxie.sandbox.executor import DockerExecutor, verify
from uxie.sandbox.spec import CONFIRMED, LIKELY, REFUTED, VerifyJob


def _image_ready() -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", "uxie-sandbox:dev"], capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


pytestmark = pytest.mark.skipif(not _image_ready(), reason="docker image uxie-sandbox:dev missing")

GOOD = "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"
BUGGY = "def clamp(x, lo, hi):\n    return max(lo, min(x, lo))\n"  # regression introduced by "PR"


@pytest.fixture
def snaps(tmp_path):
    base, head = tmp_path / "base", tmp_path / "head"
    for d, src in ((base, GOOD), (head, BUGGY)):
        (d / "lib").mkdir(parents=True)
        (d / "lib" / "__init__.py").write_text("")
        (d / "lib" / "mathx.py").write_text(src)
    (head / "lib" / "fresh.py").write_text("def pages(n, size):\n    return n // size\n")
    return base, head


def _verify(snaps, tmp_path, code, name="j"):
    job = VerifyJob(name, "o/r", "b", "h", "tests/test_uxie_verify.py", code, timeout_s=20)
    return verify(job, snaps[0], snaps[1], tmp_path / name, DockerExecutor())


def test_regression_is_confirmed(snaps, tmp_path):
    v = _verify(
        snaps,
        tmp_path,
        "from lib.mathx import clamp\ndef test_t():\n    assert clamp(5, 0, 10) == 5\n",
    )
    assert v.verdict == CONFIRMED and v.base and v.base.outcome == "passed"


def test_new_code_bug_is_likely(snaps, tmp_path):
    v = _verify(
        snaps,
        tmp_path,
        "from lib.fresh import pages\ndef test_t():\n    assert pages(11, 10) == 2\n",
    )
    assert v.verdict == LIKELY and v.base and v.base.outcome == "error"


def test_false_claim_is_refuted(snaps, tmp_path):
    v = _verify(
        snaps,
        tmp_path,
        "from lib.mathx import clamp\ndef test_t():\n    assert clamp(-5, 0, 10) == 0\n",
    )
    assert v.verdict == REFUTED and v.base is None


@pytest.mark.parametrize(
    "code",
    [
        "def test_t():\n    open('/src/pwned', 'w').write('x')\n",
        "import urllib.request\n"
        "def test_t():\n    urllib.request.urlopen('http://1.1.1.1', timeout=3)\n",
        "import os\ndef test_t():\n    assert os.getuid() == 0\n",
    ],
    ids=["write-source", "network", "root"],
)
def test_escapes_are_blocked(snaps, tmp_path, code):
    v = _verify(snaps, tmp_path, code)
    assert v.head.outcome == "failed", v.head.output_tail  # every attempt fails inside the box
    assert not (snaps[1] / "pwned").exists()


def test_infinite_loop_times_out(snaps, tmp_path):
    job = VerifyJob(
        "t",
        "o/r",
        "b",
        "h",
        "tests/test_uxie_verify.py",
        "def test_t():\n    while True:\n        pass\n",
        timeout_s=3,
    )
    v = verify(job, snaps[0], snaps[1], tmp_path / "t", DockerExecutor())
    assert v.head.outcome == "timeout"
