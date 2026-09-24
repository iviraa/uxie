"""GitHub REST client for the PR endpoints the reviewer needs."""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
USER_AGENT = "uxie/0.1"
MAX_TARBALL_BYTES = 200 * 1024 * 1024


class GitHubClient:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_pr(self, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{repo}/pulls/{number}").json()

    def list_pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        url: str | None = f"/repos/{repo}/pulls/{number}/files?per_page=100"
        while url:
            resp = self._request("GET", url)
            out.extend(resp.json())
            url = _next_link(resp.headers.get("Link"))
        return out

    def download_snapshot(self, repo: str, sha: str, dest: Path) -> Path:
        """Extract the repo tarball at sha into dest; returns the snapshot root."""
        resp = self._request("GET", f"/repos/{repo}/tarball/{sha}")
        if len(resp.content) > MAX_TARBALL_BYTES:
            raise RuntimeError(f"tarball too large: {len(resp.content)} bytes")
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            tar.extractall(dest, filter="data")  # "data" filter blocks path traversal
        roots = [p for p in dest.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("unexpected tarball layout")
        return roots[0]

    def create_review(
        self, repo: str, number: int, commit_id: str, body: str, comments: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = {"commit_id": commit_id, "event": "COMMENT", "body": body, "comments": comments}
        return self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload).json()

    def add_reaction(self, repo: str, comment_id: int, content: str) -> None:
        self._request(
            "POST",
            f"/repos/{repo}/issues/comments/{comment_id}/reactions",
            json={"content": content},
        )

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        for attempt in range(4):
            resp = self._client.request(method, url, **kw)
            if resp.status_code < 400:
                return resp
            if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 30))
                time.sleep(min(max(0, reset - int(time.time())) + 1, 60))
                continue
            if resp.status_code >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"exhausted retries for {method} {url}")


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if section.endswith('rel="next"'):
            return section.split(";", 1)[0].strip().strip("<>")
    return None
