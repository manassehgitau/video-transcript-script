#!/bin/bash
# Start bgutil PO token server in background
npx bgutil-ytdlp-pot-provider serve &

# Start your app
exec uvicorn app.main:app --host 0.0.0.0 --port 8000