"""
TranscriptHQ transcriber disabled.

The external TranscriptHQ integration has been intentionally disabled
because local/offline transcription via `faster-whisper` is the preferred
method in this deployment. The original implementation is available in
version control if you need to restore it.
"""

import logging


class TranscriptHQTranscriber:
    """Disabled stub for TranscriptHQ integration."""

    def __init__(self, *_, **__):
        self._last_method = "transcripthq_disabled"

    def transcribe(self, url: str, language: str = "en") -> list[dict]:
        raise ValueError(
            "TranscriptHQ integration is disabled. Use YouTubeTranscriber or upload a local file via /transcribe-file."
        )
