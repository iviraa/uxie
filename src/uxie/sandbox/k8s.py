"""Executor that runs each test as a locked-down Kubernetes Job."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from uxie.sandbox.spec import ERROR, TIMEOUT, RunOutcome

ROLE_LABEL = {"uxie.dev/role": "sandbox"}


def job_manifest(
    name: str,
    sub_path: str,
    test_path: str,
    timeout_s: int,
    image: str,
    pull: str = "IfNotPresent",
    env_sub_path: str | None = None,
    target_file: str | None = None,
) -> dict[str, Any]:
    """Pure function so the security posture is unit-testable without a cluster."""
    env_vars = [{"name": "TEST_TIMEOUT", "value": str(timeout_s)}]
    if target_file:
        env_vars.append({"name": "UXIE_TARGET_FILE", "value": target_file})
    mounts: list[dict[str, Any]] = [
        {"name": "src", "mountPath": "/src", "subPath": sub_path, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
    ]
    if env_sub_path:  # dependency env from the same volume, read-only
        env_vars.append({"name": "UXIE_DEPS", "value": "/env/site"})  # see sitecustomize.py
        mounts.append(
            {"name": "src", "mountPath": "/env", "subPath": env_sub_path, "readOnly": True}
        )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": ROLE_LABEL},
        "spec": {
            "backoffLimit": 0,  # a flaky retry could flip a verdict; one shot only
            "activeDeadlineSeconds": timeout_s + 30,
            "ttlSecondsAfterFinished": 300,
            "template": {
                "metadata": {"labels": ROLE_LABEL},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": "sandbox",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "runner",
                            "image": image,
                            "imagePullPolicy": pull,
                            "args": [test_path],
                            "env": env_vars,
                            "resources": {
                                "limits": {
                                    "cpu": "1",
                                    "memory": "512Mi",
                                    "ephemeral-storage": "100Mi",
                                },
                                "requests": {"cpu": "250m", "memory": "128Mi"},
                            },
                            "securityContext": {
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": mounts,
                        }
                    ],
                    "volumes": [
                        {
                            "name": "src",
                            "persistentVolumeClaim": {"claimName": "jobs", "readOnly": True},
                        },
                        {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
                    ],
                },
            },
        },
    }


def load_config() -> None:
    """In a pod use the service account; on a laptop use ~/.kube/config."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


class JobRunner:
    """Create a Job, wait for it, return (state, logs), and always clean it up."""

    def __init__(self, namespace: str) -> None:
        self.ns = namespace
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()

    def run(self, body: dict[str, Any], budget_s: int) -> tuple[str, str]:
        name = body["metadata"]["name"]
        self._batch.create_namespaced_job(self.ns, body)
        try:
            deadline = time.monotonic() + budget_s
            while time.monotonic() < deadline:
                status = self._batch.read_namespaced_job_status(name, self.ns).status
                if status.succeeded or status.failed:
                    return ("failed" if status.failed else "succeeded"), self._logs(name)
                time.sleep(0.5)
            return "timeout", ""
        finally:
            self._batch.delete_namespaced_job(
                name, self.ns, propagation_policy="Background", grace_period_seconds=0
            )

    def _logs(self, name: str) -> str:
        pods = self._core.list_namespaced_pod(self.ns, label_selector=f"job-name={name}").items
        if not pods:
            return ""
        try:
            # raw bytes: the client would otherwise parse JSON logs and return str(dict)
            resp = self._core.read_namespaced_pod_log(
                pods[0].metadata.name, self.ns, _preload_content=False
            )
            return str(resp.data.decode(errors="replace"))
        except ApiException:
            return ""


class K8sExecutor:
    """host_root is where this process sees the shared "jobs" volume (see deploy/k3d.sh)."""

    def __init__(
        self,
        host_root: Path,
        namespace: str = "uxie-sandbox",
        image: str = "uxie-sandbox:dev",
    ) -> None:
        load_config()
        self._jobs = JobRunner(namespace)
        self.host_root = host_root.resolve()
        self.image = image

    def run(
        self,
        src: Path,
        test_path: str,
        timeout_s: int,
        env: Path | None = None,
        target_file: str | None = None,
    ) -> RunOutcome:
        rel = src.resolve().relative_to(self.host_root)  # raises if outside the shared mount
        env_rel = str(env.resolve().relative_to(self.host_root)) if env else None
        body = job_manifest(
            f"verify-{uuid.uuid4().hex[:10]}",
            str(rel),
            test_path,
            timeout_s,
            self.image,
            env_sub_path=env_rel,
            target_file=target_file,
        )
        state, logs = self._jobs.run(body, timeout_s + 60)
        if state == "timeout":
            return RunOutcome(TIMEOUT, -1, float(timeout_s + 60), "job did not finish")
        try:
            return RunOutcome(**json.loads(logs.strip().splitlines()[-1]))
        except (IndexError, json.JSONDecodeError, TypeError) as e:
            if state == "failed":
                return RunOutcome(TIMEOUT, -1, 0.0, "deadline exceeded")
            return RunOutcome(ERROR, -1, 0.0, str(e)[:500])


def build_manifest(name: str, env_sub_path: str, image: str, command: list[str]) -> dict[str, Any]:
    """Dependency install Job: has network (unlike the sandbox), but no privileges or token."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": {"uxie.dev/role": "env-builder"}},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 900,
            "ttlSecondsAfterFinished": 300,
            "template": {
                "metadata": {"labels": {"uxie.dev/role": "env-builder"}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "uv",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": command,
                            "env": [
                                {"name": "HOME", "value": "/tmp"},
                                {"name": "UV_CACHE_DIR", "value": "/tmp/uv-cache"},
                            ],
                            "resources": {
                                "limits": {"cpu": "2", "memory": "2Gi"},
                                "requests": {"cpu": "250m", "memory": "256Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "jobs", "mountPath": "/env", "subPath": env_sub_path},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "jobs", "persistentVolumeClaim": {"claimName": "jobs"}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "2Gi"}},
                    ],
                },
            },
        },
    }


class K8sBuildRunner:
    """EnvBuilder runner that installs dependencies in a Job instead of `docker run`."""

    def __init__(self, host_root: Path, namespace: str = "uxie-system") -> None:
        load_config()
        self._jobs = JobRunner(namespace)
        self.host_root = host_root.resolve()

    def __call__(self, env: Path, image: str, command: list[str]) -> tuple[int, str]:
        rel = str(env.resolve().relative_to(self.host_root))
        body = build_manifest(f"env-{uuid.uuid4().hex[:10]}", rel, image, command)
        state, logs = self._jobs.run(body, 960)
        return (0 if state == "succeeded" else 1), logs
