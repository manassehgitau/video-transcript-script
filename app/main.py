"""
Video Transcription Service – FastAPI application.

New endpoints added on top of the original REST API:

  WS  /ws/transcribe        – submit a URL job via WebSocket, receive result when done
  WS  /ws/transcribe-file   – submit a file upload via WebSocket, receive result when done
  GET /job/{job_id}         – poll the status / result of any enqueued job

The original REST endpoints (/transcribe, /transcribe-file) are kept for
backward compatibility; they still run synchronously (no queue).
"""

import json
import logging
import os
import tempfile
import uuid
import asyncio
from typing import Optional

import redis.asyncio as aioredis
from arq.connections import ArqRedis, create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.transcribers.youtube import YouTubeTranscriber
from app.transcribers.local_file import LocalFileTranscriber
from app.utils import detect_source, VideoSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Video Transcription Service",
    description=(
        "Transcription API for AI agents. Supports YouTube (and TranscriptHQ for other sources).\n\n"
        "Use the WebSocket endpoints for non-blocking, queued transcription."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Redis / ARQ pool (shared across requests)
# ---------------------------------------------------------------------------

def _arq_redis_settings() -> RedisSettings:
    from urllib.parse import urlparse
    parsed = urlparse(settings.redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
        password=parsed.password or None,
    )


_arq_pool: Optional[ArqRedis] = None
_redis_pubsub: Optional[aioredis.Redis] = None


@app.on_event("startup")
async def startup() -> None:
    global _arq_pool, _redis_pubsub
    _arq_pool = await create_pool(_arq_redis_settings())
    # A separate plain redis connection used for pub/sub subscriptions
    _redis_pubsub = aioredis.from_url(settings.redis_url, decode_responses=True)
    logger.info("Redis/ARQ pool ready at %s", settings.redis_url)


@app.on_event("shutdown")
async def shutdown() -> None:
    if _arq_pool:
        await _arq_pool.aclose()
    if _redis_pubsub:
        await _redis_pubsub.aclose()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TranscribeRequest(BaseModel):
    url: str
    language: str = "en"


class TranscriptSegment(BaseModel):
    start: float
    duration: float
    text: str


class TranscribeResponse(BaseModel):
    url: str
    source: str
    language: str
    full_text: str
    segments: list[TranscriptSegment]


class JobQueued(BaseModel):
    job_id: str
    status: str = "queued"
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str               # "queued" | "in_progress" | "done" | "error" | "not_found"
    result: Optional[dict] = None


# ---------------------------------------------------------------------------
# Utility: wait for a job result via Redis pub/sub
# ---------------------------------------------------------------------------

async def _wait_for_result(job_id: str, timeout: int = settings.job_timeout) -> dict:
    """
    Subscribe to the Redis channel ``job:<job_id>`` and block until the worker
    publishes the result (or the timeout expires).

    Falls back to reading the stored key in case the publish happened before we
    subscribed (race condition on very fast jobs).
    """
    channel = f"job:{job_id}"

    # Fast-path: result already stored
    raw = await _redis_pubsub.get(f"result:{job_id}")
    if raw:
        return json.loads(raw)

    # Subscribe and wait (with timeout to avoid hanging connections)
    pubsub = _redis_pubsub.pubsub()
    await pubsub.subscribe(channel)
    try:
        async def _listen_for_message() -> dict:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    return json.loads(message["data"])

        try:
            return await asyncio.wait_for(_listen_for_message(), timeout=timeout)
        except asyncio.TimeoutError:
            # Re-check stored result (in case it was published before we subscribed)
            raw = await _redis_pubsub.get(f"result:{job_id}")
            if raw:
                return json.loads(raw)
            return {"status": "error", "job_id": job_id, "detail": "Timed out waiting for result"}
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# REST endpoints (synchronous – kept for backward compatibility)
# ---------------------------------------------------------------------------

@app.post("/transcribe", response_model=TranscribeResponse)
def transcribe(request: TranscribeRequest, response: Response, debug: bool = False):
    """Synchronous transcription from a video URL (original behaviour)."""
    source = detect_source(request.url)

    if source == VideoSource.YOUTUBE:
        transcriber = YouTubeTranscriber()
    # elif source == VideoSource.OTHER:
    #     transcriber = TranscriptHQTranscriber()
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported video URL: {request.url}")

    try:
        result = transcriber.transcribe(request.url, language=request.language)
        method = getattr(transcriber, "_last_method", None)
        if method:
            response.headers["X-Transcription-Method"] = method
    except Exception as e:
        error_msg = str(e)
        logger.exception("Error during transcription for %s: %s", request.url, error_msg)
        raise HTTPException(status_code=422, detail=error_msg or "Transcription failed.")

    return TranscribeResponse(
        url=request.url,
        source=source.value,
        language=request.language,
        full_text=" ".join(seg["text"] for seg in result),
        segments=[TranscriptSegment(**seg) for seg in result],
    )


@app.post("/transcribe-file", response_model=TranscribeResponse)
async def transcribe_file_rest(file: UploadFile = File(...), language: str = Form("en")):
    """Synchronous transcription from an uploaded file (original behaviour)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    temp_path = None
    try:
        suffix = os.path.splitext(file.filename)[1] or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            temp_path = tmp.name

        transcriber = LocalFileTranscriber()
        result = transcriber.transcribe(temp_path, language=language)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    return TranscribeResponse(
        url=file.filename,
        source="local_file",
        language=language,
        full_text=" ".join(seg["text"] for seg in result),
        segments=[TranscriptSegment(**seg) for seg in result],
    )


# ---------------------------------------------------------------------------
# Job status poll endpoint
# ---------------------------------------------------------------------------

@app.get("/job/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    """
    Poll the status of a queued transcription job.

    Clients that prefer HTTP polling over WebSockets can call this endpoint
    repeatedly until ``status`` is ``"done"`` or ``"error"``.
    """
    raw = await _redis_pubsub.get(f"result:{job_id}")
    if raw:
        data = json.loads(raw)
        return JobStatus(job_id=job_id, status=data.get("status", "done"), result=data)

    # Check if job is in the ARQ queue / in-progress
    try:
        from arq.jobs import Job as ArqJob
    except Exception:
        logger.exception("Failed to import arq.jobs.Job for job status check")
        return JobStatus(job_id=job_id, status="not_found")

    job = ArqJob(job_id, _arq_pool)
    job_info = await job.info()
    if job_info is None:
        return JobStatus(job_id=job_id, status="not_found")

    return JobStatus(job_id=job_id, status="in_progress")


# ---------------------------------------------------------------------------
# WebSocket: URL transcription
# ---------------------------------------------------------------------------

@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket):
    """
    WebSocket endpoint for queued URL transcription.

    Protocol
    --------
    1. Client connects.
    2. Client sends JSON:  {"url": "https://...", "language": "en"}
    3. Server immediately replies: {"status": "queued", "job_id": "<uuid>", "message": "..."}
    4. Server waits for the worker to finish, then sends the full transcript JSON.
    5. Connection closes.

    Example (JavaScript):
        const ws = new WebSocket("ws://localhost:8000/ws/transcribe");
        ws.onopen = () => ws.send(JSON.stringify({url: "https://youtu.be/dQw4w9WgXcQ"}));
        ws.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        url = data.get("url")
        language = data.get("language", "en")

        if not url:
            await websocket.send_json({"status": "error", "detail": "Missing 'url' field"})
            await websocket.close()
            return

        job_id = str(uuid.uuid4())

        # Enqueue the job
        await _arq_pool.enqueue_job(
            "transcribe_url",
            job_id,
            url,
            language,
            _job_id=job_id,
        )
        logger.info("[ws] Queued URL job %s for %s", job_id, url)

        await websocket.send_json({
            "status": "queued",
            "job_id": job_id,
            "message": f"Job queued. Waiting for transcription of: {url}",
        })

        # Block until result is ready, then push it to the client
        result = await _wait_for_result(job_id)
        await websocket.send_json(result)

    except WebSocketDisconnect:
        logger.info("[ws] Client disconnected before result was ready")
    except json.JSONDecodeError:
        await websocket.send_json({"status": "error", "detail": "Invalid JSON"})
    except Exception as exc:
        logger.exception("[ws] Unexpected error: %s", exc)
        try:
            await websocket.send_json({"status": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WebSocket: file transcription
# ---------------------------------------------------------------------------

@app.websocket("/ws/transcribe-file")
async def ws_transcribe_file(websocket: WebSocket):
    """
    WebSocket endpoint for queued file transcription.

    Protocol
    --------
    1. Client connects.
    2. Client sends a JSON metadata frame:
           {"filename": "lecture.mp4", "language": "en", "size": <bytes>}
    3. Server replies: {"status": "ready"}
    4. Client sends the raw file bytes as a binary frame.
    5. Server replies: {"status": "queued", "job_id": "<uuid>", "message": "..."}
    6. Server waits, then sends the full transcript JSON.
    7. Connection closes.

    Example (Python):
        import asyncio, json
        import websockets

        async def upload():
            async with websockets.connect("ws://localhost:8000/ws/transcribe-file") as ws:
                with open("video.mp4", "rb") as f:
                    data = f.read()
                await ws.send(json.dumps({"filename": "video.mp4", "language": "en", "size": len(data)}))
                ack = json.loads(await ws.recv())   # {"status": "ready"}
                await ws.send(data)                 # binary frame
                queued = json.loads(await ws.recv())
                result = json.loads(await ws.recv())
                print(result["full_text"])

        asyncio.run(upload())
    """
    await websocket.accept()
    try:
        # Step 1 – receive metadata
        meta_raw = await websocket.receive_text()
        meta = json.loads(meta_raw)
        filename = meta.get("filename", "upload.tmp")
        language = meta.get("language", "en")

        await websocket.send_json({"status": "ready"})

        # Step 2 – receive binary file
        file_bytes = await websocket.receive_bytes()

        job_id = str(uuid.uuid4())

        # Enqueue – pass raw bytes (fine for typical clips; use object storage for large files)
        await _arq_pool.enqueue_job(
            "transcribe_file",
            job_id,
            filename,
            file_bytes,
            language,
            _job_id=job_id,
        )
        logger.info("[ws] Queued file job %s for %s (%d bytes)", job_id, filename, len(file_bytes))

        await websocket.send_json({
            "status": "queued",
            "job_id": job_id,
            "message": f"Job queued. Transcribing: {filename}",
        })

        result = await _wait_for_result(job_id)
        await websocket.send_json(result)

    except WebSocketDisconnect:
        logger.info("[ws] Client disconnected before result was ready")
    except json.JSONDecodeError:
        await websocket.send_json({"status": "error", "detail": "Invalid JSON in metadata frame"})
    except Exception as exc:
        logger.exception("[ws] File WS unexpected error: %s", exc)
        try:
            await websocket.send_json({"status": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
