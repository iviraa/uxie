# Untrusted-code runner: no network at runtime, non-root, read-only rootfs (enforced by the runtime)
FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.4.2 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

COPY run_test.py uxie_reach.py pytest.ini sitecustomize.py /opt/uxie/

# /opt/uxie on the path so sitecustomize (dependency env loader) runs in every interpreter
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp PYTHONPATH=/opt/uxie
USER 10001
WORKDIR /src
ENTRYPOINT ["python", "/opt/uxie/run_test.py"]
