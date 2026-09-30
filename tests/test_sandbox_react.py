"""React component bugs proven in the TS sandbox (real React, happy-dom, Testing Library)."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from uxie.sandbox.envs import NodeDeps
from uxie.sandbox.executor import DockerExecutor, verify
from uxie.sandbox.languages import TYPESCRIPT
from uxie.sandbox.spec import CONFIRMED, VerifyJob

JOBS = Path.home() / ".uxie" / "jobs"
pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["docker", "image", "inspect", "uxie-sandbox-ts:dev"], capture_output=True
    ).returncode
    != 0,
    reason="uxie-sandbox-ts:dev image missing",
)

COUNTER = """import React, {{ useState }} from "react";

export function Counter() {{
  const [count, setCount] = useState(0);
  const addTwo = () => {{
    {body}
  }};
  return <button onClick={{addTwo}}>count: {{count}}</button>;
}}
"""
GOOD = "setCount((c) => c + 1);\n    setCount((c) => c + 1);"
STALE = "setCount(count + 1);\n    setCount(count + 1);  // reads the stale value twice"

TEST = """import React from "react";
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { Counter } from "./Counter";

afterEach(() => cleanup());

test("one click adds two", () => {
  render(<Counter />);
  fireEvent.click(screen.getByRole("button"));
  assert.equal(screen.getByRole("button").textContent, "count: 2");
});
"""


def _project(root: Path, body: str) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name": "ui", "type": "module", "dependencies": '
        '{"react": "18.3.1", "react-dom": "18.3.1"}}\n'
    )
    (root / "tsconfig.json").write_text('{"compilerOptions": {"jsx": "react-jsx"}}\n')
    (root / "src" / "Counter.tsx").write_text(COUNTER.format(body=body))
    return root


def test_stale_state_bug_is_confirmed():
    work = JOBS / f"react-{uuid.uuid4().hex[:8]}"
    try:
        base, head = _project(work / "base", GOOD), _project(work / "head", STALE)
        for p in (base, head):  # trusted setup: a real lockfile, like a real repo
            r = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{p}:/p",
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
            assert r.returncode == 0, r.stderr
        env = NodeDeps(work / "envs", min_free_bytes=2 * 1024**3).ensure(
            head, target="src/Counter.tsx"
        )
        assert env is not None, list((work / "envs").glob("*.failed"))
        assert (env / "mod" / "node_modules" / "@testing-library" / "react").is_dir()
        job = VerifyJob("r", "", "", "", TYPESCRIPT.test_path("src/Counter.tsx", head), TEST, 90)
        v = verify(job, base, head, work / "w", DockerExecutor(), env, TYPESCRIPT)
        assert v.verdict == CONFIRMED, (v.head.failure, v.head.output_tail[-1500:])
        assert "count: 1" in v.head.failure or "count: 1" in v.head.output_tail
    finally:
        shutil.rmtree(work, ignore_errors=True)
