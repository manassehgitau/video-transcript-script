#!/bin/bash
set -e

# Start bgutil PO token server in background (needed for yt-dlp YouTube downloads)
node /opt/bgutil/server/build/main.js &
BGUTIL_PID=$!
echo "bgutil server started (PID $BGUTIL_PID)"

echo "Starting ARQ worker ..."
exec arq app.queue.worker.WorkerSettings
