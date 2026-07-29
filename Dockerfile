# Pinned by digest for reproducible builds (Dependabot bumps it weekly);
# the digest is the multi-arch manifest list for python:3.12-slim.
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

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

# Optional: audio files mounted here become bot commands (skill.m4a ->
# !skill); see "Custom sounds" in the README. Absent directory = no customs.
ENV CUSTOM_SOUNDS_DIR=/custom-sounds

# The bot touches this file every 30s while its Discord connection is up
# (see _heartbeat in app.py); a stale file fails the health check, so
# "unhealthy" means "not connected to Discord", not just "process died".
ENV HEALTH_FILE=/tmp/skill-issue-bot-healthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, sys, time; sys.exit(0 if time.time() - os.path.getmtime(os.environ['HEALTH_FILE']) < 90 else 1)"]

# Run the bot
CMD ["python", "app.py"]
