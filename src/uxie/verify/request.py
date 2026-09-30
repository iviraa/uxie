"""Verify job message: one review finding to prove, carried on the verify queue."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerifyRequest:
    repo: str
    pr_number: int
    installation_id: int
    base_sha: str
    head_sha: str
    title: str
    body: str
    finding: dict[str, Any] = field(default_factory=dict)
    comment_id: int | None = None

    @property
    def fingerprint(self) -> str:
        f = self.finding
        return str(f.get("fingerprint") or f"{f.get('path')}:{f.get('line')}")

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> VerifyRequest:
        return VerifyRequest(**json.loads(raw))
