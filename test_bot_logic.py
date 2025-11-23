import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import sys
import os

# Mock discord and yt_dlp before importing app
sys.modules['discord'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['discord.ext.commands'] = MagicMock()
# Link them up just in case
sys.modules['discord.ext'].commands = sys.modules['discord.ext.commands']
sys.modules['yt_dlp'] = MagicMock()

# Configure the mock bot to return the original function when used as a decorator
mock_bot = MagicMock()
def side_effect_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator
mock_bot.command.side_effect = side_effect_decorator
mock_bot.event.side_effect = lambda func: func
sys.modules['discord.ext.commands'].Bot.return_value = mock_bot

# Now we can import the app logic. 
# Since app.py imports these, we need to make sure they are mocked first.
# We also need to mock dotenv and os.getenv to avoid error on import
with patch('dotenv.load_dotenv'), patch('os.getenv', return_value='FAKE_TOKEN'):
    import app

class TestBotLogic(unittest.IsolatedAsyncioTestCase):
    async def test_play_audio_connects_and_plays(self):
        # Setup mocks
        ctx = MagicMock()
        ctx.author.voice.channel = MagicMock()
        ctx.voice_client = None
        ctx.send = AsyncMock()
        
        # Mock the voice client connection
        vc_mock = MagicMock()
        vc_mock.is_playing.return_value = False
        vc_mock.play = MagicMock()
        ctx.author.voice.channel.connect = AsyncMock(return_value=vc_mock)

        # Mock yt_dlp context manager
        ydl_instance = MagicMock()
        ydl_instance.extract_info.return_value = {'url': 'http://audio.url', 'title': 'Test Video'}
        app.youtube_dl.YoutubeDL.return_value.__enter__.return_value = ydl_instance

        # Run the function
        # We need to mock asyncio.get_event_loop().run_in_executor because app.py uses it
        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value={'url': 'http://audio.url', 'title': 'Test Video'})
            
            await app.play_audio(ctx, "http://youtube.com/video")

        # Verify
        ctx.author.voice.channel.connect.assert_called_once()
        vc_mock.play.assert_called_once()
        ctx.send.assert_called_with("Playing: Test Video")

    async def test_play_audio_no_voice(self):
        ctx = MagicMock()
        ctx.author.voice = None # User not in voice
        ctx.send = AsyncMock()

        await app.play_audio(ctx, "http://youtube.com/video")
        
        ctx.send.assert_called_with("You are not connected to a voice channel.")

    async def test_leave_command(self):
        ctx = MagicMock()
        ctx.voice_client = AsyncMock()
        ctx.send = AsyncMock()

        await app.leave(ctx)

        ctx.voice_client.disconnect.assert_called_once()
        ctx.send.assert_called_with("Disconnected.")

if __name__ == '__main__':
    unittest.main()
