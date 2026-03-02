"""
Transcriber for local audio/video files using Whisper.
Supports any format that FFmpeg can decode (mp3, mp4, wav, webm, mkv, etc).
"""

try:
    from faster_whisper import WhisperModel  # type: ignore[import]
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# Module-level Whisper model (shared across requests)
_whisper_model = None
if WHISPER_AVAILABLE:
    try:
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    except Exception as e:
        print(f"Warning: Failed to initialize Whisper model: {e}")


class LocalFileTranscriber:
    """
    Transcribes local audio/video files using offline Whisper.
    Supports any format that FFmpeg can handle.
    """

    def transcribe(self, file_path: str, language: str = "en") -> list[dict]:
        """
        Transcribes a local audio/video file.
        
        Args:
            file_path: Path to the audio/video file
            language: Language code (e.g., 'en', 'es', 'fr')
        
        Returns:
            List of segments in standard format:
                [{"text": "...", "start": 0.0, "duration": 3.2}, ...]
        """
        if not WHISPER_AVAILABLE or _whisper_model is None:
            raise ValueError(
                "Whisper is not available. Install faster-whisper to enable local file transcription."
            )
        
        try:
            # Transcribe using Whisper
            segments, _ = _whisper_model.transcribe(file_path, language=language)
            try:
                self._last_method = "local_file"
            except Exception:
                pass
            # Normalize to standard format
            return [
                {
                    "text": seg.text,
                    "start": seg.start,
                    "duration": seg.end - seg.start,
                }
                for seg in segments
            ]
        
        except Exception as e:
            raise ValueError(f"Whisper transcription failed: {str(e)}")
