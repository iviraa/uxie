import hashlib
import hmac

from critic.github.webhook import route, verify_signature


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature():
    body = b'{"a":1}'
    assert verify_signature("s3cret", body, _sig("s3cret", body))
    assert not verify_signature("s3cret", body, _sig("other", body))
    assert not verify_signature("s3cret", body, None)
    assert not verify_signature("s3cret", body, "sha1=abc")


def _pr(action="opened", draft=False, state="open"):
    return {
        "action": action,
        "installation": {"id": 7},
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 3, "draft": draft, "state": state, "head": {"sha": "abc"}},
    }


def test_route_pull_request():
    job = route("pull_request", _pr())
    assert job and (job.repo, job.pr_number, job.head_sha, job.installation_id) == (
        "o/r",
        3,
        "abc",
        7,
    )
    assert job.group_id == "o/r#3"
    assert route("pull_request", _pr(action="closed")) is None
    assert route("pull_request", _pr(draft=True)) is None
    assert route("pull_request", _pr(action="labeled")) is None


def _comment(body="/review", assoc="OWNER", is_pr=True):
    issue = {"number": 3, "state": "open"} | ({"pull_request": {}} if is_pr else {})
    return {
        "action": "created",
        "installation": {"id": 7},
        "repository": {"full_name": "o/r"},
        "issue": issue,
        "comment": {"id": 99, "body": body, "author_association": assoc},
    }


def test_route_review_command():
    job = route("issue_comment", _comment())
    assert job and job.trigger == "command" and job.head_sha is None and job.comment_id == 99
    assert route("issue_comment", _comment(assoc="NONE")) is None  # strangers can't spend money
    assert route("issue_comment", _comment(body="lgtm")) is None
    assert route("issue_comment", _comment(is_pr=False)) is None


def test_route_ignores_uninstalled():
    p = _pr()
    del p["installation"]
    assert route("pull_request", p) is None
