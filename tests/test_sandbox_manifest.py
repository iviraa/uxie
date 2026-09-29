from uxie.sandbox.k8s import ROLE_LABEL, job_manifest


def test_job_is_locked_down():
    m = job_manifest("verify-x", "a/head", "tests/t.py", 30, "img:dev")
    pod = m["spec"]["template"]["spec"]
    (c,) = pod["containers"]
    assert m["spec"]["backoffLimit"] == 0
    assert m["spec"]["template"]["metadata"]["labels"] == ROLE_LABEL  # NetworkPolicy selector
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert c["securityContext"] == {
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    src = next(v for v in c["volumeMounts"] if v["name"] == "src")
    assert src == {"name": "src", "mountPath": "/src", "subPath": "a/head", "readOnly": True}
    assert all("hostPath" not in v for v in pod["volumes"])  # forbidden under "restricted"
    assert set(c["resources"]["limits"]) == {"cpu", "memory", "ephemeral-storage"}
