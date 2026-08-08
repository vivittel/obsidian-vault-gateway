# syntax=docker/dockerfile:1
#
# IMPLEMENTATION_PLAN.md sections 9-10: Python slim base, pinned versions,
# non-root, no vault data baked into the image, healthcheck defined.

FROM python:3.13-slim-bookworm AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml README.md ./
COPY app ./app
# Installed with --no-deps: requirements.lock already pinned every transitive
# dependency above: this step only adds the "app" package itself.
RUN pip install --no-cache-dir --no-deps .
RUN python -m compileall -q /opt/venv/lib


FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Mount points. The vault itself is never copied into the image (section 10:
# "コンテナ内にVaultデータをコピーしない") — these directories exist only so a
# missing bind mount fails obviously rather than silently creating a directory
# owned by root at container start.
RUN mkdir -p /vault-ro /vault-write/inbox \
    && chown -R gateway:gateway /vault-write

USER gateway
WORKDIR /

EXPOSE 8000

# Checks the response body, not just the status code: GatewayApplication.health()
# (app/application.py) always answers HTTP 200, even when a mount is missing or
# has the wrong permissions — that case sets the JSON body's "status" to
# "degraded" instead. A check that only looked at the HTTP status would report
# a container with a broken vault or inbox mount as healthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request, sys; r = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); sys.exit(0 if r.status == 200 and json.load(r)['status'] == 'ok' else 1)"]

# --no-access-log: uvicorn's built-in access log prints the raw query string,
# which would leak search terms that app/middleware.py deliberately keeps out
# of the application log (see docs/PHASE1_PLAN.md section 4.7).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
