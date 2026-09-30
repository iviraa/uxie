from pathlib import Path

import httpx
import pytest

from tests.conftest import PATCH, FakeModel, tool_turn
from uxie import pipeline
from uxie.github.webhook import ReviewJob

FINDING = {
    "path": "a.py",
    "line": 4,
    "severity": "high",
    "category": "bug",
    "title": "t",
    "explanation": "e",
    "confidence": 0.9,
}


class FakeGH:
    def __init__(self, head="abc", draft=False, patch=PATCH, reject_first=False):
        self.head, self.draft, self.patch, self.reject_first = head, draft, patch, reject_first
        self.reviews: list[dict] = []
        self.inline: list[dict] = []  # posted review comments, as GitHub would list them

    def get_pr(self, repo, n):
        return {
            "base": {"sha": "base1"},
            "head": {"sha": self.head},
            "draft": self.draft,
            "state": "open",
            "title": "t",
            "body": "",
        }

    def list_pr_files(self, repo, n):
        return [
            {"filename": "a.py", "status": "modified", "patch": self.patch},
            {"filename": "logo.png", "status": "added"},
        ]

    def download_snapshot(self, repo, sha, dest: Path):
        root = dest / "o-r-abc"
        root.mkdir(parents=True)
        (root / "a.py").write_text("x = 1\n")
        return root

    def create_review(self, repo, n, sha, body, comments):
        if self.reject_first and comments:
            self.reject_first = False
            req = httpx.Request("POST", "https://x")
            raise httpx.HTTPStatusError(
                "422", request=req, response=httpx.Response(422, request=req)
            )
        self.reviews.append({"sha": sha, "body": body, "comments": comments})
        self.inline += comments
        return {"id": 555}

    def list_review_comments(self, repo, n):
        return self.inline

    def list_reviews(self, repo, n):
        return self.reviews

    def list_comments_for_review(self, repo, n, review_id):
        return [{"id": 1000 + i, "body": c["body"]} for i, c in enumerate(self.inline)]


class FakeStore:
    def __init__(self, claimable=True):
        self.claimable = claimable
        self.finished: list[tuple[str, dict]] = []
        self.forced: list[bool] = []

    def claim(self, repo, pr, sha, force=False):
        self.forced.append(force)
        return self.claimable

    def finish(self, repo, pr, sha, status, **fields):
        self.finished.append((status, fields))


def _job(sha="abc", trigger="opened"):
    return ReviewJob("o/r", 3, 7, sha, trigger)


def _model():
    return FakeModel([tool_turn("submit_review", {"summary": "s", "findings": [FINDING]})])


def test_happy_path_posts_inline(tmp_path, cfg):
    gh, store = FakeGH(), FakeStore()
    assert pipeline.run(_job(), gh, _model(), cfg, store, None, tmp_path / "w") == "done"
    (rev,) = gh.reviews
    assert rev["comments"][0]["path"] == "a.py" and rev["comments"][0]["line"] == 4
    assert store.finished[0][0] == "done" and store.finished[0][1]["review_id"] == 555
    assert not (tmp_path / "w").exists()  # snapshot cleaned up


def test_stale_job_skipped_before_claim(tmp_path, cfg):
    store = FakeStore()
    assert pipeline.run(_job(sha="old"), FakeGH(), _model(), cfg, store, None, tmp_path) == "stale"
    assert store.forced == []


def test_duplicate_delivery(tmp_path, cfg):
    gh = FakeGH()
    status = pipeline.run(_job(), gh, _model(), cfg, FakeStore(claimable=False), None, tmp_path)
    assert status == "duplicate" and gh.reviews == []


def test_command_forces_and_resolves_head(tmp_path, cfg):
    store = FakeStore()
    assert (
        pipeline.run(
            _job(sha=None, trigger="command"), FakeGH(), _model(), cfg, store, None, tmp_path / "w"
        )
        == "done"
    )
    assert store.forced == [True]


def test_large_diff_skipped(tmp_path, cfg):
    from dataclasses import replace

    store = FakeStore()
    status = pipeline.run(
        _job(), FakeGH(), _model(), replace(cfg, max_changed_lines=2), store, None, tmp_path
    )
    assert status == "skipped" and "too large" in store.finished[0][1]["reason"]


def test_rejected_anchor_falls_back_to_body(tmp_path, cfg):
    gh = FakeGH(reject_first=True)
    assert pipeline.run(_job(), gh, _model(), cfg, FakeStore(), None, tmp_path / "w") == "done"
    (rev,) = gh.reviews
    assert rev["comments"] == [] and "`a.py:4`" in rev["body"]


def test_failure_marks_failed_and_reraises(tmp_path, cfg):
    store = FakeStore()
    broken = FakeModel([])  # pop from empty list
    with pytest.raises(IndexError):
        pipeline.run(_job(), FakeGH(), broken, cfg, store, None, tmp_path / "w")
    assert store.finished[-1][0] == "failed"


def _run(gh, cfg, tmp_path, trigger="opened"):
    return pipeline.run(_job(trigger=trigger), gh, _model(), cfg, FakeStore(), None, tmp_path / "w")


def test_rereview_does_not_repost_same_finding(tmp_path, cfg):
    gh = FakeGH()
    _run(gh, cfg, tmp_path)
    _run(gh, cfg, tmp_path)  # new push, same bug still there
    assert len(gh.reviews) == 1  # quiet: nothing new to say


def test_command_always_replies_and_notes_repeats(tmp_path, cfg):
    gh = FakeGH()
    _run(gh, cfg, tmp_path)
    _run(gh, cfg, tmp_path, trigger="command")
    assert len(gh.reviews) == 2
    assert gh.reviews[1]["comments"] == []
    assert "1 earlier finding(s) still apply" in gh.reviews[1]["body"]


def test_clean_pr_gets_one_review_then_stays_quiet(tmp_path, cfg):
    gh = FakeGH()
    for _ in range(2):
        empty = FakeModel([tool_turn("submit_review", {"summary": "ok", "findings": []})])
        pipeline.run(_job(), gh, empty, cfg, FakeStore(), None, tmp_path / "w")
    assert len(gh.reviews) == 1 and "no defects found" in gh.reviews[0]["body"]


def test_posted_findings_are_queued_for_verification(tmp_path, cfg):
    gh, queued = FakeGH(), []
    pipeline.run(_job(), gh, _model(), cfg, FakeStore(), None, tmp_path / "w", queued.append)
    (req,) = queued
    assert (req.base_sha, req.head_sha, req.comment_id) == ("base1", "abc", 1000)
    assert req.finding["line"] == 4 and req.fingerprint == req.finding["fingerprint"]


def test_nothing_queued_when_rereview_is_quiet(tmp_path, cfg):
    gh, queued = FakeGH(), []
    _run(gh, cfg, tmp_path)
    pipeline.run(_job(), gh, _model(), cfg, FakeStore(), None, tmp_path / "w", queued.append)
    assert queued == []
