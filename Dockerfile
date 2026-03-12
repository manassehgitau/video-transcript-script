# ─────────────────────────────────────────────
# Stage 1: Python dependencies
# ─────────────────────────────────────────────
FROM python:3.11-slim AS python-deps

WORKDIR /app

# System packages needed by faster-whisper / yt-dlp / ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────
# Stage 2: Final image (Python + Node.js)
# ─────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 (required by bgutil-ytdlp-pot-provider)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from build stage
COPY --from=python-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Install bgutil-ytdlp-pot-provider globally from the GitHub repo
# (package isn't available in the public npm registry)
RUN npm install -g git+https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git

# Copy application code
COPY . .

# Pre-download the faster-whisper model so container starts quickly
# Comment this out if you want a smaller image and are OK with first-run delay
# RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu')"

# Make start scripts executable
RUN chmod +x start.sh start-worker.sh

# Cloud Run injects PORT env var; default to 8000
ENV PORT=8000

# Default: run the web server
# Override CMD to "worker" when deploying the worker service
CMD ["./start.sh"]
