#!/bin/bash
set -e

# Start bgutil PO token server in background (needed for yt-dlp YouTube downloads)
node /opt/bgutil/server/build/main.js &
BGUTIL_PID=$!
echo "bgutil server started (PID $BGUTIL_PID)"

# Cloud Run requires the container to listen on $PORT for health checks.
# Start a minimal HTTP health-check server in the background.
PORT="${PORT:-8080}"
python -c "
import http.server, socketserver, threading

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *args):
        pass  # silence logs

with socketserver.TCPServer(('', ${PORT}), HealthHandler) as httpd:
    print('Health-check server listening on port ${PORT}')
    httpd.serve_forever()
" &
HEALTH_PID=$!
echo "Health-check server started (PID $HEALTH_PID)"

echo "Starting ARQ worker ..."
exec arq app.queue.worker.WorkerSettings
