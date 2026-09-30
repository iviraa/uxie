# Go sandbox: same locks and JSON contract as the Python image; compiles fully offline.
FROM golang:1.27-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

# warm build cache for the standard library, copied into /tmp at run time (saves ~10s a run)
RUN mkdir -p /tmp/warm && cd /tmp/warm && go mod init warm \
    && printf 'package warm\nimport (\n"errors"\n"fmt"\n"sort"\n"strconv"\n"strings"\n"testing"\n"time"\n)\nfunc TestX(t *testing.T) {\n_ = fmt.Sprint(errors.New("x"), sort.IntsAreSorted(nil), strconv.Itoa(1), strings.ToUpper("a"), time.Second)\n}\n' > w_test.go \
    && GOCACHE=/opt/gocache CGO_ENABLED=0 go test -covermode=set -coverprofile=/tmp/c.out ./... \
    && chmod -R a+rX /opt/gocache && rm -rf /tmp/warm /tmp/c.out

COPY run_test.py /opt/uxie/run_test.py

# no downloads of any kind: modules come from the read-only cache mounted at /env (or vendor/)
ENV GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local CGO_ENABLED=0 \
    GOCACHE=/tmp/gocache GOPATH=/tmp/gopath HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 10001
WORKDIR /src
ENTRYPOINT ["python3", "/opt/uxie/run_test.py"]
