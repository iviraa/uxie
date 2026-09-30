# TypeScript/JavaScript sandbox: same locks and JSON contract; runs tests with node:test via tsx.
FROM node:24-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --no-audit --no-fund tsx@4 \
    && mkdir -p /opt/uxie/tools && cd /opt/uxie/tools && npm init -y >/dev/null \
    && npm install --no-audit --no-fund fast-check@3 \
    && ln -s /opt/uxie/tools/node_modules /node_modules \
    && cd / && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

COPY run_test.py /opt/uxie/run_test.py

ENV HOME=/tmp NPM_CONFIG_CACHE=/tmp/npm NODE_OPTIONS=--enable-source-maps \
    PYTHONDONTWRITEBYTECODE=1
USER 10001
WORKDIR /src
ENTRYPOINT ["python3", "/opt/uxie/run_test.py"]
