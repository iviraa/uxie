"""Run a verify job's test against base and head checkouts in a locked-down container."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from uxie.sandbox.spec import ERROR, TIMEOUT, RunOutcome, Verdict, VerifyJob, decide

HARDENING = [
    "--network",
    "none",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,size=64m",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "128",
    "--memory",
    "512m",
    "--cpus",
    "1",
    "--user",
    "10001:10001",
]


class Executor(Protocol):
    def run(
        self,
        src: Path,
        test_path: str,
        timeout_s: int,
        env: Path | None = None,
        target_file: str | None = None,
    ) -> RunOutcome: ...


class DockerExecutor:
    def __init__(self, image: str = "uxie-sandbox:dev") -> None:
        self.image = image

    def run(
        self,
        src: Path,
        test_path: str,
        timeout_s: int,
        env: Path | None = None,
        target_file: str | None = None,
    ) -> RunOutcome:
        deps = ["-v", f"{env.resolve()}:/env:ro", "-e", "UXIE_DEPS=/env/site"] if env else []
        if target_file:
            deps += ["-e", f"UXIE_TARGET_FILE={target_file}"]
        cmd = [
            "docker",
            "run",
            "--rm",
            *HARDENING,
            *deps,
            "-e",
            f"TEST_TIMEOUT={timeout_s}",
            "-v",
            f"{src.resolve()}:/src:ro",
            self.image,
            test_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 30)
        except subprocess.TimeoutExpired:
            return RunOutcome(TIMEOUT, -1, float(timeout_s + 30), "container wall-clock exceeded")
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            return RunOutcome(**data)
        except (IndexError, json.JSONDecodeError, TypeError):
            return RunOutcome(ERROR, proc.returncode, 0.0, (proc.stdout + proc.stderr)[-3000:])


def stage(snapshot: Path, job: VerifyJob, dest: Path) -> Path:
    """Copy a snapshot and drop the generated test into it."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(snapshot, dest, symlinks=True)
    target = (dest / job.test_path).resolve()
    if dest.resolve() not in target.parents:
        raise ValueError(f"test_path escapes checkout: {job.test_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(job.test_code)
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
