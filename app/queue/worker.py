"""
ARQ worker entry point.

Start the worker locally with:

    arq app.queue.worker.WorkerSettings

The worker connects to Redis, pulls jobs off the queue, and calls the task
functions defined in app/queue/tasks.py.
"""

import logging

import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.queue.tasks import transcribe_url, transcribe_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _parse_redis_settings(url: str) -> RedisSettings:
    """Convert a redis:// URL into an ARQ RedisSettings object."""
    # arq.RedisSettings can take host/port/db/password but not a URL directly,
    # so we parse it manually.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or 0),
        password=parsed.password or None,
    )


class WorkerSettings:
    """ARQ worker configuration."""

    # Tasks this worker can handle
    functions = [transcribe_url, transcribe_file]

    # Redis connection (shared with the FastAPI app)
    redis_settings = _parse_redis_settings(settings.redis_url)

    # How long (seconds) a job may run before it is cancelled
    job_timeout = settings.job_timeout

    # Keep finished job results for this many seconds
    keep_result = settings.result_ttl

    # Number of parallel jobs this worker runs (increase if CPU-bound work allows)
    max_jobs = 4

    # Called once when the worker starts – create any shared resources here
    async def on_startup(ctx: dict) -> None:
        ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)

    # Called once when the worker shuts down
    async def on_shutdown(ctx: dict) -> None:
        await ctx["redis"].aclose()
