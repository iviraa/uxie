"""GitHub App auth: signed JWT -> short-lived installation access token."""

from __future__ import annotations

import time

import httpx
import jwt

GITHUB_API = "https://api.github.com"

_token_cache: dict[int, tuple[str, float]] = {}


def app_jwt(app_id: str, private_key: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    # iat backdated 60s for clock skew; GitHub caps exp at 10 minutes
    payload = {"iat": int(now) - 60, "exp": int(now) + 540, "iss": app_id}
    return jwt.encode(payload, private_key, algorithm="RS256")


def installation_token(app_id: str, private_key: str, installation_id: int) -> str:
    cached = _token_cache.get(installation_id)
    if cached and cached[1] - time.time() > 300:
        return cached[0]
    resp = httpx.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt(app_id, private_key)}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    token = resp.json()["token"]
    _token_cache[installation_id] = (token, time.time() + 3600)  # tokens live 1h
    return str(token)
