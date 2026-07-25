import asyncio
import logging
import os
import random
import signal
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import discord
import yt_dlp
from discord.ext import commands
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Initialize the bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    # Nothing this bot says should ever ping anyone. Clip titles come from
    # YouTube, so without this a video named "@everyone" would notify a whole
    # server every time someone ran the command.
    allowed_mentions=discord.AllowedMentions.none(),
)

# YouTube soundboard clips: each entry becomes a bot command (e.g. !skill)
CLIPS = {
    'skill': 'https://www.youtube.com/watch?v=LuE0QMHErQo',
    'ded': 'https://www.youtube.com/watch?v=-LTtripsg5U',
}

# YouTube-DL options (streaming only, nothing is downloaded)
YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    # Bounds each network operation; EXTRACT_TIMEOUT caps the job as a whole
    'socket_timeout': 10,
    'retries': 2,
    'extractor_args': {
        'youtube': {
            'player_client': ['default']
        }
    }
}

# Outer deadline on a whole extraction: yt-dlp makes many network calls, so
# socket_timeout alone doesn't bound a long series of slow-but-successful ones
EXTRACT_TIMEOUT = 30  # seconds

# Joining/moving voice happens while holding a guild's playback lock, so it
# needs a tighter bound than discord.py's 30s default: a wedged connect would
# otherwise block every other play command in that server for half a minute.
VOICE_CONNECT_TIMEOUT = 15  # seconds

# Reconnect flags keep the stream alive if YouTube drops the connection mid-clip
FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ---------------------------------------------------------------------------
# Airhorn Solutions sound collections
#
# Recreates the classic airhornbot by Discord (github.com/discord/airhornbot,
# MIT licensed). Sound names, weights, command aliases, and the chain behavior
# are transcribed from the original Go bot; the .dca files in sounds/ are the
# original audio. Weights skew the random pick when no sound name is given.
# ---------------------------------------------------------------------------

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')

AIRHORN = {
    'prefix': 'airhorn',
    'commands': ['airhorn'],
    'sounds': {
        'default': 1000,
        'reverb': 800,
        'spam': 800,
        'tripletap': 800,
        'fourtap': 800,
        'distant': 500,
        'echo': 500,
        'clownfull': 250,
        'clownshort': 250,
        'clownspam': 250,
        'highfartlong': 200,
        'highfartshort': 200,
        'midshort': 100,
        'truck': 10,
    },
}

KHALED = {
    'prefix': 'another',
    'commands': ['anotha', 'anothaone'],
    'sounds': {'one': 1, 'one_classic': 1, 'one_echo': 1},
    # DJ Khaled is always followed by an airhorn, as the prophecy foretold
    'chain_with': AIRHORN,
}

CENA = {
    'prefix': 'jc',
    'commands': ['johncena', 'cena'],
    'sounds': {'airhorn': 1, 'echo': 1, 'full': 1, 'jc': 1, 'nameis': 1, 'spam': 1},
}

ETHAN = {
    'prefix': 'ethan',
    'commands': ['ethan', 'eb', 'ethanbradberry', 'h3h3'],
    'sounds': {
        'areyou_classic': 100,
        'areyou_condensed': 100,
        'areyou_crazy': 100,
        'areyou_ethan': 100,
        'classic': 100,
        'echo': 100,
        'high': 100,
        'slowandlow': 100,
        'cuts': 30,
        'beat': 30,
        'sodiepop': 1,
    },
}

COW = {
    'prefix': 'cow',
    'commands': ['stan', 'stanislav'],
    'sounds': {'herd': 10, 'moo': 10, 'x3': 1},
}

BIRTHDAY = {
    'prefix': 'birthday',
    'commands': ['birthday', 'bday'],
    'sounds': {'horn': 50, 'horn3': 30, 'sadhorn': 25, 'weakhorn': 25},
}

WOW = {
    'prefix': 'wow',
    'commands': ['wowthatscool', 'wtc'],
    'sounds': {'thatscool': 50},
}

SOUND_COLLECTIONS = [AIRHORN, KHALED, CENA, ETHAN, COW, BIRTHDAY, WOW]

# A single 20ms frame of Opus silence
OPUS_SILENCE_FRAME = b'\xf8\xff\xfe'


class DCASource(discord.AudioSource):
    """Streams pre-encoded Opus frames from .dca files (the airhornbot format).

    A .dca file is a sequence of Opus frames, each prefixed with an int16
    little-endian length. Because the audio is already Opus, no ffmpeg or
    re-encoding is involved. Multiple files play back to back with a short
    silence gap between them (the original bot's 250ms "part delay").
    """

    GAP_FRAMES = 12  # 12 x 20ms = ~250ms between chained sounds

    def __init__(self, *paths):
        self._paths = list(paths)
        self._file = None
        self._gap_remaining = 0

    def is_opus(self):
        return True

    def _open_next(self):
        if not self._paths:
            return False
        self._file = open(self._paths.pop(0), 'rb')
        magic = self._file.read(4)
        if magic == b'DCA1':
            # DCA v1 has a JSON metadata block after the magic; skip it
            meta_length = int.from_bytes(self._file.read(4), 'little', signed=True)
            self._file.read(max(meta_length, 0))
        else:
            self._file.seek(0)
        return True

    def _read_frame(self):
        header = self._file.read(2)
        if len(header) < 2:
            return b''
        size = int.from_bytes(header, 'little', signed=True)
        if size <= 0:
            return b''
        frame = self._file.read(size)
        return frame if len(frame) == size else b''

    def read(self):
        while True:
            if self._gap_remaining > 0:
                self._gap_remaining -= 1
                return OPUS_SILENCE_FRAME
            if self._file is None:
                if not self._open_next():
                    return b''
            frame = self._read_frame()
            if frame:
                return frame
            self._file.close()
            self._file = None
            if not self._paths:
                return b''
            self._gap_remaining = self.GAP_FRAMES

    def cleanup(self):
        if self._file is not None:
            self._file.close()
            self._file = None


def _sound_path(collection, name):
    return os.path.join(SOUNDS_DIR, f"{collection['prefix']}_{name}.dca")


def _pick_sound(collection):
    """Weighted-random sound pick, like the original bot."""
    names = list(collection['sounds'])
    weights = list(collection['sounds'].values())
    return random.choices(names, weights=weights)[0]


# Per-guild state: the lock serializes plays within a guild, the request
# counter orders plays by command arrival (so a slow extraction can't stomp a
# newer clip), and the generation counter lets the after-play callback tell
# "clip finished" apart from "clip was interrupted by a newer one".
_play_locks = defaultdict(asyncio.Lock)
_play_requests = defaultdict(int)
_play_generation = defaultdict(int)


@bot.event
async def on_ready():
    log.info('%s has connected to Discord!', bot.user)


def _check_sound_files():
    """Returns the names of configured sounds that have no file on disk."""
    missing = []
    for collection in SOUND_COLLECTIONS:
        for name in collection['sounds']:
            if not os.path.isfile(_sound_path(collection, name)):
                missing.append(f"{collection['prefix']}_{name}.dca")
    if missing:
        log.warning('Missing %d sound file(s) in %s: %s',
                    len(missing), SOUNDS_DIR, ', '.join(missing))
    return missing


async def _reply(ctx, message):
    """Sends a chat message, tolerating a channel the bot can't post in.

    Every reply here is advisory. If the bot is missing Send Messages (or
    Discord is having a bad day), that must not turn into an unhandled error
    on top of whatever we were already reporting.
    """
    try:
        await ctx.send(message)
    except discord.HTTPException:
        log.warning('Could not send a reply in guild %s: %s',
                    getattr(ctx.guild, 'id', None), message)


def _caller_channel(ctx):
    """The voice channel the command's author is in, or None."""
    voice = getattr(ctx.author, 'voice', None)
    return voice.channel if voice is not None else None


async def _ready_to_play(ctx):
    """Guards shared by all play commands. Returns False if we can't play."""
    if ctx.guild is None:
        await _reply(ctx, "This command only works in a server.")
        return False
    if _caller_channel(ctx) is None:
        await _reply(ctx, "You are not connected to a voice channel.")
        return False
    return True


async def _connect_to_caller(ctx):
    """Joins or moves to the caller's channel. Returns None if it failed."""
    # Re-read the caller's channel instead of trusting the earlier guard: this
    # runs under the guild's play lock, so the caller may have left voice while
    # an older clip was still playing.
    channel = _caller_channel(ctx)
    if channel is None:
        await _reply(ctx, "You are not connected to a voice channel.")
        return None
    try:
        if ctx.voice_client is None:
            # self_deaf because the bot only ever speaks — a deafened client
            # isn't sent anyone else's audio.
            return await channel.connect(
                timeout=VOICE_CONNECT_TIMEOUT, self_deaf=True)
        vc = ctx.voice_client
        if vc.channel != channel:
            await vc.move_to(channel, timeout=VOICE_CONNECT_TIMEOUT)
        return vc
    except Exception:
        # Exception text can leak URLs/paths/library internals; log it, but
        # keep the chat message stable.
        log.exception('Failed to connect to voice channel')
        await _reply(ctx, "Couldn't connect to the voice channel. Check the bot logs for details.")
        return None


def _make_after_playing(vc, guild_id, generation):
    def after_playing(error):
        """Callback after audio finishes - disconnects the bot."""
        if error:
            log.error('Player error: %s', error)
        # Only disconnect if no newer clip has taken over the connection.
        if _play_generation[guild_id] != generation:
            return
        coro = vc.disconnect()
        try:
            asyncio.run_coroutine_threadsafe(coro, bot.loop)
        except RuntimeError:
            # This runs on the audio thread, which can outlive the event loop
            # during shutdown. The loop closing already tore the voice
            # connection down, so there is nothing left to disconnect.
            coro.close()
            log.debug('Event loop closed before disconnecting guild %s', guild_id)
    return after_playing


# Extractions get their own small pool: a deadline-abandoned extraction keeps
# its thread busy until yt-dlp gives up, and those zombies must not starve the
# event loop's default executor (which discord.py and the rest of the app use).
_extract_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='yt-extract')


async def _extract_audio(url):
    """Resolves stream info for a YouTube URL in a worker thread.

    Raises asyncio.TimeoutError after EXTRACT_TIMEOUT. The worker thread
    can't be killed and runs to completion, but nothing waits on it.
    """
    def extract():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            return ydl.extract_info(url, download=False)

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_extract_pool, extract), EXTRACT_TIMEOUT)


async def play_audio(ctx, url):
    """Joins voice and plays audio streamed from a YouTube URL."""
    if not await _ready_to_play(ctx):
        return

    guild_id = ctx.guild.id

    # Claim a request slot before extracting so plays keep command-arrival
    # order even though extraction times vary wildly.
    _play_requests[guild_id] += 1
    request = _play_requests[guild_id]

    # Extraction runs before taking the guild's playback lock: it's the slow,
    # network-bound step, and a hung extraction must not wedge every later
    # play in the guild. It also runs before joining voice, so a failed
    # extraction never yanks the bot into (and back out of) a channel.
    try:
        info = await _extract_audio(url)
    except (TimeoutError, asyncio.TimeoutError):
        log.error('Timed out extracting audio from %s', url)
        await _reply(ctx, "Timed out fetching audio for that clip. Try again in a moment.")
        return
    except Exception:
        log.exception('Error extracting audio')
        await _reply(ctx, "Couldn't fetch audio for that clip. Check the bot logs for details.")
        return

    audio_url = info.get('url')
    # `or` rather than a dict default: yt-dlp can hand back an explicit None
    title = info.get('title') or 'Unknown Title'
    if not audio_url:
        await _reply(ctx, "Could not find a playable audio stream for that video.")
        return

    # The caller may have left voice while extraction ran
    if _caller_channel(ctx) is None:
        await _reply(ctx, "You are not connected to a voice channel.")
        return

    async with _play_locks[guild_id]:
        # A newer play command claimed the guild while this one was still
        # extracting; playing now would replace the newer clip with an older
        # one, so this request is stale and drops silently.
        if _play_requests[guild_id] != request:
            log.info('Dropping stale play request for guild %s', guild_id)
            return

        # Claim a new generation: if we interrupt a running clip below, its
        # after-play callback must not disconnect the bot underneath us.
        _play_generation[guild_id] += 1
        generation = _play_generation[guild_id]

        vc = await _connect_to_caller(ctx)
        if vc is None:
            return

        # Stop any currently playing audio
        if vc.is_playing():
            vc.stop()

        # Check if still connected before playing
        if not vc.is_connected():
            await _reply(ctx, "Bot is not connected to a voice channel.")
            return

        # Play the audio
        # FFmpegPCMAudio requires ffmpeg to be installed and in PATH
        try:
            source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTS)
            vc.play(source, after=_make_after_playing(vc, guild_id, generation))
        except Exception:
            log.exception('Error playing audio')
            await _reply(ctx, "Couldn't start playback. Check the bot logs for details.")
            if not vc.is_playing():
                await vc.disconnect()
            return

        await _reply(ctx, f"Playing: {title}")


async def play_local(ctx, paths):
    """Joins voice and plays local .dca files back to back."""
    if not await _ready_to_play(ctx):
        return

    guild_id = ctx.guild.id

    # Horns join the same ordering as YouTube clips: claiming a request slot
    # here lets a still-extracting older clip detect that it has gone stale.
    _play_requests[guild_id] += 1

    async with _play_locks[guild_id]:
        _play_generation[guild_id] += 1
        generation = _play_generation[guild_id]

        vc = await _connect_to_caller(ctx)
        if vc is None:
            return

        if vc.is_playing():
            vc.stop()

        if not vc.is_connected():
            await _reply(ctx, "Bot is not connected to a voice channel.")
            return

        # Like the original airhornbot, successful plays are silent in chat
        try:
            vc.play(DCASource(*paths), after=_make_after_playing(vc, guild_id, generation))
        except Exception:
            log.exception('Error playing sound')
            await _reply(ctx, "Couldn't play that sound. Check the bot logs for details.")
            if not vc.is_playing():
                await vc.disconnect()


async def play_horn(ctx, collection, sound_name=None):
    """Plays a sound from a collection: named if given, weighted-random if not."""
    if sound_name:
        sound_name = sound_name.lower()
        if sound_name not in collection['sounds']:
            options = ' '.join(f'`{name}`' for name in collection['sounds'])
            await _reply(ctx, f"Unknown sound. Try one of: {options}")
            return
        name = sound_name
    else:
        name = _pick_sound(collection)

    paths = [_sound_path(collection, name)]

    # KHALED chains into a random airhorn, exactly like the original
    chained = collection.get('chain_with')
    if chained:
        paths.append(_sound_path(chained, _pick_sound(chained)))

    # A sound file missing from the deployment would otherwise make the bot
    # join, play silence and leave. Skip what isn't there, and say so if that
    # leaves nothing to play.
    playable = [path for path in paths if os.path.isfile(path)]
    if len(playable) != len(paths):
        log.error('Missing sound file(s): %s', ', '.join(
            os.path.basename(path) for path in paths if path not in playable))
    if not playable:
        await _reply(ctx, "That sound is missing from this deployment. "
                          "Check the bot logs for details.")
        return

    await play_local(ctx, playable)


def _make_clip_command(name, url):
    """Registers a bot command that plays the given YouTube clip."""
    @bot.command(name=name, help=f"Plays the '{name}' clip.")
    @commands.cooldown(1, 3, commands.BucketType.guild)
    async def _cmd(ctx):
        await play_audio(ctx, url)
    return _cmd


for _name, _url in CLIPS.items():
    _make_clip_command(_name, _url)


def _make_horn_command(collection):
    """Registers a bot command (plus aliases) for an airhorn sound collection."""
    name, *aliases = collection['commands']

    @bot.command(name=name, aliases=aliases,
                 help=f"Plays a random '{name}' sound; pass a name to pick one.")
    @commands.cooldown(1, 3, commands.BucketType.guild)
    async def _cmd(ctx, sound=None):
        await play_horn(ctx, collection, sound)
    return _cmd


for _collection in SOUND_COLLECTIONS:
    _make_horn_command(_collection)


@bot.command(name='clips', aliases=['sounds'])
@commands.cooldown(1, 3, commands.BucketType.guild)
async def clips(ctx):
    """Lists the available clips and airhorn commands."""
    clip_names = ' '.join(f'`!{name}`' for name in sorted(CLIPS))
    horn_names = ' '.join(f"`!{c['commands'][0]}`" for c in SOUND_COLLECTIONS)
    await _reply(
        ctx,
        f"Clips: {clip_names}\n"
        f"Airhorns: {horn_names} — add a sound name to pick one, e.g. `!airhorn truck`.\n"
        f"`!help` lists every alias and `!leave` kicks me out of voice."
    )


@bot.command(name='leave')
# Same cooldown as the play commands: !leave is the one way to make the bot
# drop a voice connection on demand, so it needs the same anti-churn guard.
@commands.cooldown(1, 3, commands.BucketType.guild)
async def leave(ctx):
    """Disconnects the bot from the voice channel."""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await _reply(ctx, "Disconnected.")
    else:
        await _reply(ctx, "I am not in a voice channel.")


@bot.event
async def on_command_error(ctx, error):
    """Reports command errors instead of dumping tracebacks to the console."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await _reply(ctx, f"Slow down! Try again in {error.retry_after:.1f}s.")
        return
    log.error('Command %s failed', ctx.command, exc_info=error)
    await _reply(ctx, "Something went wrong running that command. Check the bot logs for details.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Disconnects if the bot is left alone (only bots remain) in the channel."""
    if member.bot:
        return

    voice_client = member.guild.voice_client
    if voice_client and voice_client.channel:
        members = voice_client.channel.members
        # The `members and` guard matters: this list is built from the member
        # cache, and an empty one would otherwise read as "everyone left" and
        # cut a clip short. The bot itself is always in there for real.
        if members and all(m.bot for m in members):
            await voice_client.disconnect()


@bot.event
async def on_guild_remove(guild):
    """Drops per-guild playback state when the bot is removed from a guild.

    Removal is the one point where no play command can be mid-flight for the
    guild, so popping the lock here can't race an active !airhorn.
    """
    _play_locks.pop(guild.id, None)
    _play_requests.pop(guild.id, None)
    _play_generation.pop(guild.id, None)


HEARTBEAT_INTERVAL = 30  # seconds


async def _heartbeat(path, interval=HEARTBEAT_INTERVAL):
    """Touches `path` while the bot is connected and ready.

    Lets a container health check verify the Discord connection is actually
    up — a stale file means "process alive but not connected".
    """
    while not bot.is_closed():
        if bot.is_ready():
            try:
                with open(path, 'w') as f:
                    f.write(f'{time.time()}\n')
            except OSError:
                log.warning('Could not write health file %s', path)
        await asyncio.sleep(interval)


async def _main():
    """Runs the bot, shutting down cleanly on SIGINT/SIGTERM."""
    load_dotenv()

    # Validated here rather than at import so importing the module (tests,
    # tooling) never requires a token.
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise SystemExit("No DISCORD_TOKEN found in environment variables.")

    # Warn about a partial deployment at startup rather than on first use, and
    # once rather than on every Discord reconnect.
    _check_sound_files()

    loop = asyncio.get_running_loop()
    background = set()

    def _request_shutdown():
        task = asyncio.create_task(bot.close())
        # asyncio only holds a weak reference to running tasks, so an
        # unreferenced shutdown task can be garbage-collected mid-flight.
        background.add(task)
        task.add_done_callback(background.discard)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Signal handlers aren't supported on Windows event loops
            pass

    heartbeat_task = None
    health_file = os.getenv('HEALTH_FILE')
    if health_file:
        heartbeat_task = asyncio.create_task(_heartbeat(health_file))

    try:
        async with bot:
            await bot.start(token)
    except discord.LoginFailure:
        # The default traceback here is pure noise for the one cause it ever
        # has: the token is wrong.
        raise SystemExit(
            "Discord rejected DISCORD_TOKEN. Check the token in your "
            "environment (or .env file) against the Developer Portal.") from None
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        # Drop queued extractions so shutdown isn't waiting on work nobody
        # will hear. One already running still has to finish, but yt-dlp's
        # own socket timeout and retry limit bound how long that takes.
        _extract_pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    asyncio.run(_main())
