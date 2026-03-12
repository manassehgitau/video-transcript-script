#!/bin/bash
set -e

# Start bgutil PO token server in background (needed for yt-dlp YouTube downloads)
npx bgutil-ytdlp-pot-provider serve &
BGUTIL_PID=$!
echo "bgutil server started (PID $BGUTIL_PID)"

echo "Starting ARQ worker ..."
exec arq app.queue.worker.WorkerSettings
