from enum import Enum
from urllib.parse import urlparse, parse_qs
import re


class VideoSource(str, Enum):
    YOUTUBE = "youtube"
    OTHER = "transcripthq"


YOUTUBE_PATTERNS = [
    r"youtube\.com/watch",
    r"youtu\.be/",
    r"youtube\.com/shorts/",
    r"youtube\.com/embed/",
]


def detect_source(url: str) -> VideoSource:
    for pattern in YOUTUBE_PATTERNS:
        if re.search(pattern, url):
            return VideoSource.YOUTUBE
    return VideoSource.OTHER


def extract_youtube_video_id(url: str) -> str:
    """Extract the video ID from any YouTube URL format."""
    parsed = urlparse(url)

    # youtu.be/VIDEO_ID
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("?")[0]

    # youtube.com/shorts/VIDEO_ID
    if "/shorts/" in parsed.path:
        return parsed.path.split("/shorts/")[1].split("/")[0]

    # youtube.com/embed/VIDEO_ID
    if "/embed/" in parsed.path:
        return parsed.path.split("/embed/")[1].split("/")[0]

    # youtube.com/watch?v=VIDEO_ID
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]

    raise ValueError(f"Could not extract YouTube video ID from URL: {url}")
