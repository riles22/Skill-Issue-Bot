import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("No DISCORD_TOKEN found in environment variables.")

# Initialize the bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# YouTube-DL options
YDL_OPTS = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'noplaylist': True,
    'quiet': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['default']
        }
    }
}

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

async def play_audio(ctx, url):
    """Helper function to join voice and play audio from a URL."""
    if not ctx.author.voice:
        await ctx.send("You are not connected to a voice channel.")
        return

    channel = ctx.author.voice.channel
    
    # Connect to voice channel if not already connected
    try:
        if ctx.voice_client is None:
            vc = await channel.connect()
        else:
            vc = ctx.voice_client
            if vc.channel != channel:
                await vc.move_to(channel)
    except Exception as e:
        await ctx.send(f"Failed to connect to voice channel: {e}")
        return

    # Stop any currently playing audio
    if vc.is_playing():
        vc.stop()

    # Extract audio URL in a separate thread to avoid blocking
    try:
        with youtube_dl.YoutubeDL(YDL_OPTS) as ydl:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            audio_url = info['url']
            title = info.get('title', 'Unknown Title')
    except Exception as e:
        await ctx.send(f"Error extracting audio: {e}")
        return

    # Check if still connected before playing
    if not vc.is_connected():
        await ctx.send("Bot is not connected to a voice channel.")
        return

    # Play the audio
    # FFmpegPCMAudio requires ffmpeg to be installed and in PATH
    try:
        source = discord.FFmpegPCMAudio(audio_url)
        
        def after_playing(error):
            """Callback after audio finishes - disconnects the bot."""
            if error:
                print(f'Player error: {error}')
            # Schedule the disconnect in the event loop
            asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)
        
        vc.play(source, after=after_playing)
        await ctx.send(f"Playing: {title}")
    except Exception as e:
        await ctx.send(f"Error playing audio: {e}")

@bot.command(name='skill')
async def skill(ctx):
    """Plays the 'skill' clip."""
    await play_audio(ctx, "https://www.youtube.com/watch?v=LuE0QMHErQo")

@bot.command(name='ded')
async def ded(ctx):
    """Plays the 'ded' clip."""
    await play_audio(ctx, "https://www.youtube.com/watch?v=-LTtripsg5U")

@bot.command(name='leave')
async def leave(ctx):
    """Disconnects the bot from the voice channel."""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected.")
    else:
        await ctx.send("I am not in a voice channel.")

@bot.event
async def on_voice_state_update(member, before, after):
    """Disconnects if the bot is left alone in the channel."""
    if member == bot.user:
        return
    
    voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
    if voice_client and voice_client.channel:
        # If the bot is alone in the channel (len == 1 means only the bot is there)
        if len(voice_client.channel.members) == 1:
            await voice_client.disconnect()

if __name__ == "__main__":
    bot.run(TOKEN)