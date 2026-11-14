#!/usr/bin/env python3
"""Zero-dependency GitHub `workflow_job.queued` webhook -> Cloud Run Jobs dispatcher."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import sys
import time
import urllib.error
import urllib.request

SECRET = (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").encode("utf-8")
PROJECT = os.environ.get("GCP_PROJECT") or ""
REGION = os.environ.get("GCP_REGION") or "us-west1"
JOB_NAME = os.environ.get("CLOUD_RUN_JOB") or "huggy-ephemeral-runner"
REQUIRED_LABEL = os.environ.get("REQUIRED_LABEL") or "gcp-huggy"
ALLOWED_REPO = (os.environ.get("ALLOWED_REPO") or "").strip().strip("/")


def verify_signature(raw_body: bytes, sig_header: str, secret: bytes | None = None) -> bool:
    key = SECRET if secret is None else secret
    if not key or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def fetch_gcp_access_token() -> str:
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def should_dispatch_job(event: str, payload: dict, required_label: str = REQUIRED_LABEL, allowed_repo: str = ALLOWED_REPO) -> bool:
    if event != "workflow_job" or payload.get("action") != "queued":
        return False
    labels = (payload.get("workflow_job") or {}).get("labels") or []
    if required_label not in labels:
        return False
    repo = payload.get("repository") or {}
    if not repo.get("private"):
        return False
    full_name = (repo.get("full_name") or "").strip()
    if allowed_repo:
        if "/" in allowed_repo and full_name != allowed_repo:
            return False
        if "/" not in allowed_repo and not full_name.startswith(f"{allowed_repo}/"):
            return False
    return True


def trigger_cloud_run_job(max_attempts: int = 3) -> int:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            token = fetch_gcp_access_token()
            url = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}:run"
            req = urllib.request.Request(
                url,
                data=b"{}",
                method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt + 1 == max_attempts:
                raise
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt + 1 == max_attempts:
                raise
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Exhausted retries: {last_exc}")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        if length <= 0 or length > 2_000_000:
            self.send_response(400)
            self.end_headers()
            return
        body = self.rfile.read(length)
        if not verify_signature(body, self.headers.get("X-Hub-Signature-256") or ""):
            self.send_response(401)
            self.end_headers()
            return

        event = self.headers.get("X-GitHub-Event") or ""
        if event == "ping":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pong")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return

        if should_dispatch_job(event, payload):
            try:
                status = trigger_cloud_run_job()
                sys.stdout.write(f"Triggered Cloud Run Job {JOB_NAME} (HTTP {status})\n")
                sys.stdout.flush()
            except Exception as exc:
                sys.stderr.write(f"Failed to trigger Cloud Run Job: {exc}\n")
                self.send_response(500)
                self.end_headers()
                return

        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"ok")


if __name__ == "__main__":
    if not SECRET or not PROJECT:
        sys.stderr.write("GITHUB_WEBHOOK_SECRET and GCP_PROJECT must be set\n")
        sys.exit(1)
    port = int(os.environ.get("PORT") or "8080")
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
