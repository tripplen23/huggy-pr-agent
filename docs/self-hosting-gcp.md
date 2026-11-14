# Self-Hosting Huggy Runners on Google Cloud (Zero GitHub Actions Minutes)

By default, `huggy-pr-agent` runs on GitHub-hosted `ubuntu-latest` runners so any repository can start in 2 minutes with zero infrastructure.

On active **private** organizations, however, GitHub's 2,000 included Actions minutes/month can run out quickly — even though a PR review spends ~98% of its wall-clock time idle waiting on LLM network I/O. Moving `runs-on:` to a **GitHub Actions Self-Hosted Runner on GCP** drops GitHub Actions minute consumption to **0 minutes/month** while keeping 100% of GitHub's UI (`cancel-in-progress`, live check logs, Re-run button, and `@v1` updates).

> **Security Rule:** Attach self-hosted runners **only to private repositories/organizations** (with `github.event.pull_request.head.repo.full_name == github.repository` enforced). Public repositories already receive unlimited free GitHub-hosted Actions minutes and should remain on `runs-on: ubuntu-latest`.

---

## Google for Startups Cloud Credit Checklist

If your team uses **Google for Startups Cloud Program** credits, follow three rules so **100% of runner and Gemini costs deduct from your Startup Grant** ($0 cash):

1. **Use free first-party OS images (`debian-13` from `debian-cloud`)** — avoid paid Marketplace/commercial OS licenses (`Ubuntu Pro`, `RHEL`, `Windows`).
2. **Use Pay-As-You-Go (`STANDARD` or `SPOT`) billing** — avoid pre-paid subscriptions or Committed Use Discounts (CUDs), which are excluded from Startup Program credits.
3. **Attach a least-privilege Service Account (`roles/aiplatform.user` only)** — never attach the default Compute Engine service account with broad project-editor rights.

---

## Option A — Dedicated GCE VM Multi-Slot Runner *(Fastest & Warmest Cache)*

Best when you want **0s cold-start**, warm SSD caches (`~/.pi-cli`, `pnpm` store, `RUNNER_TOOL_CACHE` for Node/Go), and 5-minute setup. One `e2-standard-4` VM (4 vCPU, 16 GB RAM, 60 GB `pd-balanced` SSD) comfortably runs **3 concurrent runner slots** (`huggy-gcp-1..3`) for both PR reviews and heavy `@huggy` test gates (`next build`, `tsc`, `go test`).

### 1. Create the Least-Privilege Vertex Service Account (if not already created)

```bash
export PROJECT_ID="your-gcp-project"
export ZONE="us-west1-c"

gcloud iam service-accounts create pi-vertex \
  --display-name="Huggy Vertex AI Runner" \
  --project="$PROJECT_ID"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:pi-vertex@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

### 2. Provision the GCE VM with [`examples/gcp/gce-setup.sh`](../examples/gcp/gce-setup.sh)

*(Uses `--no-address` when your VPC has Cloud NAT + IAP; remove `--no-address` if your subnet has no Cloud NAT).*

```bash
gcloud compute instances create huggy-runner \
  --project="$PROJECT_ID" \
  --zone="$ZONE" \
  --machine-type=e2-standard-4 \
  --provisioning-model=STANDARD \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=60GB \
  --boot-disk-type=pd-balanced \
  --no-address \
  --service-account="pi-vertex@${PROJECT_ID}.iam.gserviceaccount.com" \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --metadata-from-file=startup-script=examples/gcp/gce-setup.sh
```

### 3. Register the 3 Runner Slots Once

Open your GitHub repository (or organization) **Settings → Actions → Runners → New self-hosted runner**, copy the 1-hour registration token, and run:

```bash
gcloud compute ssh huggy-runner \
  --project="$PROJECT_ID" \
  --zone="$ZONE" \
  --tunnel-through-iap \
  --command="sudo register-huggy-runners https://github.com/YOUR_ORG/YOUR_REPO <REGISTRATION_TOKEN>"
```

### 4. Point Your Workflow at `[self-hosted, gcp-huggy]`

Change **one line** (`runs-on:`) in `.github/workflows/huggy.yml` while keeping all existing `with:` inputs (`gcp_credentials_json`, `api_key`, etc.) unchanged:

```yaml
    runs-on: [self-hosted, gcp-huggy]
```

---

## Option B — Serverless Ephemeral Runners on Cloud Run Jobs *(Scale-to-Zero & Per-Job Container Isolation)*

Best when you want **strict container-per-job isolation** (`--ephemeral` destroys the container immediately after 1 job) and **$0 idle compute** overnight and on weekends.

### Architecture

1. **Runner Container (`examples/gcp/cloud-run/Dockerfile` + `entrypoint.sh`)**: Pre-bakes `git`, `rg`, `node 24`, `pnpm`, `@earendil-works/pi-coding-agent`, and GitHub's `actions-runner` (`2.338.2`). Deployed as a **Cloud Run Job** (`huggy-ephemeral-runner`) running under `pi-vertex` (`roles/aiplatform.user` **only** — zero access to Secret Manager or GitHub admin PATs).
2. **Webhook Dispatcher (`examples/gcp/cloud-run/webhook.py`)**: A tiny, zero-dependency Python **Cloud Run Service** (`min-instances=0`) running under a separate `huggy-webhook` SA. When a `workflow_job.queued` event with label `"gcp-huggy"` arrives (verified via `X-Hub-Signature-256`), `webhook.py` mints a single-use runner registration token (`GH_REG_TOKEN`) and passes it as a per-execution container override to `huggy-ephemeral-runner`, which runs one `--ephemeral` job and exits cleanly.

### Deploy Option B

*(First create the `pi-vertex` SA from **Option A — Step 1** above if you haven't already).*

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-west1"
export REPO_URL="https://github.com/YOUR_ORG/YOUR_REPO"
: "${GITHUB_RUNNER_PAT:?Set GITHUB_RUNNER_PAT (fine-grained PAT with repo Administration: Read & Write)}"
: "${GITHUB_WEBHOOK_SECRET:?Set GITHUB_WEBHOOK_SECRET}"

# 1. Build & push the ephemeral runner image to Artifact Registry
gcloud artifacts repositories describe huggy --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1 \
  || gcloud artifacts repositories create huggy --repository-format=docker --location="$REGION" --project="$PROJECT_ID"
gcloud builds submit examples/gcp/cloud-run \
  --tag "${REGION}-docker.pkg.dev/${PROJECT_ID}/huggy/runner:latest" \
  --project="$PROJECT_ID"

# 2. Store your fine-grained GitHub PAT and Webhook Secret in Secret Manager
printf '%s' "$GITHUB_RUNNER_PAT" | gcloud secrets create huggy-gh-runner-pat --data-file=- --project="$PROJECT_ID"
printf '%s' "$GITHUB_WEBHOOK_SECRET" | gcloud secrets create huggy-gh-webhook-secret --data-file=- --project="$PROJECT_ID"

# 2b. Create a dedicated webhook SA; ONLY the webhook SA can read secrets (never the PR-executing runner SA!)
gcloud iam service-accounts create huggy-webhook \
  --display-name="Huggy Runner Webhook Dispatcher" \
  --project="$PROJECT_ID"

for sec in huggy-gh-runner-pat huggy-gh-webhook-secret; do
  gcloud secrets add-iam-policy-binding "$sec" \
    --member="serviceAccount:huggy-webhook@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID"
done

# 3. Create the Cloud Run Job (95m timeout gives 5m startup buffer over 90m command jobs)
gcloud run jobs create huggy-ephemeral-runner \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/huggy/runner:latest" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --cpu=4 --memory=8Gi \
  --task-timeout=5700s \
  --max-retries=0 \
  --service-account="pi-vertex@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="GH_REPO_URL=${REPO_URL},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global"

# Allow the webhook SA to invoke ONLY `huggy-ephemeral-runner` (with container env overrides)
gcloud run jobs add-iam-policy-binding huggy-ephemeral-runner \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:huggy-webhook@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.developer"

# 4. Deploy the scale-to-zero `workflow_job` webhook Cloud Run Service
gcloud run deploy huggy-runner-webhook \
  --source=examples/gcp/cloud-run \
  --command="python3" --args="/app/webhook.py" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="huggy-webhook@${PROJECT_ID}.iam.gserviceaccount.com" \
  --cpu=1 --memory=256Mi --min-instances=0 --max-instances=5 \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},CLOUD_RUN_JOB=huggy-ephemeral-runner,GH_REPO_URL=${REPO_URL},REQUIRED_LABEL=gcp-huggy,ALLOWED_REPO=${REPO_URL#https://github.com/}" \
  --set-secrets="GITHUB_WEBHOOK_SECRET=huggy-gh-webhook-secret:latest,GH_RUNNER_PAT=huggy-gh-runner-pat:latest"
```

Finally, add the deployed `huggy-runner-webhook` URL under **GitHub Repo/Org Settings → Webhooks** (Content type: `application/json`, Secret: `$GITHUB_WEBHOOK_SECRET`, Event: **`Workflow jobs`** only), and set `runs-on: [self-hosted, gcp-huggy]` in your workflow.

---

## Choosing Between GitHub-Hosted, GCE VM (Option A), and Cloud Run Jobs (Option B)

| Option | GitHub Actions Minutes | Cold Start | Isolation | Best For |
| :--- | :---: | :---: | :---: | :--- |
| **Default (`ubuntu-latest`)** | Standard quota (Free on public repos) | ~10s | Fresh VM per job | Public repos & small private repos (< 150 runs/mo) |
| **Option A — GCE VM (`gce-setup.sh`)** | **0 mins** | **0s** (warm cache) | Shared VM + `/tmp` hook | Private teams running frequent PR reviews & heavy `@huggy` builds |
| **Option B — Cloud Run Jobs** | **0 mins** | ~6–10s | Fresh container per job | Multi-repo orgs wanting serverless scale-to-zero & ephemeral sandboxes |
