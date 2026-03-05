FROM python:3.12-slim

# Install system deps in a single layer to reduce image size.
# Use --no-install-recommends to avoid extra packages.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x from NodeSource (required by bgutil provider)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /service

# Copy dependency manifest first so Docker can cache installs
COPY requirements.txt ./

# Upgrade pip, install Python requirements without cache
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Install small helper packages after base requirements to avoid invalidating cache
RUN pip install --no-cache-dir yt-dlp-get-pot bgutil-ytdlp-pot-provider

# Copy application code
COPY app/ ./app/

# Copy start script and make executable
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Create a non-root user and use it for running the app
RUN useradd --create-home --shell /bin/false appuser && chown -R appuser:appuser /service /start.sh

# Environment for better Python behavior in containers
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

EXPOSE 8000

# Use the start script as entrypoint (start.sh should run the server or manage env setup)
USER appuser
ENTRYPOINT ["/start.sh"]
