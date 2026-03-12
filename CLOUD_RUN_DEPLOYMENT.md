# Deploying Video Transcript Service to Google Cloud Run

## How it works on Cloud Run

Your app has three moving parts. Here's how each one maps to GCP:

```
┌───────────────────────────────────────────────────────────────┐
│  COOLIFY (before)          │  GOOGLE CLOUD RUN (after)        │
├───────────────────────────────────────────────────────────────┤
│  FastAPI container         │  Cloud Run Service: web          │
│  ARQ Worker container      │  Cloud Run Service: worker       │
│  Redis service (sidecar)   │  Upstash Redis (external SaaS)   │
└───────────────────────────────────────────────────────────────┘
```

**Why two Cloud Run services?**  
Cloud Run runs one process per container. Your app needs FastAPI running
for HTTP/WebSocket traffic AND a separate ARQ worker consuming jobs from
the queue. Both use the same Docker image but start with a different command.

**Why external Redis (not Cloud Memorystore)?**  
Cloud Memorystore (managed Redis) requires a VPC connector, which costs
~$72/month minimum. Upstash Redis is serverless, free up to 10k requests/day,
and reachable over the public internet without VPC setup.

---

## Step 1 — Get a Redis URL (Upstash — free tier)

1. Go to https://upstash.com and sign up (free).
2. Create a new Redis database → choose the same region as your Cloud Run
   deployment (e.g. `us-central1`).
3. Copy the **Redis URL** — it looks like:
   ```
   rediss://default:AbCdEf123456@global-xxx.upstash.io:6380
   ```
   Note the `rediss://` (with double-s) — that means TLS, which Upstash uses.
4. Keep this URL handy; you'll need it in the steps below.

> **Alternative:** If you already have a managed Redis somewhere else
> (e.g. your Contabo instance), just use that URL instead.

---

## Step 2 — Prerequisites on your machine

Install and authenticate the Google Cloud CLI:

```bash
# Install: https://cloud.google.com/sdk/docs/install
gcloud auth login
gcloud auth application-default login
```

Also make sure Docker is running locally.

---

## Step 3 — Copy the deployment files into your project

Copy these files into the **root of your `script-video-transcript` repo**:

```
script-video-transcript/
├── Dockerfile          ← replace/add this
├── start.sh            ← update this (uses $PORT)
├── start-worker.sh     ← new file
├── cloudbuild.yaml     ← new file (for CI/CD)
├── deploy.sh           ← new file (for manual deploy)
└── ... (existing app files)
```

---

## Step 4 — Manual first-time deployment

Open `deploy.sh` and set the two variables at the top:

```bash
PROJECT_ID="my-gcp-project"          # your GCP project ID
REDIS_URL="rediss://default:xxx@..."  # from Upstash
```

Then run:

```bash
chmod +x deploy.sh start.sh start-worker.sh
./deploy.sh
```

This will:
1. Enable Cloud Run, Artifact Registry, and Cloud Build APIs
2. Create an Artifact Registry Docker repository
3. Build your image locally
4. Push it to Artifact Registry
5. Deploy the `video-transcript-web` service (FastAPI)
6. Deploy the `video-transcript-worker` service (ARQ worker)

At the end it prints your public URL, e.g.:
```
https://video-transcript-web-abc123-uc.a.run.app
```

---

## Step 5 — (Optional) Automatic CI/CD with Cloud Build

To auto-deploy on every `git push`:

1. In the GCP Console → Cloud Build → Triggers → **Create Trigger**
2. Connect your GitHub repo (`zinduaofficial/script-video-transcript`)
3. Point it to `cloudbuild.yaml`
4. Under **Substitution variables** add:
   - `_REDIS_URL` → your Upstash Redis URL
5. Save. Every push to `main` will now build and deploy automatically.

---

## Environment variables

Set these on both services (done automatically by `deploy.sh` / `cloudbuild.yaml`):

| Variable             | Description                                        | Required |
|----------------------|----------------------------------------------------|----------|
| `REDIS_URL`          | Redis connection string (from Upstash or other)    | ✅ Yes    |
| `TRANSCRIPTHQ_API_KEY` | API key for non-YouTube sources (optional)       | No       |
| `JOB_TIMEOUT`        | Max job time in seconds (default 7200)             | No       |
| `YTDLP_COOKIEFILE`   | Path to yt-dlp cookies file (for 403 errors)       | No       |

To update env vars without redeploying the image:

```bash
gcloud run services update video-transcript-web \
  --region=us-central1 \
  --set-env-vars="REDIS_URL=rediss://..."
```

---

## Architecture diagram (Cloud Run)

```
                          ┌─────────────────────────────────────┐
Internet ──▶ HTTPS ──▶   │  Cloud Run: video-transcript-web    │
                          │  (FastAPI + bgutil, min=0)          │
                          └──────────────┬──────────────────────┘
                                         │ enqueue_job / pub-sub
                                         ▼
                               ┌──────────────────┐
                               │  Upstash Redis   │  (external, TLS)
                               └────────┬─────────┘
                                        │ dequeue
                                        ▼
                          ┌─────────────────────────────────────┐
                          │  Cloud Run: video-transcript-worker │
                          │  (ARQ worker + bgutil, min=1)       │
                          └─────────────────────────────────────┘
```

---

## Resource sizing guide

| Service | Memory | CPU | Min instances | Why                                           |
|---------|--------|-----|---------------|-----------------------------------------------|
| web     | 2 Gi   | 2   | 0             | FastAPI is lightweight; scale to zero to save money |
| worker  | 4 Gi   | 4   | 1             | Whisper needs RAM; min=1 so jobs don't queue on cold start |

Adjust in `deploy.sh` or the GCP Console as needed.

---

## Handling yt-dlp YouTube cookies (if you get 403 errors)

Cloud Run containers are stateless and ephemeral — you can't easily drop a
cookies file in there manually. Options:

**Option A — Mount from Google Cloud Storage (recommended)**

1. Export your browser cookies for YouTube as `cookies.txt`
2. Upload to a GCS bucket:
   ```bash
   gsutil cp cookies.txt gs://your-bucket/youtube-cookies.txt
   ```
3. Mount the bucket in Cloud Run (add `--add-volume` and `--add-volume-mount`)
   or have your app download the file on startup from GCS.

**Option B — Store in Secret Manager**

1. ```bash
   gcloud secrets create ytdlp-cookies --data-file=cookies.txt
   ```
2. Add `--set-secrets=YTDLP_COOKIES_CONTENT=ytdlp-cookies:latest` to your
   deploy command.
3. In your app, write the secret to `/tmp/cookies.txt` at startup and set
   `YTDLP_COOKIEFILE=/tmp/cookies.txt`.

---

## Cost estimate (light usage)

| Resource               | Cost                          |
|------------------------|-------------------------------|
| Cloud Run web (min=0)  | ~$0/month at low traffic      |
| Cloud Run worker (min=1, 4Gi/4CPU) | ~$60–80/month   |
| Artifact Registry      | ~$0.10/GB storage             |
| Upstash Redis (free)   | $0 up to 10k req/day          |

To reduce cost, set `--min-instances=0` on the worker too — but then expect
a ~10-30s cold-start delay on the first job after idle.

---

## Useful commands

```bash
# View web service logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=video-transcript-web" --limit=50

# View worker logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=video-transcript-worker" --limit=50

# Scale worker to 0 (pause/stop billing)
gcloud run services update video-transcript-worker --min-instances=0 --region=us-central1

# Update just the image (redeploy without changing config)
gcloud run deploy video-transcript-web \
  --image=us-central1-docker.pkg.dev/PROJECT_ID/video-transcript/video-transcript-app:latest \
  --region=us-central1
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Worker keeps restarting | Check logs — likely Redis connection failing. Verify `REDIS_URL` is set correctly on the worker service. |
| Web returns 503 | Cold start taking too long. Set `--min-instances=1` on web service or check for startup errors in logs. |
| `bgutil` not found | Ensure `npm install -g bgutil-ytdlp-pot-provider` is in your Dockerfile (it is — just double-check the image rebuilt). |
| Whisper model slow on first job | The model downloads on first use. To avoid this, uncomment the pre-download line in the Dockerfile. |
| WebSocket connection drops | Cloud Run supports WebSockets — but the default timeout is 300s. Increase with `--timeout=3600`. |
