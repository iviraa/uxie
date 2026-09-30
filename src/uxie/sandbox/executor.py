"""Run a verify job's test against base and head checkouts in a locked-down container."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from uxie.sandbox.languages import PYTHON, Language
from uxie.sandbox.spec import ERROR, TIMEOUT, RunOutcome, Verdict, VerifyJob, decide


def hardening(lang: Language, scratch: Path | None) -> list[str]:
    """docker run flags: no network, read-only root, non-root, capped resources."""
    tmp = (
        ["--tmpfs", f"/tmp:rw,size={_docker_size(lang.tmp_size)}"]
        if lang.tmp_in_memory or scratch is None
        else ["-v", f"{scratch}:/tmp"]  # compilers need real disk, not RAM, for build caches
    )
    return [
        "--network",
        "none",
        "--read-only",
        *tmp,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(lang.pids),
        "--memory",
        _docker_size(lang.memory),
        "--cpus",
        "1",
        "--user",
        "10001:10001",
    ]


def _docker_size(k8s: str) -> str:
    return k8s.replace("Gi", "g").replace("Mi", "m")


class Executor(Protocol):
    def run(
        self,
        src: Path,
        test_path: str,
        timeout_s: int,
        env: Path | None = None,
        target_file: str | None = None,
        lang: Language = PYTHON,
    ) -> RunOutcome: ...


class DockerExecutor:
    def __init__(self, image: str | None = None) -> None:
        self.image = image  # override the language's image (tests); None = per language

    def run(
        self,
        src: Path,
        test_path: str,
        timeout_s: int,
        env: Path | None = None,
        target_file: str | None = None,
        lang: Language = PYTHON,
    ) -> RunOutcome:
        deps: list[str] = []
        if env:
            deps += ["-v", f"{env.resolve()}:/env:ro"]
            for k, v in lang.deps_env("/env").items():
                deps += ["-e", f"{k}={v}"]
        if target_file:
            deps += ["-e", f"UXIE_TARGET_FILE={target_file}"]
        scratch = None if lang.tmp_in_memory else Path(tempfile.mkdtemp(prefix="uxie-tmp-"))
        if scratch:
            scratch.chmod(0o777)  # the container runs as uid 10001
        cmd = [
            "docker",
            "run",
            "--rm",
            *hardening(lang, scratch),
            *deps,
            "-e",
            f"TEST_TIMEOUT={timeout_s}",
            "-e",
            f"UXIE_LANG={lang.name}",
            "-v",
            f"{src.resolve()}:/src:ro",
            self.image or lang.image,
            test_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 60)
        except subprocess.TimeoutExpired:
            return RunOutcome(TIMEOUT, -1, float(timeout_s + 60), "container wall-clock exceeded")
        finally:
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            return RunOutcome(**data)
        except (IndexError, json.JSONDecodeError, TypeError):
            return RunOutcome(ERROR, proc.returncode, 0.0, (proc.stdout + proc.stderr)[-3000:])


def stage(
    snapshot: Path, job: VerifyJob, dest: Path, lang: Language = PYTHON, has_deps: bool = False
) -> Path:
    """Copy a snapshot, apply the language's staging hook, and drop the generated test in."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(snapshot, dest, symlinks=True)
    target = (dest / job.test_path).resolve()
    if dest.resolve() not in target.parents:
        raise ValueError(f"test_path escapes checkout: {job.test_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(job.test_code)
    lang.prepare(dest, has_deps)
    return dest


def verify(
    job: VerifyJob,
    base_snapshot: Path | None,
    head_snapshot: Path,
    workdir: Path,
    ex: Executor,
    env: Path | None = None,
) -> Verdict:
    head = ex.run(stage(head_snapshot, job, workdir / "head"), job.test_path, job.timeout_s, env)
    base = None
    if head.outcome == "failed" and base_snapshot is not None:  # base only matters if head fails
        base = ex.run(
            stage(base_snapshot, job, workdir / "base"), job.test_path, job.timeout_s, env
        )
    return Verdict(job.job_id, decide(head, base), head, base)
