"""Chaos tests against the live system: crash recovery and exactly-once verdicts.

A: send a verify job, force-kill the dispatcher pod mid-proof, and check the job still
   finishes (recovery time measured) with exactly one reply on the PR comment.
B: redeliver the same finished job and check nothing is posted twice.

usage: python ops/chaos.py --repo iviraa/uxie-sandbox --pr 6
Costs one proof (~$0.05) and posts one reply on the sandbox PR.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid

import boto3

REGION = "us-east-2"


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def replies(repo: str, pr: int, comment_id: int) -> int:
    out = sh(
        "gh",
        "api",
        f"repos/{repo}/pulls/{pr}/comments",
        "--paginate",
        "--jq",
        f"[.[] | select(.in_reply_to_id == {comment_id})] | length",
    )
    return sum(int(x) for x in out.split())


def item(table: str, key: dict[str, str]) -> dict:
    ddb = boto3.client("dynamodb", region_name=REGION)
    got = (
        ddb.get_item(
            TableName=table, Key={k: {"S": v} for k, v in key.items()}, ConsistentRead=True
        ).get("Item")
        or {}
    )
    return {k: list(v.values())[0] for k, v in got.items()}


def wait(pred, timeout: float, every: float = 2.0) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return time.monotonic() - t0
        time.sleep(every)
    return None


def installation_id(repo: str) -> int:
    """Needs app auth (a JWT signed with the app key), not the user's gh token."""
    import httpx

    from uxie.github.app_auth import app_jwt

    raw = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
        SecretId="uxie/github-app"
    )["SecretString"]
    s = json.loads(raw)
    r = httpx.get(
        f"https://api.github.com/repos/{repo}/installation",
        headers={
            "Authorization": f"Bearer {app_jwt(s['app_id'], s['private_key'])}",
            "Accept": "application/vnd.github+json",
        },
        timeout=20,
    )
    r.raise_for_status()
    return int(r.json()["id"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--stack", default="uxie")
    args = ap.parse_args()

    cf = boto3.client("cloudformation", region_name=REGION)
    outs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
    }
    queue, table = outs["VerifyQueueUrl"], outs["ReviewsTableName"]

    pr = json.loads(sh("gh", "api", f"repos/{args.repo}/pulls/{args.pr}"))
    comments = json.loads(sh("gh", "api", f"repos/{args.repo}/pulls/{args.pr}/comments"))
    root = next(
        c
        for c in comments
        if c.get("in_reply_to_id") is None
        and "uxie:" in c["body"]
        and c["user"]["login"].endswith("[bot]")
    )
    inst = installation_id(args.repo)

    fp = f"chaos-{uuid.uuid4().hex[:8]}"  # fresh key, so the claim starts clean
    finding = {
        "path": root["path"],
        "line": root["line"],
        "severity": "high",
        "category": "bug",
        "title": root["body"].split("**")[1],
        "explanation": root["body"].split("\n\n", 1)[1][:1500],
        "confidence": 0.9,
        "fingerprint": fp,
    }
    req = {
        "repo": args.repo,
        "pr_number": args.pr,
        "installation_id": inst,
        "base_sha": pr["base"]["sha"],
        "head_sha": pr["head"]["sha"],
        "title": pr["title"],
        "body": pr["body"] or "",
        "finding": finding,
        "comment_id": root["id"],
        "post_if_proven": False,
        "trace": {},
    }
    key = {"PK": f"PR#{args.repo}#{args.pr}", "SK": f"VERIFY#{pr['head']['sha']}#{fp}"}
    before = replies(args.repo, args.pr, root["id"])
    sqs = boto3.client("sqs", region_name=REGION)

    print("A) crash mid-proof")
    sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(req))
    started = wait(lambda: item(table, key).get("status") == "in_progress", 300)
    assert started is not None, "dispatcher never claimed the job"
    time.sleep(5)  # well into the proof
    pod = sh(
        "kubectl",
        "get",
        "pods",
        "-n",
        "uxie-system",
        "-l",
        "app=uxie-dispatcher",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    sh("kubectl", "delete", "pod", pod, "-n", "uxie-system", "--grace-period=0", "--force")
    killed = time.monotonic()
    print(f"   claimed after {started:.0f}s, killed {pod} mid-proof")
    done = wait(lambda: item(table, key).get("status") == "done", 900, every=5)
    assert done is not None, "job never recovered"
    after = replies(args.repo, args.pr, root["id"])
    rec = item(table, key)
    print(
        f"   recovered and finished {time.monotonic() - killed:.0f}s after the kill: "
        f"verdict={rec.get('verdict')}, replies +{after - before}"
    )
    assert after - before == (1 if rec.get("published") else 0), "reply count is not exactly-once"

    print("B) redeliver the finished job")
    sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(req))
    time.sleep(90)
    again = replies(args.repo, args.pr, root["id"])
    assert again == after, f"duplicate reply posted ({again - after})"
    assert item(table, key).get("status") == "done"
    print("   no second reply, record unchanged")
    print("PASS")


if __name__ == "__main__":
    main()
