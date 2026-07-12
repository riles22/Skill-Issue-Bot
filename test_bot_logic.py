import logging
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Keep expected error-path logging out of the test output
logging.disable(logging.CRITICAL)

# Mock discord, yt_dlp and dotenv before importing app so the tests run
# without any of the runtime dependencies installed.
sys.modules['discord'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['discord.ext.commands'] = MagicMock()
# Link them up just in case
sys.modules['discord.ext'].commands = sys.modules['discord.ext.commands']
sys.modules['yt_dlp'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

commands_mock = sys.modules['discord.ext.commands']

# Configure the mocked decorators to return the original function
mock_bot = MagicMock()
def side_effect_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator
mock_bot.command.side_effect = side_effect_decorator
mock_bot.event.side_effect = lambda func: func
commands_mock.Bot.return_value = mock_bot
commands_mock.cooldown.side_effect = side_effect_decorator

# Real exception types so app code can isinstance() against them
commands_mock.CommandNotFound = type('CommandNotFound', (Exception,), {})
commands_mock.CommandOnCooldown = type('CommandOnCooldown', (Exception,), {})

# Now we can import the app logic, with a fake token in place.
with patch('os.getenv', return_value='FAKE_TOKEN'):
    import app


def make_voice_ctx():
    """Build a ctx mock for a user in a guild voice channel, plus the voice client."""
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.guild.id = 1234
    ctx.voice_client = None

    vc = MagicMock()
    vc.is_playing.return_value = False
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()
    vc.move_to = AsyncMock()
    vc.channel = ctx.author.voice.channel
    ctx.author.voice.channel.connect = AsyncMock(return_value=vc)
    return ctx, vc


def configure_extraction(result=None, error=None):
    """Configure what yt-dlp's extract_info returns (or raises)."""
    ydl_instance = MagicMock()
    if error is not None:
        ydl_instance.extract_info.side_effect = error
    else:
        ydl_instance.extract_info.return_value = result
    app.yt_dlp.YoutubeDL.return_value.__enter__.return_value = ydl_instance
    return ydl_instance


class TestBotLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset per-guild state between tests
        app._play_locks.clear()
        app._play_generation.clear()

    async def test_play_audio_connects_and_plays(self):
        ctx, vc = make_voice_ctx()
        configure_extraction({'url': 'http://audio.url', 'title': 'Test Video'})

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.author.voice.channel.connect.assert_called_once()
        vc.play.assert_called_once()
        ctx.send.assert_called_with("Playing: Test Video")

    async def test_play_audio_no_voice(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.voice = None  # User not in voice

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.send.assert_called_with("You are not connected to a voice channel.")

    async def test_play_audio_rejects_dms(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = None  # Command invoked in a DM

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.send.assert_called_with("This command only works in a server.")

    async def test_play_audio_extraction_error_disconnects(self):
        ctx, vc = make_voice_ctx()
        configure_extraction(error=RuntimeError("boom"))

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.send.assert_called_with("Error extracting audio: boom")
        vc.play.assert_not_called()
        vc.disconnect.assert_awaited_once()

    async def test_interrupting_a_clip_does_not_disconnect(self):
        ctx, vc = make_voice_ctx()
        configure_extraction({'url': 'http://audio.url/1', 'title': 'Clip One'})
        await app.play_audio(ctx, "http://youtube.com/one")
        after_first = vc.play.call_args.kwargs['after']

        # A second command arrives while the first clip is still playing
        ctx.voice_client = vc
        vc.is_playing.return_value = True
        configure_extraction({'url': 'http://audio.url/2', 'title': 'Clip Two'})
        await app.play_audio(ctx, "http://youtube.com/two")
        after_second = vc.play.call_args.kwargs['after']

        with patch('asyncio.run_coroutine_threadsafe') as rct:
            # The interrupted clip's callback fires (vc.stop did this in real
            # life); it must NOT disconnect the bot from under the new clip.
            after_first(None)
            rct.assert_not_called()

            # The latest clip finishing naturally does disconnect.
            after_second(None)
            rct.assert_called_once()
            rct.call_args.args[0].close()  # tidy up the un-awaited coroutine

    async def test_clip_commands_play_their_url(self):
        cmd = app._make_clip_command('testclip', 'http://clip.url')
        ctx = MagicMock()

        with patch.object(app, 'play_audio', AsyncMock()) as play:
            await cmd(ctx)

        play.assert_awaited_once_with(ctx, 'http://clip.url')

    async def test_clips_command_lists_clips(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await app.clips(ctx)

        message = ctx.send.call_args.args[0]
        for name in app.CLIPS:
            self.assertIn(f'!{name}', message)

    async def test_leave_command(self):
        ctx = MagicMock()
        ctx.voice_client = AsyncMock()
        ctx.send = AsyncMock()

        await app.leave(ctx)

        ctx.voice_client.disconnect.assert_called_once()
        ctx.send.assert_called_with("Disconnected.")

    async def test_command_not_found_is_ignored(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await app.on_command_error(ctx, commands_mock.CommandNotFound())

        ctx.send.assert_not_called()

    async def test_cooldown_error_is_friendly(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        error = commands_mock.CommandOnCooldown()
        error.retry_after = 2.5

        await app.on_command_error(ctx, error)

        ctx.send.assert_called_with("Slow down! Try again in 2.5s.")

    async def test_generic_command_error_is_reported(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await app.on_command_error(ctx, RuntimeError("kaboom"))

        ctx.send.assert_called_with("Something went wrong: kaboom")

    async def test_disconnects_when_left_alone(self):
        member = MagicMock()
        member.bot = False
        vc = MagicMock()
        vc.disconnect = AsyncMock()
        bot_member = MagicMock()
        bot_member.bot = True
        vc.channel.members = [bot_member]
        member.guild.voice_client = vc

        await app.on_voice_state_update(member, MagicMock(), MagicMock())

        vc.disconnect.assert_awaited_once()

    async def test_stays_when_humans_remain(self):
        member = MagicMock()
        member.bot = False
        vc = MagicMock()
        vc.disconnect = AsyncMock()
        bot_member = MagicMock()
        bot_member.bot = True
        human = MagicMock()
        human.bot = False
        vc.channel.members = [bot_member, human]
        member.guild.voice_client = vc

        await app.on_voice_state_update(member, MagicMock(), MagicMock())

        vc.disconnect.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
