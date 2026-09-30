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


def test_env_mount_is_read_only():
    m = job_manifest("v", "a/head", "t.py", 30, "img", env_sub_path="envs/abc")
    (c,) = m["spec"]["template"]["spec"]["containers"]
    env = next(v for v in c["volumeMounts"] if v["mountPath"] == "/env")
    assert env == {"name": "src", "mountPath": "/env", "subPath": "envs/abc", "readOnly": True}
    assert {"name": "UXIE_DEPS", "value": "/env/site"} in c["env"]


def test_build_job_has_no_privileges_or_token():
    from uxie.sandbox.k8s import build_manifest

    m = build_manifest("env-x", "envs/k.tmp", "uv:img", ["uv", "pip", "install"])
    pod = m["spec"]["template"]["spec"]
    (c,) = pod["containers"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert c["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert {"name": "jobs", "mountPath": "/env", "subPath": "envs/k.tmp"} in c["volumeMounts"]
    assert all("hostPath" not in v for v in pod["volumes"])
