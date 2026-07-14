# Skill Issue Bot

![CI](https://github.com/riles22/Skill-Issue-Bot/actions/workflows/ci.yml/badge.svg)

A tiny Discord soundboard bot. It joins your voice channel, plays a meme clip,
and disconnects when the clip ends (or when everyone else leaves the channel).
It also carries a full recreation of the legendary **Airhorn Solutions** bot
with all of its original sounds.

## Commands

| Command  | What it does                        |
|----------|-------------------------------------|
| `!skill` | Plays the "skill" clip (YouTube)    |
| `!ded`   | Plays the "ded" clip (YouTube)      |
| `!clips` | Lists all available clips & horns   |
| `!leave` | Disconnects the bot from voice      |

### Airhorn Solutions

The classic [airhornbot](https://github.com/discord/airhornbot) sound
collections, with the original audio, weights, aliases, and behavior:

| Command | Aliases | Sounds |
|---------|---------|--------|
| `!airhorn` | — | default, reverb, spam, tripletap, fourtap, distant, echo, clownfull, clownshort, clownspam, highfartlong, highfartshort, midshort, truck |
| `!anotha` | `!anothaone` | one, one_classic, one_echo — always chased by a random airhorn |
| `!johncena` | `!cena` | airhorn, echo, full, jc, nameis, spam |
| `!ethan` | `!eb` `!ethanbradberry` `!h3h3` | areyou_classic, areyou_condensed, areyou_crazy, areyou_ethan, classic, echo, high, slowandlow, cuts, beat, sodiepop |
| `!stan` | `!stanislav` | herd, moo, x3 |
| `!birthday` | `!bday` | horn, horn3, sadhorn, weakhorn |
| `!wowthatscool` | `!wtc` | thatscool |

A bare command plays a weighted-random sound from its collection, just like
the original; add a sound name to pick one (e.g. `!airhorn truck`). Sounds are
the original pre-encoded Opus `.dca` files played natively — no ffmpeg or
re-encoding involved (see [sounds/README.md](sounds/README.md) for credits).

All play commands share a short per-server cooldown (one play per 3 seconds),
and triggering a new clip while one is playing interrupts it.

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
which ffmpeg installs pull in on most systems). ffmpeg is only used for the
YouTube clips — the airhorn sounds play without it.

```bash
pip install -r requirements.txt
python app.py
```

## Hosting (running it off your own machine, for free)

The bot is a small always-on worker — it needs no open ports, no domain, and
about 256–512 MB of RAM. It must stay connected 24/7, which rules out free
PaaS tiers that sleep on idle (Render, Koyeb, Replit). The genuinely free
paths are a free cloud VM or spare hardware at home.

On any host with Docker, running it is one command (the image is published
for both x86 and ARM on every push to `main`, but only after CI passes):

```bash
docker run -d --name skill-issue-bot --restart unless-stopped \
  -e DISCORD_TOKEN=your-token-here \
  ghcr.io/riles22/skill-issue-bot:latest
```

Or clone the repo and use compose:

```bash
git clone https://github.com/riles22/Skill-Issue-Bot.git && cd Skill-Issue-Bot
cp .env.example .env   # paste your token
docker compose up -d --build
```

`--restart unless-stopped` keeps it alive across crashes and reboots either way.

### Option A: Oracle Cloud Always Free (a real $0-forever VM)

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/).
   A card is required for identity verification, but Always Free resources
   never bill.
2. Create a Compute instance: shape **VM.Standard.E2.1.Micro** (x86, 1 GB —
   always available) or **Ampere A1** (ARM, more RAM but region capacity
   varies). Pick an Ubuntu image and add your SSH key.
3. SSH in, install Docker (`curl -fsSL https://get.docker.com | sh`), then
   run the `docker run` command above.

Google Cloud's **e2-micro** (in `us-west1`/`us-central1`/`us-east1`) is an
equivalent always-free alternative, with the same card-for-verification
caveat.

### Option B: spare hardware at home

A Raspberry Pi (3 or newer), an old laptop, or any box that can stay plugged
in works fine — the published image includes ARM builds, so the same
`docker run` command applies. Electricity cost for a Pi is a few dollars a
year.

### Notes

- **Run exactly one instance.** Two copies on the same token both answer
  every command — stop the local one once the hosted one is online.
- **Skip third-party "free Discord bot hosting" sites.** They require handing
  over your bot token and tend to be unreliable.
- Paid-but-easy managed options (Railway, Render/Fly worker services,
  ~$5/mo) also deploy this repo directly from GitHub if you ever change your
  mind.

## Tests and linting

```bash
python -m unittest -v
pip install -r requirements-dev.txt && ruff check .
```

The unit tests mock `discord.py`, `yt-dlp`, and `python-dotenv`, so they run
without any dependencies installed. CI runs the tests, `ruff`, and a Docker
image build on every push and PR, and Dependabot keeps dependencies fresh.
Pushes to `main` (and `v*` release tags) additionally publish the GHCR image
— but only after all of those checks pass. The multi-arch image is pushed
once under a `candidate-<sha>` tag, both platform digests are Trivy-scanned
*as pushed*, and only then are `:latest` (and friends) pointed at that exact
digest — so the scanned bytes are precisely the bytes users pull. Release
tags (`git tag v1.0.0 && git push --tags`) also publish semver image tags
(`:1.0.0`, `:1.0`) alongside `:latest`.

Runtime dependencies are locked in `constraints.txt`, so CI and the Docker
image install the exact same tested set. To refresh the lock after editing
`requirements.txt`:

```bash
uv pip compile requirements.txt --python-version 3.12 -o constraints.txt
```

### Manual smoke test

CI cannot exercise real Discord voice (the unit tests mock it), so before
merging changes that touch playback — and before tagging a release — run the
bot against a real server once and check:

1. `!skill` / `!ded` — YouTube extraction still works
2. `!airhorn` — DCA playback works
3. `!anotha` — chained playback (DJ Khaled followed by an airhorn)
4. Interrupting a playing clip with another command
5. Moving the bot between voice channels mid-clip
6. Leaving the bot alone with only other bots (it should disconnect)
7. `docker stop` during playback (clean voice disconnect, no zombie session)
8. The published multi-arch image on the intended host (`docker ps` should
   report the container healthy once the bot is connected)

## Operational notes

- **Keep `yt-dlp` fresh.** YouTube changes constantly; if clips stop playing
  with extraction errors, rebuild the image (or `pip install -U yt-dlp`) to
  pick up the latest release. Dependabot opens weekly bump PRs for this.
- The bot shuts down cleanly on SIGTERM (`docker stop`, platform redeploys),
  disconnecting from voice before exiting.
- **The container health check tracks the Discord connection, not the
  process.** While connected, the bot touches the file named by
  `HEALTH_FILE` every 30 seconds; the Docker `HEALTHCHECK` fails once that
  file goes stale. The heartbeat pauses whenever the gateway websocket
  drops, so an extended Discord outage (or a bot stuck reconnecting) also
  turns the container `unhealthy` — not just a bad token at startup. Check
  the token and the logs. Outside Docker, set `HEALTH_FILE` yourself or
  leave it unset to disable the heartbeat.
- **Docker does not restart unhealthy containers on its own.** `--restart
  unless-stopped` only reacts to the process exiting; a running-but-unhealthy
  container just sits there flagged in `docker ps`. If you want automatic
  recovery, uncomment the `autoheal` sidecar in `compose.yaml` (or use any
  orchestrator/watchdog that acts on Docker health status).
- Error replies in Discord are deliberately generic; the full exception and
  traceback are always in the container logs (`docker logs skill-issue-bot`).

## License

[MIT](LICENSE)
