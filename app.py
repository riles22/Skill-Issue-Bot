import asyncio
import logging
import os
import signal
from collections import defaultdict

import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("No DISCORD_TOKEN found in environment variables.")

log = logging.getLogger(__name__)

# Initialize the bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Soundboard clips: each entry becomes a bot command (e.g. !skill)
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

# Per-guild state: the lock serializes plays within a guild, and the generation
# counter lets the after-play callback tell "clip finished" apart from "clip
# was interrupted by a newer one".
_play_locks = defaultdict(asyncio.Lock)
_play_generation = defaultdict(int)


@bot.event
async def on_ready():
    log.info('%s has connected to Discord!', bot.user)


async def play_audio(ctx, url):
    """Helper function to join voice and play audio from a URL."""
    if ctx.guild is None:
        await ctx.send("This command only works in a server.")
        return

    if not ctx.author.voice:
        await ctx.send("You are not connected to a voice channel.")
        return

    channel = ctx.author.voice.channel
    guild_id = ctx.guild.id

    async with _play_locks[guild_id]:
        # Claim a new generation: if we interrupt a running clip below, its
        # after-play callback must not disconnect the bot underneath us.
        _play_generation[guild_id] += 1
        generation = _play_generation[guild_id]

        # Connect to voice channel if not already connected
        try:
            if ctx.voice_client is None:
                vc = await channel.connect()
            else:
                vc = ctx.voice_client
                if vc.channel != channel:
                    await vc.move_to(channel)
        except Exception as e:
            log.exception('Failed to connect to voice channel')
            await ctx.send(f"Failed to connect to voice channel: {e}")
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
        except Exception as e:
            log.exception('Error extracting audio')
            await ctx.send(f"Error extracting audio: {e}")
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

        def after_playing(error):
            """Callback after audio finishes - disconnects the bot."""
            if error:
                log.error('Player error: %s', error)
            # Only disconnect if no newer clip has taken over the connection.
            if _play_generation[guild_id] == generation:
                asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)

        # Play the audio
        # FFmpegPCMAudio requires ffmpeg to be installed and in PATH
        try:
            source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTS)
            vc.play(source, after=after_playing)
        except Exception as e:
            log.exception('Error playing audio')
            await ctx.send(f"Error playing audio: {e}")
            if not vc.is_playing():
                await vc.disconnect()
            return

        await ctx.send(f"Playing: {title}")


def _make_clip_command(name, url):
    """Registers a bot command that plays the given clip."""
    @bot.command(name=name, help=f"Plays the '{name}' clip.")
    @commands.cooldown(1, 3, commands.BucketType.guild)
    async def _cmd(ctx):
        await play_audio(ctx, url)
    return _cmd


for _name, _url in CLIPS.items():
    _make_clip_command(_name, _url)


@bot.command(name='clips')
async def clips(ctx):
    """Lists the available clips."""
    names = ' '.join(f'`!{name}`' for name in sorted(CLIPS))
    await ctx.send(f"Available clips: {names}")


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
    log.error('Command %s failed: %s', ctx.command, error)
    await ctx.send(f"Something went wrong: {error}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Disconnects if the bot is left alone (only bots remain) in the channel."""
    if member.bot:
        return

    voice_client = member.guild.voice_client
    if voice_client and voice_client.channel:
        if all(m.bot for m in voice_client.channel.members):
            await voice_client.disconnect()


async def _main():
    """Runs the bot, shutting down cleanly on SIGINT/SIGTERM."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
        except NotImplementedError:
            # Signal handlers aren't supported on Windows event loops
            pass

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    asyncio.run(_main())
