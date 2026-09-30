# Verify dispatcher: trusted component (has network + AWS creds); never runs PR code itself.
FROM python:3.12-slim

RUN pip install --no-cache-dir "boto3>=1.34" "httpx>=0.27" "pyjwt[crypto]>=2.8" "kubernetes>=31" \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin dispatcher

COPY src/uxie /opt/app/uxie
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001
ENTRYPOINT ["python", "-m", "uxie.verify.main"]
