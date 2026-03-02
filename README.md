# Video Transcription Service

A lightweight REST API for video transcription, designed for AI agents.

- **YouTube** → `youtube-transcript-api` (free, no API key needed)
- **Other videos** → TranscriptHQ (set `TRANSCRIPTHQ_API_KEY`)

---

## Quick Start

```bash
# Option A: Docker (recommended)
docker-compose up --build

# Option B: Local
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API will be live at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

---

## API Usage

### `POST /transcribe`

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "language": "en"
}
```

**Response:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "source": "youtube",
  "language": "en",
  "full_text": "We're no strangers to love...",
  "segments": [
    { "start": 0.0, "duration": 3.2, "text": "We're no strangers to love" },
    ...
  ]
}
```

### AI Agent Integration

Your agent should call `POST /transcribe` and read the `full_text` field for context. Example (Python):

```python
import requests

def get_video_transcript(url: str) -> str:
    response = requests.post(
        "http://localhost:8000/transcribe",
        json={"url": url, "language": "en"},
    )
    response.raise_for_status()
    return response.json()["full_text"]
```

### cURL example
```bash
curl -X POST http://localhost:8000/transcribe \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtu.be/dQw4w9WgXcQ"}'
```

---

## Supported URL Formats

All standard YouTube URL formats are supported:
- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/shorts/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`

---

## Adding TranscriptHQ (non-YouTube videos)

1. Sign up at [TranscriptHQ](https://transcripthq.com)
2. Set your API key: `export TRANSCRIPTHQ_API_KEY=your_key_here`
3. Update `app/transcribers/transcripthq.py` with the actual API endpoint/response shape from their docs
4. Pass any non-YouTube URL to `/transcribe` — it will automatically route to TranscriptHQ

---

## Deploy to Production

### Railway / Render / Fly.io
These platforms all support Docker. Just connect your repo and set the `TRANSCRIPTHQ_API_KEY` env var in their dashboard.

### Environment Variables
| Variable | Required | Description |
|---|---|---|
| `TRANSCRIPTHQ_API_KEY` | No (for now) | Needed for non-YouTube video transcription |
