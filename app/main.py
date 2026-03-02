from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Response
import logging
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import tempfile
from app.transcribers.youtube import YouTubeTranscriber
from app.transcribers.transcripthq import TranscriptHQTranscriber
from app.transcribers.local_file import LocalFileTranscriber
from app.utils import detect_source, VideoSource

app = FastAPI(
    title="Video Transcription Service",
    description="Transcription API for AI agents. Supports YouTube (and TranscriptHQ for other sources).",
    version="1.0.0",
)

# Configure logging for the app
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request / Response models ---
# https://www.youtube.com/watch?v=GPdraLtOGI4
class TranscribeRequest(BaseModel):
    url: str
    language: str = "en"  # preferred language for transcript


class TranscriptSegment(BaseModel):
    start: float       # seconds from start of video
    duration: float    # seconds
    text: str


class TranscribeResponse(BaseModel):
    url: str
    source: str                        # "youtube" | "transcripthq"
    language: str
    full_text: str                     # entire transcript as one string (easiest for AI agents)
    segments: list[TranscriptSegment]  # timestamped chunks if needed


# --- Routes ---

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcribe", response_model=TranscribeResponse)
def transcribe(request: TranscribeRequest, response: Response, debug: bool = False):
    """
    Main endpoint. Feed in a video URL, get back the full transcript.
    The AI agent should call this and read `full_text` for context.
    """
    source = detect_source(request.url)

    if source == VideoSource.YOUTUBE:
        transcriber = YouTubeTranscriber()
    elif source == VideoSource.OTHER:
        transcriber = TranscriptHQTranscriber()
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported video URL: {request.url}")

    try:
        result = transcriber.transcribe(request.url, language=request.language)
        # expose which method was used for debugging/diagnostics
        method = getattr(transcriber, "_last_method", None)
        if method:
            response.headers["X-Transcription-Method"] = method
    except Exception as e:
        error_msg = str(e)
        logger.exception("Error during transcription for %s: %s", request.url, error_msg)
        
        # Handle common error patterns with user-friendly messages
        if "no element found" in error_msg.lower() or "parsing" in error_msg.lower():
            detail = (
                "YouTube blocked or restricted access to this video. "
                "The video may be region-restricted, age-restricted, or have download protections. "
                "Try a different video or check that captions are enabled."
            )
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            detail = (
                "YouTube blocked this download (HTTP 403). "
                "The video may have content protection or geographic restrictions."
            )
        elif "format" in error_msg.lower() or "not available" in error_msg.lower():
            detail = (
                "No downloadable formats available for this video. "
                "Try a different video or check if captions are available."
            )
        else:
            detail = error_msg if error_msg else "Transcription failed. Please try again."

        # If debug flag provided, include raw internal error text for troubleshooting
        if debug:
            detail = f"{detail} (internal: {error_msg})"

        raise HTTPException(status_code=422, detail=detail)

    return TranscribeResponse(
        url=request.url,
        source=source.value,
        language=request.language,
        full_text=" ".join(seg["text"] for seg in result),
        segments=[
            TranscriptSegment(
                start=seg["start"],
                duration=seg["duration"],
                text=seg["text"],
            )
            for seg in result
        ],
    )


@app.post("/transcribe-file", response_model=TranscribeResponse)
async def transcribe_file(file: UploadFile = File(...), language: str = Form("en")):
    """
    Transcribe a local audio or video file.
    
    Supports: mp3, mp4, wav, webm, mkv, m4a, aac, opus, flac, ogg, and more.
    
    Args:
        file: Audio or video file (multipart upload)
        language: Language code (default: "en")
    
    Returns:
        Transcription with segments and full text.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    temp_file_path = None
    try:
        # Save uploaded file to temp directory
        temp_file = tempfile.NamedTemporaryFile(
            suffix=os.path.splitext(file.filename)[1] or ".tmp",
            delete=False,
            dir=tempfile.gettempdir()
        )
        temp_file_path = temp_file.name
        
        # Write uploaded file content to temp file
        content = await file.read()
        temp_file.write(content)
        temp_file.close()
        
        # Transcribe using local file transcriber
        transcriber = LocalFileTranscriber()
        try:
            result = transcriber.transcribe(temp_file_path, language=language)
            method = getattr(transcriber, "_last_method", None)
            if method:
                response.headers["X-Transcription-Method"] = method
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        
        # Return response
        return TranscribeResponse(
            url=file.filename,
            source="local_file",
            language=language,
            full_text=" ".join(seg["text"] for seg in result),
            segments=[
                TranscriptSegment(
                    start=seg["start"],
                    duration=seg["duration"],
                    text=seg["text"],
                )
                for seg in result
            ],
        )
    
    finally:
        # Clean up temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                print(f"Warning: Failed to delete temp file {temp_file_path}: {e}")
