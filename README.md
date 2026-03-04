# Video Transcription Service

A lightweight queued transcription service built with FastAPI + ARQ.

- Primary sources:
  - YouTube: uses `youtube-transcript-api` when captions are available (no API key).
  - Local/offline: uses `faster-whisper` (faster-whisper) as a fallback to transcribe downloaded audio.

This repository provides both a simple synchronous REST API (kept for compatibility)
and WebSocket + ARQ-driven queued transcription for non-blocking, scalable usage.

Contents

- Architecture overview and local setup
- Running locally (with and without Docker)
- WebSocket and HTTP usage examples
- Docker Compose and deployment notes (Coolify / managed Redis like Contabo)

Quick links

- API docs: `http://localhost:8000/docs`
- Health check: `GET /health`

---

## Architecture overview

```
┌──────────────┐   WebSocket / HTTP   ┌─────────────────┐
│  Your client │ ──────────────────▶  │  FastAPI (main) │
└──────────────┘                      └────────┬────────┘
                                               │ enqueue_job (arq)
                                               ▼
                                       ┌──────────────┐
                                       │    Redis     │  ◀── pub/sub result
                                       └──────┬───────┘         ▲
                                              │ dequeue          │
                                              ▼                  │
                                       ┌──────────────┐  publish │
                                       │  ARQ Worker  │ ─────────┘
                                       └──────────────┘
```

- `FastAPI` receives requests, enqueues jobs and delivers results via WebSocket.
- `Redis` is the message broker (job queue + pub/sub result channel). Use your managed Redis URL (e.g., Contabo).
- `ARQ Worker` is a separate process that performs transcription tasks (YouTube download + Whisper, or direct YouTube captions).

---

## 1. Install & Python environment (Local)

Create and activate a virtualenv, then install requirements:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

Note: `faster-whisper` will download the Whisper model (the `small` model is used by default) on first run and cache it.

---

## 2. Environment variables

Copy the example env file and edit values as needed:

```bash
cp .env.example .env
```

Important variables:

- `REDIS_URL` — URL to your Redis instance. In containerized deployments set this to the managed Redis URL provided by Contabo (or set `REDIS_URL` in Coolify).
  - Example: `REDIS_URL=redis://username:password@redis-host.example.com:6379/0`
- `TRANSCRIPTHQ_API_KEY` — optional API key for non-YouTube sources
- `JOB_TIMEOUT` — maximum job run time in seconds (default 7200 = 2 hours)

---

## 3. Running locally (three processes)

This project uses ARQ for background jobs. For local development run three processes:

Terminal 1 — Redis (local)

```bash
redis-server
```

Terminal 2 — ARQ Worker

```bash
arq app.queue.worker.WorkerSettings
```

Terminal 3 — FastAPI

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for the interactive Swagger UI.

---

## 4. Docker (recommended for deployment)

Build and run with Docker Compose (this repo's `docker-compose.yml` expects `REDIS_URL` to point to an external Redis resource in managed deployments):

```bash
docker-compose up --build
```

Notes for Coolify / managed platforms

- Coolify can deploy Compose apps; set environment variables (notably `REDIS_URL` pointing to Contabo) in the Coolify UI.
- This repository's `docker-compose.yml` provides two services: `web` (FastAPI) and `worker` (ARQ). Both read `REDIS_URL` from env.

---

## 5. API usage

Synchronous REST (kept for compatibility): `POST /transcribe` — runs transcription synchronously (may block).

Queued WebSocket flow (recommended):

- Connect to `ws://<host>/ws/transcribe`
- Send JSON: `{"url":"<video_url>", "language":"en"}`
- You will receive a queued acknowledgement `{ "status": "queued", "job_id": "..." }` and later the final result message with `status: done`.

Python WebSocket example:

```python
import asyncio, json, websockets

async def test_url():
    uri = "ws://localhost:8000/ws/transcribe"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"url": "https://youtu.be/abbeIUOCzmw?si=RvEm_Ngfsg1SH-Ei", "language": "en"}))
        while True:
            msg = json.loads(await ws.recv())
            print(msg["status"], msg.get("job_id", ""))
            if msg["status"] in ("done", "error"):
                print(msg.get("full_text", msg.get("detail")))
                break

asyncio.run(test_url())
```

HTTP polling: poll `GET /job/{job_id}` to check status if you prefer not to use WebSockets.

---

## 6. YouTube captions vs Whisper fallback

The transcriber attempts the following, in order:

1. Use `youtube-transcript-api` to fetch YouTube captions (fast, no cost).
2. If captions are unavailable, disabled, or `youtube-transcript-api` isn't installed, fall back to `faster-whisper`:
   - The worker downloads audio via `yt-dlp` and runs Whisper locally.
   - Whisper is CPU/GPU-sensitive — the repo attempts to auto-detect a CUDA device and uses `int8` quantization on CPU for speed.

If you don't want Whisper fallback in a given environment, remove `faster-whisper` / `yt-dlp` from `requirements.txt` or set up the environment without them.

---

## 7. Verifying Redis queue activity

```bash
# Watch live key activity
redis-cli monitor

# Check stored job results
redis-cli keys "result:*"
redis-cli get "result:<job_id>"

# See ARQ's internal job keys
redis-cli keys "arq:*"
```

---

## 8. Common issues

| Symptom                              | Fix                                                            |
| ------------------------------------ | -------------------------------------------------------------- |
| `Connection refused` on Redis        | Ensure `REDIS_URL` is correct and reachable                    |
| Worker not picking up jobs           | Check `arq app.queue.worker.WorkerSettings` is running         |
| `faster-whisper` model download slow | First run only; model cached at `~/.cache/huggingface`         |
| YouTube 403 error                    | Set `YTDLP_COOKIEFILE` in `.env` to a valid cookies file       |
| WebSocket closes immediately         | Check FastAPI logs – likely a JSON parse error in your payload |

---

## 9. Adding TranscriptHQ (non-YouTube videos)

1. Sign up at https://transcripthq.com
2. Set your API key via `TRANSCRIPTHQ_API_KEY` environment variable
3. Implement/adjust `app/transcribers/transcripthq.py` to match their API if needed

---

## 10. Deploying to Coolify / Contabo

Steps (high level):

1. Create a managed Redis instance in Contabo and copy the connection URL.
2. In Coolify, create a new application using this repository and choose Docker Compose deployment.
3. Add environment variables in Coolify (notably `REDIS_URL` and optionally `TRANSCRIPTHQ_API_KEY`, `JOB_TIMEOUT`).
4. Deploy — Coolify will build images and start `web` and `worker` services.

If you'd like, I can provide a short `coolify.md` with step-by-step screenshots and env examples.

---

## 11. License & notes

This project is provided as-is. When deploying to production, consider GPU-enabled workers for faster Whisper transcription and persistent object storage for large file uploads.
