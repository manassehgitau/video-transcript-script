"""
ARQ task definitions.

Each function here is an async task that ARQ picks up from the Redis queue and
runs in the worker process. When a task finishes (or fails) the worker:
  1. Stores the result/error as JSON in a Redis key  ``result:<job_id>``
  2. Publishes a notification to the Redis pub/sub channel  ``job:<job_id>``

The WebSocket handler in main.py subscribes to that channel so it can push the
result to the connected client the moment it is ready.
"""

import json
import logging
import os
import tempfile
from typing import Any

import redis.asyncio as aioredis

from app.config import settings
from app.transcribers.youtube import YouTubeTranscriber
# from app.transcribers.transcripthq import TranscriptHQTranscriber
from app.transcribers.local_file import LocalFileTranscriber
from app.utils import detect_source, VideoSource

logger = logging.getLogger("app.tasks")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _publish_result(redis: aioredis.Redis, job_id: str, payload: dict) -> None:
    """Store result and notify any waiting WebSocket subscriber."""
    encoded = json.dumps(payload)
    await redis.set(f"result:{job_id}", encoded, ex=settings.result_ttl)
    await redis.publish(f"job:{job_id}", encoded)


# ---------------------------------------------------------------------------
# Task: transcribe from URL
# ---------------------------------------------------------------------------

async def transcribe_url(ctx: dict, job_id: str, url: str, language: str = "en") -> dict:
    """
    ARQ task – transcribe a video URL (YouTube or other).

    ``ctx["redis"]`` is the shared aioredis connection provided by ARQ.
    """
    redis: aioredis.Redis = ctx["redis"]
    logger.info("[job:%s] Starting URL transcription: %s", job_id, url)

    try:
        source = detect_source(url)

        if source == VideoSource.YOUTUBE:
            transcriber = YouTubeTranscriber()
        # elif source == VideoSource.OTHER:
        #     transcriber = TranscriptHQTranscriber()
        else:
            raise ValueError(f"Unsupported video URL: {url}")

        # Run the (blocking) transcription in a thread so the event loop stays free
        import asyncio
        result_segments = await asyncio.get_event_loop().run_in_executor(
            None, lambda: transcriber.transcribe(url, language=language)
        )

        payload = {
            "status": "done",
            "job_id": job_id,
            "url": url,
            "source": source.value,
            "language": language,
            "full_text": " ".join(seg["text"] for seg in result_segments),
            "segments": result_segments,
        }
        logger.info("[job:%s] URL transcription complete (%d segments)", job_id, len(result_segments))

    except Exception as exc:
        logger.exception("[job:%s] URL transcription failed: %s", job_id, exc)
        payload = {"status": "error", "job_id": job_id, "detail": str(exc)}

    await _publish_result(redis, job_id, payload)
    return payload


# ---------------------------------------------------------------------------
# Task: transcribe from uploaded file bytes
# ---------------------------------------------------------------------------

async def transcribe_file(
    ctx: dict,
    job_id: str,
    filename: str,
    file_bytes: bytes,
    language: str = "en",
) -> dict:
    """
    ARQ task – transcribe a locally uploaded audio/video file.

    The raw bytes are passed through Redis (fine for typical video clips; for
    very large files consider storing to disk / object storage instead and
    passing only a path).
    """
    redis: aioredis.Redis = ctx["redis"]
    logger.info("[job:%s] Starting file transcription: %s (%d bytes)", job_id, filename, len(file_bytes))

    temp_path = None
    try:
        suffix = os.path.splitext(filename)[1] or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name

        import asyncio
        transcriber = LocalFileTranscriber()
        result_segments = await asyncio.get_event_loop().run_in_executor(
            None, lambda: transcriber.transcribe(temp_path, language=language)
        )

        payload = {
            "status": "done",
            "job_id": job_id,
            "url": filename,
            "source": "local_file",
            "language": language,
            "full_text": " ".join(seg["text"] for seg in result_segments),
            "segments": result_segments,
        }
        logger.info("[job:%s] File transcription complete (%d segments)", job_id, len(result_segments))

    except Exception as exc:
        logger.exception("[job:%s] File transcription failed: %s", job_id, exc)
        payload = {"status": "error", "job_id": job_id, "detail": str(exc)}

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    await _publish_result(redis, job_id, payload)
    return payload
