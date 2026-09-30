import json

from uxie.common import config


class FakeSM:
    def __init__(self, secret):
        self.secret = secret

    def get_secret_value(self, SecretId):  # noqa: N803
        return {"SecretString": json.dumps(self.secret)}


def test_otel_settings_decode_grafana_headers(monkeypatch):
    monkeypatch.setenv("OTEL_SECRET_ARN", "arn:x")
    monkeypatch.setattr(
        config.boto3,
        "client",
        lambda name: FakeSM(
            {
                "endpoint": "https://otlp-gateway.grafana.net/otlp/",
                "headers": "Authorization=Basic%20dXNlcjp0b2tlbg==",
            }
        ),
    )
    s = config.otel_settings()
    assert s.endpoint == "https://otlp-gateway.grafana.net/otlp"
    assert s.headers == {"Authorization": "Basic dXNlcjp0b2tlbg=="}


def test_otel_off_without_secret(monkeypatch):
    monkeypatch.delenv("OTEL_SECRET_ARN", raising=False)
    assert config.otel_settings() is None
