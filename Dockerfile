# Pinned by digest for reproducible builds (Dependabot bumps it weekly);
# the digest is the multi-arch manifest list for python:3.12-slim.
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# Unbuffered output so logs show up immediately in container logs
ENV PYTHONUNBUFFERED=1

# ffmpeg for audio transcoding, libopus for Discord voice encoding
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libopus0 && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install the exact pinned set CI tested
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Copy the rest of the application code
COPY . .

# Run as an unprivileged user
RUN useradd --create-home appuser
USER appuser

# The bot touches this file every 30s while its Discord connection is up
# (see _heartbeat in app.py); a stale file fails the health check, so
# "unhealthy" means "not connected to Discord", not just "process died".
ENV HEALTH_FILE=/tmp/skill-issue-bot-healthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, sys, time; sys.exit(0 if time.time() - os.path.getmtime(os.environ['HEALTH_FILE']) < 90 else 1)"]

# Run the bot
CMD ["python", "app.py"]
