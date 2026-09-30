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
