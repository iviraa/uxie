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
) -> dict[str, Any]:
    """Pure function so the security posture is unit-testable without a cluster."""
    env_vars = [{"name": "TEST_TIMEOUT", "value": str(timeout_s)}]
    mounts: list[dict[str, Any]] = [
        {"name": "src", "mountPath": "/src", "subPath": sub_path, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
    ]
    if env_sub_path:  # dependency env from the same volume, read-only
        env_vars.append({"name": "PYTHONPATH", "value": "/env/site"})
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


class K8sExecutor:
    """host_root on this machine backs the chart's "jobs" volume (see deploy/k3d.sh)."""

    def __init__(
        self,
        host_root: Path,
        namespace: str = "uxie-sandbox",
        image: str = "uxie-sandbox:dev",
    ) -> None:
        config.load_kube_config()
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()
        self.host_root = host_root.resolve()
        self.ns, self.image = namespace, image

    def run(self, src: Path, test_path: str, timeout_s: int, env: Path | None = None) -> RunOutcome:
        rel = src.resolve().relative_to(self.host_root)  # raises if outside the shared mount
        env_rel = str(env.resolve().relative_to(self.host_root)) if env else None
        name = f"verify-{uuid.uuid4().hex[:10]}"
        body = job_manifest(name, str(rel), test_path, timeout_s, self.image, env_sub_path=env_rel)
        self._batch.create_namespaced_job(self.ns, body)
        try:
            return self._wait(name, timeout_s + 60)
        finally:
            self._batch.delete_namespaced_job(
                name, self.ns, propagation_policy="Background", grace_period_seconds=0
            )

    def _wait(self, name: str, budget_s: int) -> RunOutcome:
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            status = self._batch.read_namespaced_job_status(name, self.ns).status
            if status.succeeded or status.failed:
                return self._result(name, bool(status.failed))
            time.sleep(0.5)
        return RunOutcome(TIMEOUT, -1, float(budget_s), "job did not finish")

    def _result(self, name: str, job_failed: bool) -> RunOutcome:
        pods = self._core.list_namespaced_pod(self.ns, label_selector=f"job-name={name}").items
        if not pods:
            return RunOutcome(ERROR, -1, 0.0, "no pod for job")
        try:
            # raw bytes: the client would otherwise parse JSON logs and return str(dict)
            resp = self._core.read_namespaced_pod_log(
                pods[0].metadata.name, self.ns, _preload_content=False
            )
            logs = resp.data.decode(errors="replace")
            return RunOutcome(**json.loads(logs.strip().splitlines()[-1]))
        except (ApiException, IndexError, json.JSONDecodeError, TypeError) as e:
            reason = "deadline exceeded" if job_failed else str(e)
            return RunOutcome(TIMEOUT if job_failed else ERROR, -1, 0.0, reason[:500])
