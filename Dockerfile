FROM python:3.12-slim

# Unbuffered output so logs show up immediately in container logs
ENV PYTHONUNBUFFERED=1

# ffmpeg for audio transcoding, libopus for Discord voice encoding
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libopus0 && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run as an unprivileged user
RUN useradd --create-home appuser
USER appuser

# Run the bot
CMD ["python", "app.py"]
