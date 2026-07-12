# Skill Issue Bot

A tiny Discord soundboard bot. It joins your voice channel, plays a meme clip
streamed from YouTube, and disconnects when the clip ends (or when everyone
else leaves the channel).

## Commands

| Command  | What it does                        |
|----------|-------------------------------------|
| `!skill` | Plays the "skill" clip              |
| `!ded`   | Plays the "ded" clip                |
| `!leave` | Disconnects the bot from voice      |

Clips live in the `CLIPS` dict at the top of `app.py` — add an entry and a
matching command to extend the soundboard.

## Setup

### 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application.
2. Under **Bot**, copy the bot token.
3. Still under **Bot**, enable the **Message Content Intent** (privileged; the
   `!` commands do not work without it).
4. Under **OAuth2 → URL Generator**, pick the `bot` scope with permissions
   *View Channels*, *Send Messages*, *Connect*, and *Speak*, then use the
   generated URL to invite the bot to your server.

### 2. Configure the token

```bash
cp .env.example .env
# then paste your token into .env
```

The token is read from the `DISCORD_TOKEN` environment variable, so on cloud
platforms you can set it directly instead of shipping a `.env` file. Never
commit the real `.env` (it is git- and docker-ignored).

### 3a. Run with Docker (recommended)

```bash
docker build -t skill-issue-bot .
docker run --env-file .env skill-issue-bot
```

### 3b. Run locally

Requires Python 3.10+ and `ffmpeg` available on your `PATH` (plus libopus,
which ffmpeg installs pull in on most systems).

```bash
pip install -r requirements.txt
python app.py
```

## Tests

```bash
python -m unittest -v
```

The unit tests mock `discord.py`, `yt-dlp`, and `python-dotenv`, so they run
without any dependencies installed. CI runs them on every push and PR, and
also verifies the Docker image builds.

## Operational notes

- **Keep `yt-dlp` fresh.** YouTube changes constantly; if clips stop playing
  with extraction errors, rebuild the image (or `pip install -U yt-dlp`) to
  pick up the latest release.
- The bot plays one clip at a time per server: triggering a new clip while one
  is playing interrupts it.
