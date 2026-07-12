# Skill Issue Bot

![CI](https://github.com/riles22/Skill-Issue-Bot/actions/workflows/ci.yml/badge.svg)

A tiny Discord soundboard bot. It joins your voice channel, plays a meme clip
streamed from YouTube, and disconnects when the clip ends (or when everyone
else leaves the channel).

## Commands

| Command  | What it does                        |
|----------|-------------------------------------|
| `!skill` | Plays the "skill" clip              |
| `!ded`   | Plays the "ded" clip                |
| `!clips` | Lists all available clips           |
| `!leave` | Disconnects the bot from voice      |

Clip commands share a short per-server cooldown (one play per 3 seconds), and
triggering a new clip while one is playing interrupts it.

### Adding a clip

Add one line to the `CLIPS` dict in `app.py`:

```python
CLIPS = {
    'skill': 'https://www.youtube.com/watch?v=LuE0QMHErQo',
    'ded': 'https://www.youtube.com/watch?v=-LTtripsg5U',
    'mynewclip': 'https://www.youtube.com/watch?v=...',
}
```

The command (`!mynewclip`) is registered automatically.

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
docker compose up -d --build
```

Or without compose:

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

## Tests and linting

```bash
python -m unittest -v
pip install ruff && ruff check .
```

The unit tests mock `discord.py`, `yt-dlp`, and `python-dotenv`, so they run
without any dependencies installed. CI runs the tests, `ruff`, and a Docker
image build on every push and PR, and Dependabot keeps dependencies fresh.

## Operational notes

- **Keep `yt-dlp` fresh.** YouTube changes constantly; if clips stop playing
  with extraction errors, rebuild the image (or `pip install -U yt-dlp`) to
  pick up the latest release. Dependabot opens weekly bump PRs for this.
- The bot shuts down cleanly on SIGTERM (`docker stop`, platform redeploys),
  disconnecting from voice before exiting.

## License

[MIT](LICENSE)
