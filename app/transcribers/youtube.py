import tempfile
import os
import logging
import glob
import shutil
try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    YT_TRANSCRIPT_AVAILABLE = True
except Exception:
    YouTubeTranscriptApi = None
    NoTranscriptFound = Exception
    TranscriptsDisabled = Exception
    YT_TRANSCRIPT_AVAILABLE = False
from app.utils import extract_youtube_video_id

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

# Suppress yt-dlp's verbose logging
yt_dlp_logger = logging.getLogger('yt_dlp')
yt_dlp_logger.setLevel(logging.CRITICAL)

# Module logger
logger = logging.getLogger("app.youtube")


def _detect_device() -> tuple[str, str, int]:
    """Detect best device for Whisper inference.
    
    Returns (device, compute_type, cpu_threads).
    - GPU (CUDA): device="cuda", compute_type="float16", cpu_threads=1
    - CPU: device="cpu", compute_type="int8", cpu_threads=max cores-1
    """
    import os
    cpu_threads = max(1, (os.cpu_count() or 4) - 1)
    
    try:
        import torch
        if torch.cuda.is_available():
            logger.info("CUDA GPU detected. Using device=cuda, compute_type=float16")
            return "cuda", "float16", 1
    except ImportError:
        pass
    
    logger.info("Using CPU with int8 quantization, cpu_threads=%d", cpu_threads)
    return "cpu", "int8", cpu_threads


# Initialize Whisper model once at module level for reuse across requests
_whisper_model = None
_whisper_device = "cpu"
_whisper_compute_type = "int8"
_whisper_cpu_threads = 4

if WHISPER_AVAILABLE:
    try:
        _whisper_device, _whisper_compute_type, _whisper_cpu_threads = _detect_device()
        _whisper_model = WhisperModel(
            "small",
            device=_whisper_device,
            compute_type=_whisper_compute_type,
            cpu_threads=_whisper_cpu_threads,
        )
        logger.info("Whisper model loaded: device=%s, compute_type=%s, cpu_threads=%d",
                    _whisper_device, _whisper_compute_type, _whisper_cpu_threads)
    except Exception as e:
        print(f"Warning: Failed to initialize Whisper model: {e}. Fallback to YouTube captions only.")


class YouTubeTranscriber:
    """
    Fetches transcripts directly from YouTube using youtube-transcript-api.
    No external API key required — uses YouTube's built-in caption system.
    """

    def transcribe(self, url: str, language: str = "en") -> list[dict]:
        """
        Returns a list of segments:
            [{"text": "...", "start": 0.0, "duration": 3.2}, ...]
        
        Falls back to offline Whisper transcription if YouTube captions are unavailable.
        """
        video_id = extract_youtube_video_id(url)
        logger.info("transcribe called for url=%s video_id=%s language=%s", url, video_id, language)

        # If the youtube-transcript-api package isn't installed, skip directly to Whisper
        if not YT_TRANSCRIPT_AVAILABLE:
            logger.info("youtube-transcript-api not installed; using Whisper fallback for %s", video_id)
            return self._transcribe_with_whisper(url, language)

        # Step 1: Try YouTube captions first
        try:
            # Try to get transcript in requested language first
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id,
                languages=[language, "en"],  # fallback to English
            )
            logger.info("Found YouTube captions for %s (language=%s)", video_id, language)
            # mark method used
            try:
                self._last_method = "captions"
            except Exception:
                pass
            return transcript

        except NoTranscriptFound:
            logger.info("No direct captions found, checking available transcripts for %s", video_id)
            # Try fetching any available transcript and translating it
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                for t in transcript_list:
                    try:
                        if t.is_translatable:
                            translated = t.translate(language).fetch()
                            logger.info("Translated captions available for %s", video_id)
                            try:
                                self._last_method = "captions_translated"
                            except Exception:
                                pass
                            return translated
                    except Exception:
                        continue
            except Exception:
                pass

            # YouTube captions unavailable, fall back to Whisper
            logger.info("Falling back to Whisper for %s", video_id)
            return self._transcribe_with_whisper(url, language)

        except TranscriptsDisabled:
            # Captions explicitly disabled, fall back to Whisper
            logger.info("Transcripts disabled for %s; falling back to Whisper", video_id)
            return self._transcribe_with_whisper(url, language)

        except Exception as e:
            # Catch parsing/network errors from youtube_transcript_api (e.g. empty responses
            # that cause ElementTree ParseError) and fall back to Whisper instead of
            # bubbling the exception to the caller.
            logger.warning("Error fetching captions for %s via youtube-transcript-api: %s", video_id, e)
            return self._transcribe_with_whisper(url, language)

    def _transcribe_with_whisper(self, url: str, language: str = "en") -> list[dict]:
        """
        Fallback transcription using offline faster-whisper.
        Downloads audio from YouTube and transcribes locally.
        
        Returns segments in standard format:
            [{"text": "...", "start": 0.0, "duration": 3.2}, ...]
        """
        if not WHISPER_AVAILABLE or _whisper_model is None:
            logger.error("Whisper model not available for offline fallback")
            raise ValueError(
                "Whisper fallback unavailable. Install faster-whisper and yt-dlp packages."
            )
        
        if not YTDLP_AVAILABLE:
            raise ValueError(
                "yt-dlp not available. Install yt-dlp to enable audio download fallback."
            )
        
        temp_audio_path = None
        logger.info("Starting offline Whisper fallback for url=%s", url)
        temp_audio_dir = None
        try:
            # Step 1: Download audio to temp file
            temp_audio_path, temp_audio_dir = self._download_audio(url)
            logger.info("Downloaded audio to %s", temp_audio_path)
            
            # Step 2: Transcribe using Whisper with speed optimizations
            # - beam_size=1: greedy decoding (fastest, minor accuracy tradeoff)
            # - vad_filter=True: skip silence (big speedup on videos with pauses)
            logger.info("Starting Whisper transcription for %s (beam_size=1, vad_filter=True)", temp_audio_path)
            segments, info = _whisper_model.transcribe(
                temp_audio_path,
                language=language,
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            logger.info("Audio duration: %.1fs", info.duration)
            # faster-whisper may return a generator/iterator for segments; materialize to list
            try:
                segments = list(segments)
            except TypeError:
                # If it's already a list-like object, just keep it
                pass
            try:
                self._last_method = "whisper"
            except Exception:
                pass
            logger.info("Whisper produced %d segments", len(segments))
            
            # Step 3: Normalize to standard format
            return [
                {
                    "text": seg.text,
                    "start": seg.start,
                    "duration": seg.end - seg.start,
                }
                for seg in segments
            ]
        
        except ValueError as e:
            # Re-raise ValueError (download-specific errors with clear messages)
            raise e
        
        except Exception as e:
            # Catch all other errors from Whisper or file operations
            raise ValueError(
                f"Whisper transcription failed: {str(e)}. "
                f"This may indicate an issue with the downloaded audio file or Whisper model."
            )
        
        finally:
            # Step 4: Always clean up temp file
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except Exception as e:
                    print(f"Warning: Failed to delete temp audio file {temp_audio_path}: {e}")
            if temp_audio_dir and os.path.isdir(temp_audio_dir):
                try:
                    shutil.rmtree(temp_audio_dir, ignore_errors=True)
                except Exception as e:
                    print(f"Warning: Failed to delete temp audio dir {temp_audio_dir}: {e}")

    def _download_audio(self, url: str) -> tuple[str, str]:
        """
        Downloads audio from YouTube URL to a temporary directory.
        Returns `(audio_file_path, temp_dir)`.
        """
        # Use a dedicated temp directory; don't pre-create output file names.
        temp_dir = tempfile.mkdtemp(prefix="yt_audio_")
        outtmpl = os.path.join(temp_dir, "audio.%(ext)s")
        
        try:
            logger.info("Starting yt-dlp download for %s into %s", url, temp_dir)

            # Allow user to provide cookies for authenticated/download-protected videos.
            # Supported options (priority order):
            # - YTDLP_COOKIEFILE: path to a cookies.txt file already available inside the container
            # - YTDLP_COOKIE_B64: base64-encoded cookies.txt content (useful for secrets)
            # - YTDLP_COOKIES: raw cookies.txt content (multiline allowed)
            cookiefile = os.environ.get("YTDLP_COOKIEFILE")
            cookie_b64 = os.environ.get("YTDLP_COOKIE_B64")
            cookie_content = os.environ.get("YTDLP_COOKIES")

            # Common http headers to resemble a real browser
            http_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com/",
            }

            # Helper logger to capture yt-dlp internal messages for diagnostics
            class _YTDLogger:
                def debug(self, msg):
                    logger.debug(msg)
                def info(self, msg):
                    logger.info(msg)
                def warning(self, msg):
                    logger.warning(msg)
                def error(self, msg):
                    logger.error(msg)

            # Primary options
            primary_opts = {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "outtmpl": outtmpl,
                "http_headers": http_headers,
                "socket_timeout": 10,
                "retries": 1,
                "socket_family": 4,
                "skip_unavailable_fragments": True,
                "nocheckcertificate": True,
                "geo_bypass": True,
                "logger": _YTDLogger(),
                "progress_hooks": [self._progress_hook],
            }

            # If a base64 or raw cookie is provided, write it into the temp_dir for yt-dlp to use.
            if cookie_b64:
                try:
                    import base64
                    cookie_path = os.path.join(temp_dir, "cookies.txt")
                    with open(cookie_path, "wb") as cf:
                        cf.write(base64.b64decode(cookie_b64))
                    primary_opts["cookiefile"] = cookie_path
                except Exception as e:
                    logger.warning("Failed to decode YTDLP_COOKIE_B64: %s", e)
            elif cookie_content:
                try:
                    cookie_path = os.path.join(temp_dir, "cookies.txt")
                    with open(cookie_path, "w", encoding="utf-8") as cf:
                        cf.write(cookie_content)
                    primary_opts["cookiefile"] = cookie_path
                except Exception as e:
                    logger.warning("Failed to write YTDLP_COOKIES content: %s", e)
            elif cookiefile:
                primary_opts["cookiefile"] = cookiefile

            # Fallback options if primary attempt fails (try a more permissive extractor)
            fallback_opts = dict(primary_opts)
            fallback_opts.update({
                "format": "bestaudio/best",
                "noplaylist": True,
                "force_generic_extractor": True,
                "allow_unplayable_formats": True,
            })

            attempts = [primary_opts, fallback_opts]

            last_exception = None
            for attempt_no, opts in enumerate(attempts, start=1):
                print(f"[Transcriber] yt-dlp attempt {attempt_no} for {url}")
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])
                    # If download didn't raise, break out and continue to file discovery
                    last_exception = None
                    break
                except Exception as e:
                    last_exception = e
                    logger.warning("yt-dlp attempt %d failed: %s", attempt_no, str(e))
                    # on failure, try the next attempt
                    continue

            if last_exception:
                error_str = str(last_exception).lower()
                if "403" in error_str or "forbidden" in error_str:
                    raise ValueError("YouTube blocked this download (HTTP 403 - Access Forbidden)")
                elif "not available" in error_str or "format" in error_str:
                    raise ValueError("No downloadable formats available for this video")
                elif "no element found" in error_str or "parsing" in error_str:
                    raise ValueError("YouTube blocked or restricted access to this video")
                elif "timeout" in error_str or "timed out" in error_str:
                    raise ValueError("Download timed out. The video may be blocked or your connection is slow.")
                else:
                    # Attach captured logger output to help debugging
                    logger.exception("yt-dlp final failure for %s: %s", url, last_exception)
                    raise ValueError(f"Download failed: {str(last_exception)}")

            logger.info("Searching downloaded files in %s", temp_dir)
            # Find the downloaded media file in temp dir.
            candidates = [
                p
                for p in glob.glob(os.path.join(temp_dir, "audio.*"))
                if os.path.isfile(p)
            ]
            if not candidates:
                raise FileNotFoundError("Downloaded file not found")

            # Prefer common audio extensions first.
            preferred_order = [".mp3", ".m4a", ".wav", ".webm", ".aac", ".opus", ".ogg", ".flac", ".mp4", ".mkv"]
            def sort_key(path: str) -> int:
                ext = os.path.splitext(path)[1].lower()
                return preferred_order.index(ext) if ext in preferred_order else len(preferred_order)

            candidates.sort(key=sort_key)
            print(f"[Transcriber] Downloaded to {candidates[0]}")
            logger.info("Selected downloaded file %s", candidates[0])
            return candidates[0], temp_dir
        
        except ValueError:
            # Re-raise our custom ValueError
            logger.warning("Download failed, cleaning temp dir %s", temp_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        
        except Exception as e:
            # Catch any other exception
            logger.exception("Unexpected error during download: %s", e)
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(f"Unexpected error: {str(e)}")

    def _progress_hook(self, d: dict) -> None:
        """Progress hook for yt-dlp to log download status."""
        if d['status'] == 'downloading':
            print(f"[Transcriber] Downloading: {d.get('_percent_str', 'N/A')} at {d.get('_speed_str', 'N/A')}")
        elif d['status'] == 'finished':
            print(f"[Transcriber] Download finished, now processing...")

