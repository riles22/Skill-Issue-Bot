import asyncio
import logging
import os
import random
import signal
import time
from collections import defaultdict

import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Initialize the bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

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
    'extractor_args': {
        'youtube': {
            'player_client': ['default']
        }
    }
}

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


# Per-guild state: the lock serializes plays within a guild, and the generation
# counter lets the after-play callback tell "clip finished" apart from "clip
# was interrupted by a newer one".
_play_locks = defaultdict(asyncio.Lock)
_play_generation = defaultdict(int)


@bot.event
async def on_ready():
    log.info('%s has connected to Discord!', bot.user)
    _check_sound_files()


def _check_sound_files():
    missing = []
    for collection in SOUND_COLLECTIONS:
        for name in collection['sounds']:
            if not os.path.isfile(_sound_path(collection, name)):
                missing.append(f"{collection['prefix']}_{name}.dca")
    if missing:
        log.warning('Missing %d sound file(s) in %s: %s',
                    len(missing), SOUNDS_DIR, ', '.join(missing))


async def _ready_to_play(ctx):
    """Guards shared by all play commands. Returns False if we can't play."""
    if ctx.guild is None:
        await ctx.send("This command only works in a server.")
        return False
    if not ctx.author.voice:
        await ctx.send("You are not connected to a voice channel.")
        return False
    return True


async def _connect_to_caller(ctx):
    """Joins or moves to the caller's channel. Returns None if it failed."""
    channel = ctx.author.voice.channel
    try:
        if ctx.voice_client is None:
            return await channel.connect()
        vc = ctx.voice_client
        if vc.channel != channel:
            await vc.move_to(channel)
        return vc
    except Exception:
        # Exception text can leak URLs/paths/library internals; log it, but
        # keep the chat message stable.
        log.exception('Failed to connect to voice channel')
        await ctx.send("Couldn't connect to the voice channel. Check the bot logs for details.")
        return None


def _make_after_playing(vc, guild_id, generation):
    def after_playing(error):
        """Callback after audio finishes - disconnects the bot."""
        if error:
            log.error('Player error: %s', error)
        # Only disconnect if no newer clip has taken over the connection.
        if _play_generation[guild_id] == generation:
            asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)
    return after_playing


async def play_audio(ctx, url):
    """Joins voice and plays audio streamed from a YouTube URL."""
    if not await _ready_to_play(ctx):
        return

    guild_id = ctx.guild.id

    async with _play_locks[guild_id]:
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

        # Extract the stream URL in a worker thread to avoid blocking the loop
        try:
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                loop = asyncio.get_running_loop()
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            audio_url = info.get('url')
            title = info.get('title', 'Unknown Title')
        except Exception:
            log.exception('Error extracting audio')
            await ctx.send("Couldn't fetch audio for that clip. Check the bot logs for details.")
            if not vc.is_playing():
                await vc.disconnect()
            return

        if not audio_url:
            await ctx.send("Could not find a playable audio stream for that video.")
            if not vc.is_playing():
                await vc.disconnect()
            return

        # Check if still connected before playing
        if not vc.is_connected():
            await ctx.send("Bot is not connected to a voice channel.")
            return

        # Play the audio
        # FFmpegPCMAudio requires ffmpeg to be installed and in PATH
        try:
            source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTS)
            vc.play(source, after=_make_after_playing(vc, guild_id, generation))
        except Exception:
            log.exception('Error playing audio')
            await ctx.send("Couldn't start playback. Check the bot logs for details.")
            if not vc.is_playing():
                await vc.disconnect()
            return

        await ctx.send(f"Playing: {title}")


async def play_local(ctx, paths):
    """Joins voice and plays local .dca files back to back."""
    if not await _ready_to_play(ctx):
        return

    guild_id = ctx.guild.id

    async with _play_locks[guild_id]:
        _play_generation[guild_id] += 1
        generation = _play_generation[guild_id]

        vc = await _connect_to_caller(ctx)
        if vc is None:
            return

        if vc.is_playing():
            vc.stop()

        # Like the original airhornbot, successful plays are silent in chat
        try:
            vc.play(DCASource(*paths), after=_make_after_playing(vc, guild_id, generation))
        except Exception:
            log.exception('Error playing sound')
            await ctx.send("Couldn't play that sound. Check the bot logs for details.")
            if not vc.is_playing():
                await vc.disconnect()


async def play_horn(ctx, collection, sound_name=None):
    """Plays a sound from a collection: named if given, weighted-random if not."""
    if sound_name:
        sound_name = sound_name.lower()
        if sound_name not in collection['sounds']:
            options = ' '.join(f'`{name}`' for name in collection['sounds'])
            await ctx.send(f"Unknown sound. Try one of: {options}")
            return
        name = sound_name
    else:
        name = _pick_sound(collection)

    paths = [_sound_path(collection, name)]

    # KHALED chains into a random airhorn, exactly like the original
    chained = collection.get('chain_with')
    if chained:
        paths.append(_sound_path(chained, _pick_sound(chained)))

    await play_local(ctx, paths)


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
async def clips(ctx):
    """Lists the available clips and airhorn commands."""
    clip_names = ' '.join(f'`!{name}`' for name in sorted(CLIPS))
    horn_names = ' '.join(f"`!{c['commands'][0]}`" for c in SOUND_COLLECTIONS)
    await ctx.send(
        f"Clips: {clip_names}\n"
        f"Airhorns: {horn_names} — add a sound name to pick one, e.g. `!airhorn truck`."
    )


@bot.command(name='leave')
async def leave(ctx):
    """Disconnects the bot from the voice channel."""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected.")
    else:
        await ctx.send("I am not in a voice channel.")


@bot.event
async def on_command_error(ctx, error):
    """Reports command errors instead of dumping tracebacks to the console."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Slow down! Try again in {error.retry_after:.1f}s.")
        return
    log.error('Command %s failed', ctx.command, exc_info=error)
    await ctx.send("Something went wrong running that command. Check the bot logs for details.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Disconnects if the bot is left alone (only bots remain) in the channel."""
    if member.bot:
        return

    voice_client = member.guild.voice_client
    if voice_client and voice_client.channel:
        if all(m.bot for m in voice_client.channel.members):
            await voice_client.disconnect()


@bot.event
async def on_guild_remove(guild):
    """Drops per-guild playback state when the bot is removed from a guild.

    Removal is the one point where no play command can be mid-flight for the
    guild, so popping the lock here can't race an active !airhorn.
    """
    _play_locks.pop(guild.id, None)
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

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
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
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    asyncio.run(_main())
