#!/bin/bash
set -e

# Start bgutil PO token server in background (needed for yt-dlp YouTube downloads)
node /opt/bgutil/server/build/main.js &
BGUTIL_PID=$!
echo "bgutil server started (PID $BGUTIL_PID)"

# Cloud Run sets the PORT env variable; default to 8000
PORT="${PORT:-8000}"

echo "Starting FastAPI on port $PORT ..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
