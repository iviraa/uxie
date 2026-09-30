import threading

from uxie.verify.main import serve
from uxie.verify.request import VerifyRequest

REQ = VerifyRequest("o/r", 1, 7, "b", "h", "t", "", {"fingerprint": "fp"}, 5)


class FakeSQS:
    def __init__(self, batches, stop):
        self.batches, self.stop, self.deleted = list(batches), stop, []

    def receive_message(self, **kw):
        if not self.batches:
            self.stop.set()
            return {}
        return {"Messages": self.batches.pop(0)}

    def delete_message(self, QueueUrl, ReceiptHandle):  # noqa: N803
        self.deleted.append(ReceiptHandle)


def _msg(i, body=None):
    return {"MessageId": f"m{i}", "ReceiptHandle": f"r{i}", "Body": body or REQ.to_json()}


def test_deletes_only_handled_messages():
    stop = threading.Event()
    sqs = FakeSQS([[_msg(1)], [_msg(2)], [_msg(3, body="not json")]], stop)
    seen = []

    def handle_one(req):
        seen.append(req)
        if len(seen) == 2:
            raise RuntimeError("sandbox down")
        return "confirmed"

    serve(sqs, "q", handle_one, stop)
    assert sqs.deleted == ["r1"]  # r2 failed, r3 unparseable: both left for retry / DLQ
    assert seen[0] == REQ


def test_stop_is_checked_between_jobs():
    stop = threading.Event()
    sqs = FakeSQS([[_msg(1)], [_msg(2)]], stop)

    def handle_one(req):
        stop.set()  # SIGTERM arrives mid-job: this job finishes, no new one starts
        return "done"

    serve(sqs, "q", handle_one, stop)
    assert sqs.deleted == ["r1"] and len(sqs.batches) == 1


class HeartbeatSQS(FakeSQS):
    def __init__(self, batches, stop):
        super().__init__(batches, stop)
        self.extended = []

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):  # noqa: N803
        self.extended.append((ReceiptHandle, VisibilityTimeout))


def test_heartbeat_extends_visibility_and_lease_during_a_long_proof():
    import time

    stop = threading.Event()
    sqs = HeartbeatSQS([[_msg(1)]], stop)
    leases = []

    def slow(req):
        time.sleep(0.35)  # a proof that outlives several heartbeats
        return "confirmed"

    serve(sqs, "q", slow, stop, extend_lease=leases.append, heartbeat_s=0.1)
    assert len(sqs.extended) >= 2 and all(h == "r1" for h, _ in sqs.extended)
    assert len(leases) == len(sqs.extended) and leases[0] == REQ
    assert sqs.deleted == ["r1"]


def test_busy_job_is_left_in_the_queue():
    from uxie.store.reviews import Busy

    stop = threading.Event()
    sqs = FakeSQS([[_msg(1)]], stop)

    def busy(req):
        raise Busy("another pod is on it")

    serve(sqs, "q", busy, stop)
    assert sqs.deleted == []  # comes back after visibility expires, in case that pod dies
