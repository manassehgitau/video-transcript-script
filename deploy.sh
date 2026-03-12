#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh  –  One-time GCP setup + Cloud Run deployment
#
# Usage:
#   1. Fill in the variables below (or export them before running)
#   2. chmod +x deploy.sh && ./deploy.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Ensure required CLIs are installed before proceeding
required_cmds=(gcloud docker)
missing=()
for cmd in "${required_cmds[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$cmd")
  fi
done
if [ ${#missing[@]} -ne 0 ]; then
  echo "Error: missing required command(s): ${missing[*]}"
  echo "- Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
  echo "- Install Docker: https://docs.docker.com/get-docker/"
  echo "Or ensure the commands are on your PATH and retry. Exiting."
  exit 1
fi

# ── CONFIGURE THESE ──────────────────────────────────────────────────────────
PROJECT_ID="video-transcript"
REGION="${REGION:-us-central1}"
REPO="video-transcript"
IMAGE="video-transcript-app"
WEB_SERVICE="video-transcript-web"
WORKER_SERVICE="video-transcript-worker"

# Your external Redis URL (e.g. from Upstash – see README)
REDIS_URL="redis://default:7MLvrR5ffv6ag2JdxNuh19nNYHuz8XZn2zlFcQ47jFiUUXcxbmvhAKbP7EvJbzp6@kwgk0ckkoo8g84s00gwcgsc4:6379/0"
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE}:latest"

echo "==> Setting active project to ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}"

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --quiet

echo "==> Creating Artifact Registry repository (if it doesn't exist)..."
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="Video Transcript App images" 2>/dev/null || echo "   (already exists – skipping)"

echo "==> Authenticating Docker with Artifact Registry..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo "==> Building Docker image..."
docker build -t "${IMAGE_TAG}" .

echo "==> Pushing image to Artifact Registry..."
docker push "${IMAGE_TAG}"

echo "==> Deploying Web service (FastAPI)..."
gcloud run deploy "${WEB_SERVICE}" \
  --image="${IMAGE_TAG}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8000 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=0 \
  --max-instances=5 \
  --timeout=3600 \
  --set-env-vars="REDIS_URL=${REDIS_URL}" \
  --command="./start.sh"

echo "==> Deploying Worker service (ARQ)..."
gcloud run deploy "${WORKER_SERVICE}" \
  --image="${IMAGE_TAG}" \
  --region="${REGION}" \
  --platform=managed \
  --no-allow-unauthenticated \
  --port=8080 \
  --memory=4Gi \
  --cpu=4 \
  --min-instances=1 \
  --max-instances=3 \
  --timeout=3600 \
  --set-env-vars="REDIS_URL=${REDIS_URL}" \
  --command="./start-worker.sh"

echo ""
echo "✅  Deployment complete!"
echo ""
echo "Web service URL:"
gcloud run services describe "${WEB_SERVICE}" --region="${REGION}" \
  --format="value(status.url)"
