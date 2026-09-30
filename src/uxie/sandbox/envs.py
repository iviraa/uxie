"""Dependency environments for the sandbox: built once per dependency set, mounted read-only.

Security: the builder has network but installs wheels only (--no-build), so no package code
runs at install time. The PR's own project is never installed; tests import it from /src.
"""

from __future__ import annotations

import ast
import contextlib
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

BUILDER_IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
SITE = "site"  # packages land in <env>/site, mounted at /env/site in the sandbox
MIN_FREE_BYTES = 15 * 1024**3  # refuse to build when the disk is this close to full
MAX_ENV_BYTES = 1 * 1024**3  # an env bigger than this is deleted and marked failed
# multi-GB ML stacks: a unit test can't meaningfully exercise them in a 512Mi sandbox
HEAVY = {
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "tensorflow-cpu",
    "jax",
    "jaxlib",
    "mujoco",
    "gymnasium",
    "stable-baselines3",
    "loco-mujoco",
    "tensorboard",
    "transformers",
    "nvidia-cudnn-cu12",
    "triton",
    "opencv-python",
    "imageio-ffmpeg",
}
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _req_name(req: str) -> str:
    m = _NAME_RE.match(req)
    return _norm(m.group(1)) if m else ""


def _poetry(deps: dict[str, object], optional: bool = False) -> list[str]:
    """Poetry deps; `optional` selects the ones marked optional (extras) instead."""
    out = []
    for name, spec in deps.items():
        if name.lower() == "python":
            continue
        if (isinstance(spec, dict) and bool(spec.get("optional"))) != optional:
            continue
        version = spec.get("version", "") if isinstance(spec, dict) else str(spec)
        if isinstance(spec, dict) and ("path" in spec or "git" in spec):
            continue
        version = str(version).strip()
        if version.startswith(("^", "~")):
            version = ">=" + version[1:].lstrip("=")  # close enough for resolution
        out.append(name + ("" if version in ("", "*") else version))
    return out


def _literal_strs(node: ast.AST | None, names: Mapping[str, ast.AST]) -> list[str]:
    """String list from a literal, or a module-level name bound to one. Never executes code."""
    if isinstance(node, ast.Name):
        node = names.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.List | ast.Tuple):
        return [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _setup_py(path: Path) -> tuple[list[str], dict[str, list[str]], str]:
    """(install_requires, extras_require, name) read statically from setup.py's AST."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return [], {}, ""
    names = {
        t.id: n.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and getattr(call.func, "id", getattr(call.func, "attr", "")) == "setup"
        ):
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        extras: dict[str, list[str]] = {}
        ex = kw.get("extras_require")
        if isinstance(ex, ast.Name):
            ex = names.get(ex.id)
        if isinstance(ex, ast.Dict):
            for k, v in zip(ex.keys, ex.values, strict=False):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    extras[k.value] = _literal_strs(v, names)
        name = _literal_strs(kw.get("name"), names)
        return _literal_strs(kw.get("install_requires"), names), extras, name[0] if name else ""
    return [], {}, ""


def _requirements_file(path: Path, root: Path, seen: set[Path]) -> list[str]:
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    out: list[str] = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            nested = (path.parent / line.split(maxsplit=1)[1]).resolve()
            if root in nested.parents:
                out += _requirements_file(nested, root, seen)
            continue
        if line.startswith(("-", ".", "/")) or "://" in line or "git+" in line:
            continue  # editable/local/VCS installs would build the repo or run code
        out.append(line)
    return out


def requirements(root: Path) -> tuple[list[str], dict[str, list[str]]]:
    """(core requirements, optional groups) declared by the repo, minus the repo itself."""
    root = root.resolve()
    core: list[str] = []
    groups: dict[str, list[str]] = {}
    own: set[str] = set()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project", {})
        own.add(_norm(str(project.get("name", ""))))
        core += [d for d in project.get("dependencies", []) if isinstance(d, str)]
        for g, deps in project.get("optional-dependencies", {}).items():
            groups[f"extra:{g}"] = [d for d in deps if isinstance(d, str)]
        for g, deps in data.get("dependency-groups", {}).items():
            groups[f"group:{g}"] = [d for d in deps if isinstance(d, str)]
        poetry = data.get("tool", {}).get("poetry", {})
        if poetry:
            own.add(_norm(str(poetry.get("name", ""))))
            core += _poetry(poetry.get("dependencies", {}))
            extras = _poetry(poetry.get("dependencies", {}), optional=True)
            if extras:
                groups["poetry:extras"] = extras
            dev = poetry.get("dev-dependencies") or poetry.get("group", {}).get("dev", {}).get(
                "dependencies", {}
            )
            if dev:
                groups["poetry:dev"] = _poetry(dev)
    setup_py = root / "setup.py"
    if setup_py.is_file() and not core:  # legacy packaging; parsed, never run
        reqs, setup_extras, name = _setup_py(setup_py)
        core += reqs
        own.add(_norm(name))
        for g, deps in setup_extras.items():
            groups[f"extra:{g}"] = deps
    # requirements files are core only when pyproject declares nothing (often they pin dev tools)
    declared = bool(core)
    for req in sorted(root.glob("requirements*.txt"), key=lambda p: p.name != "requirements.txt"):
        lines = _requirements_file(req, root, set())  # fresh set: only guards -r cycles
        if req.name == "requirements.txt" and not declared:
            core += lines
        elif lines:
            groups[f"file:{req.name}"] = lines

    def clean(reqs: list[str]) -> list[str]:
        return sorted({r for r in reqs if _req_name(r) and _req_name(r) not in own})

    return clean(core), {g: clean(r) for g, r in sorted(groups.items()) if clean(r)}


def heavy(reqs: list[str]) -> list[str]:
    return sorted({_req_name(r) for r in reqs} & HEAVY)


def _tree_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def env_key(core: list[str], groups: dict[str, list[str]], exclude_newer: str | None) -> str:
    blob = json.dumps(
        {"image": BUILDER_IMAGE, "core": core, "groups": groups, "exclude_newer": exclude_newer},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # concurrent workers wait for one builder
        yield


# (env dir mounted at /env, image, command) -> (exit code, output)
BuildRunner = Callable[[Path, str, list[str]], tuple[int, str]]


def docker_runner(env: Path, image: str, command: list[str]) -> tuple[int, str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "2g",
        "-v",
        f"{env.resolve()}:/env",
        image,
        *command,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


class EnvBuilder:
    def __init__(
        self, envs_root: Path, image: str = BUILDER_IMAGE, runner: BuildRunner | None = None
    ) -> None:
        self.root, self.image = envs_root, image
        self.runner = runner or docker_runner

    def ensure(self, repo_root: Path, exclude_newer: str | None = None) -> Path | None:
        """Env dir for this repo's dependencies, or None if it has none or the build failed."""
        core, groups = requirements(repo_root)
        if heavy(core):
            return None  # tests will error on import -> inconclusive, which is honest
        groups = {g: r for g, r in groups.items() if not heavy(r)}
        if not core and not groups:
            return None
        key = env_key(core, groups, exclude_newer)
        env = self.root / key
        with _locked(self.root / f"{key}.lock"):
            if (env / "ok").exists():
                return env
            if (self.root / f"{key}.failed").exists():
                return None  # don't hammer PyPI rebuilding a known-bad set
            return self._build(env, core, groups, exclude_newer)

    def _build(
        self, env: Path, core: list[str], groups: dict[str, list[str]], exclude_newer: str | None
    ) -> Path | None:
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < MIN_FREE_BYTES:
            raise RuntimeError(f"refusing env build: only {free / 1024**3:.1f} GiB free")
        tmp = env.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        log: list[str] = []
        ok = self._install(tmp, "core", core, exclude_newer, log) if core else True
        if not ok:
            (self.root / f"{env.name}.failed").write_text("\n".join(log)[-20000:])
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        for name, reqs in groups.items():  # best effort: extras often need things we skip
            self._install(tmp, name, reqs, exclude_newer, log)
        size = _tree_bytes(tmp)
        if size > MAX_ENV_BYTES:
            log.append(f"env too large: {size / 1024**2:.0f} MiB")
            (self.root / f"{env.name}.failed").write_text("\n".join(log)[-20000:])
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        (tmp / "build.log").write_text("\n".join(log)[-50000:])
        (tmp / "ok").write_text(json.dumps({"core": core, "groups": list(groups)}))
        shutil.rmtree(env, ignore_errors=True)
        tmp.rename(env)
        return env

    def _install(
        self, env: Path, name: str, reqs: list[str], exclude_newer: str | None, log: list[str]
    ) -> bool:
        req_file = env / f"req-{re.sub(r'[^a-z0-9]+', '-', name.lower())}.txt"
        req_file.write_text("\n".join(reqs) + "\n")
        uv = [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            "python3.12",
            "--target",
            f"/env/{SITE}",
            "--no-build",
            "-r",
            f"/env/{req_file.name}",
        ]
        if exclude_newer:
            uv += ["--exclude-newer", exclude_newer]
        code, output = self.runner(env, self.image, uv)
        log.append(f"== {name} rc={code}\n{output}")
        return code == 0
