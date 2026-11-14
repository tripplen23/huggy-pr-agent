#!/usr/bin/env python3
"""Zero-dependency GitHub `workflow_job.queued` webhook -> Cloud Run Jobs dispatcher."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import sys
import urllib.request

SECRET = (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").encode("utf-8")
PROJECT = os.environ.get("GCP_PROJECT") or ""
REGION = os.environ.get("GCP_REGION") or "us-west1"
JOB_NAME = os.environ.get("CLOUD_RUN_JOB") or "huggy-ephemeral-runner"
GH_REPO_URL = (os.environ.get("GH_REPO_URL") or "").strip().rstrip("/")
GH_RUNNER_PAT = (os.environ.get("GH_RUNNER_PAT") or "").strip()
REQUIRED_LABEL = os.environ.get("REQUIRED_LABEL") or "gcp-huggy"
ALLOWED_REPO = (os.environ.get("ALLOWED_REPO") or "").strip().strip("/")


def verify_signature(raw_body: bytes, sig_header: str, secret: bytes | None = None) -> bool:
    key = SECRET if secret is None else secret
    if not key or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, sig_header)
    except TypeError:
        return False


def should_dispatch_job(
    event: str,
    payload: dict,
    required_label: str = REQUIRED_LABEL,
    allowed_repo: str = ALLOWED_REPO,
) -> bool:
    if event != "workflow_job" or payload.get("action") != "queued":
        return False
    labels = (payload.get("workflow_job") or {}).get("labels") or []
    if required_label not in labels:
        return False
    repo = payload.get("repository") or {}
    full_name = (repo.get("full_name") or "").strip()
    if not repo.get("private"):
        sys.stderr.write(f"Skipping non-private repository {full_name}\n")
        return False
    if allowed_repo:
        allowed = allowed_repo.casefold()
        fn_fold = full_name.casefold()
        if ("/" in allowed and fn_fold != allowed) or ("/" not in allowed and not fn_fold.startswith(f"{allowed}/")):
            sys.stderr.write(f"Skipping {full_name} (not matching ALLOWED_REPO={allowed_repo})\n")
            return False
    return True


def mint_runner_registration_token(repo_url: str = GH_REPO_URL, pat: str = GH_RUNNER_PAT) -> str:
    path_part = repo_url.removeprefix("https://github.com/").strip("/")
    api_url = (
        f"https://api.github.com/repos/{path_part}/actions/runners/registration-token"
        if "/" in path_part
        else f"https://api.github.com/orgs/{path_part}/actions/runners/registration-token"
    )
    req = urllib.request.Request(
        api_url,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "huggy-runner-webhook",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        tok = (json.loads(resp.read().decode("utf-8")) or {}).get("token") or ""
    if not tok:
        raise RuntimeError("GitHub returned an empty runner registration token")
    return tok


def fetch_gcp_access_token() -> str:
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def trigger_cloud_run_job() -> int:
    reg_token = mint_runner_registration_token()
    gcp_token = fetch_gcp_access_token()
    url = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}:run"
    body = json.dumps(
        {
            "overrides": {
                "containerOverrides": [
                    {"env": [{"name": "GH_REG_TOKEN", "value": reg_token}]}
                ]
            }
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {gcp_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.status


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10

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
    if not SECRET or not PROJECT or not GH_REPO_URL or not GH_RUNNER_PAT:
        sys.stderr.write("GITHUB_WEBHOOK_SECRET, GCP_PROJECT, GH_REPO_URL, and GH_RUNNER_PAT must be set\n")
        sys.exit(1)
    port = int(os.environ.get("PORT") or "8080")
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
