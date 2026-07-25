import logging
import os
import sys
import tempfile
import threading
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

# DCASource subclasses discord.AudioSource; give it a real base class
sys.modules['discord'].AudioSource = object

discord_mock = sys.modules['discord']

# Real exception types so app code can catch them in `except` clauses
# (Forbidden subclasses HTTPException in discord.py, as it does here)
discord_mock.HTTPException = type('HTTPException', (Exception,), {})
discord_mock.Forbidden = type('Forbidden', (discord_mock.HTTPException,), {})
discord_mock.LoginFailure = type('LoginFailure', (Exception,), {})

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

# Importing app needs no token (DISCORD_TOKEN is only checked in _main),
# but it must happen after the sys.modules mocks above — hence the noqa.
import app  # noqa: E402


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


def dca_bytes(*frames):
    """Build DCA v0 file bytes: int16-LE length-prefixed opus frames."""
    out = b''
    for frame in frames:
        out += len(frame).to_bytes(2, 'little') + frame
    return out


class TestBotLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset per-guild state between tests
        app._play_locks.clear()
        app._play_requests.clear()
        app._play_generation.clear()
        # Reset the shared yt_dlp mock so a side_effect configured by one
        # test can't leak into another (test order must not matter)
        app.yt_dlp.YoutubeDL.reset_mock(return_value=True, side_effect=True)

    async def test_play_audio_connects_and_plays(self):
        ctx, vc = make_voice_ctx()
        configure_extraction({'url': 'http://audio.url', 'title': 'Test Video'})

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.author.voice.channel.connect.assert_called_once()
        vc.play.assert_called_once()
        ctx.send.assert_called_with("Playing: Test Video")

    def test_bot_never_pings_anyone(self):
        # Clip titles come from YouTube; a title like "@everyone" must not
        # notify a server just because the bot echoes it back.
        kwargs = commands_mock.Bot.call_args.kwargs
        self.assertIs(kwargs['allowed_mentions'],
                      discord_mock.AllowedMentions.none.return_value)

    async def test_reply_survives_missing_send_permission(self):
        ctx = MagicMock()
        ctx.send = AsyncMock(side_effect=discord_mock.Forbidden())

        await app._reply(ctx, "hello")  # must not raise

        ctx.send.assert_awaited_once()

    async def test_command_error_survives_send_failure(self):
        ctx = MagicMock()
        ctx.send = AsyncMock(side_effect=discord_mock.Forbidden())

        # Reporting an error must not itself blow up in a channel the bot
        # cannot post in
        await app.on_command_error(ctx, RuntimeError("kaboom"))

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

    async def test_play_audio_extraction_error_never_joins_voice(self):
        ctx, vc = make_voice_ctx()
        configure_extraction(error=RuntimeError("boom"))

        await app.play_audio(ctx, "http://youtube.com/video")

        message = ctx.send.call_args.args[0]
        self.assertIn("Couldn't fetch audio", message)
        self.assertNotIn("boom", message)  # exception details stay in the logs
        # Extraction runs before joining voice, so there is nothing to undo
        ctx.author.voice.channel.connect.assert_not_called()
        vc.play.assert_not_called()

    def make_hanging_extraction(self):
        """Extraction that blocks until the test ends (released via cleanup)."""
        release = threading.Event()
        self.addCleanup(release.set)  # frees the worker thread at test end
        ydl = configure_extraction()
        ydl.extract_info.side_effect = lambda *a, **k: release.wait(2)
        return ydl

    async def test_play_audio_extraction_timeout_sends_stable_message(self):
        ctx, vc = make_voice_ctx()
        self.make_hanging_extraction()

        with patch.object(app, 'EXTRACT_TIMEOUT', 0.05):
            await app.play_audio(ctx, "http://youtube.com/video")

        message = ctx.send.call_args.args[0]
        self.assertIn("Timed out", message)
        ctx.author.voice.channel.connect.assert_not_called()
        vc.play.assert_not_called()

    async def test_extraction_error_leaves_current_clip_playing(self):
        ctx, vc = make_voice_ctx()
        ctx.voice_client = vc
        vc.is_playing.return_value = True
        configure_extraction(error=RuntimeError("boom"))

        await app.play_audio(ctx, "http://youtube.com/video")

        # A failed play must not touch whatever is currently playing
        vc.stop.assert_not_called()
        vc.disconnect.assert_not_awaited()
        self.assertNotIn(ctx.guild.id, app._play_generation)

    async def test_extraction_timeout_leaves_current_clip_playing(self):
        ctx, vc = make_voice_ctx()
        ctx.voice_client = vc
        vc.is_playing.return_value = True
        self.make_hanging_extraction()

        with patch.object(app, 'EXTRACT_TIMEOUT', 0.05):
            await app.play_audio(ctx, "http://youtube.com/video")

        vc.stop.assert_not_called()
        vc.disconnect.assert_not_awaited()
        self.assertNotIn(ctx.guild.id, app._play_generation)

    async def test_stale_extraction_does_not_interrupt_newer_clip(self):
        ctx, vc = make_voice_ctx()

        async def slow_extract(url):
            # A newer play command claims the guild while this extraction runs
            app._play_requests[ctx.guild.id] += 1
            return {'url': 'http://audio.url', 'title': 'Old Clip'}

        with patch.object(app, '_extract_audio', slow_extract):
            await app.play_audio(ctx, "http://youtube.com/video")

        # The stale request drops without joining voice or playing
        ctx.author.voice.channel.connect.assert_not_called()
        vc.play.assert_not_called()

    async def test_extraction_runs_outside_the_play_lock(self):
        ctx, vc = make_voice_ctx()
        seen = {}

        async def fake_extract(url):
            seen['locked'] = app._play_locks[ctx.guild.id].locked()
            return {'url': 'http://audio.url', 'title': 'Test Video'}

        with patch.object(app, '_extract_audio', fake_extract):
            await app.play_audio(ctx, "http://youtube.com/video")

        # A hung extraction must not hold the guild's lock and wedge it
        self.assertFalse(seen['locked'])
        vc.play.assert_called_once()

    async def test_caller_leaving_during_extraction_aborts(self):
        ctx, vc = make_voice_ctx()

        async def fake_extract(url):
            ctx.author.voice = None  # caller left while extraction ran
            return {'url': 'http://audio.url', 'title': 'Test Video'}

        with patch.object(app, '_extract_audio', fake_extract):
            await app.play_audio(ctx, "http://youtube.com/video")

        ctx.send.assert_called_with("You are not connected to a voice channel.")
        vc.play.assert_not_called()

    def test_ydl_opts_bound_network_operations(self):
        # Real, sane bounds — not just key presence
        socket_timeout = app.YDL_OPTS['socket_timeout']
        self.assertIsInstance(socket_timeout, (int, float))
        self.assertGreater(socket_timeout, 0)
        self.assertLessEqual(socket_timeout, app.EXTRACT_TIMEOUT)
        retries = app.YDL_OPTS['retries']
        self.assertIsInstance(retries, int)
        self.assertGreaterEqual(retries, 0)
        self.assertLessEqual(retries, 5)

    async def test_play_audio_without_stream_url(self):
        ctx, vc = make_voice_ctx()
        configure_extraction({'title': 'No Audio Here'})

        await app.play_audio(ctx, "http://youtube.com/video")

        self.assertIn("playable audio stream", ctx.send.call_args.args[0])
        ctx.author.voice.channel.connect.assert_not_called()

    async def test_play_audio_handles_null_title(self):
        ctx, vc = make_voice_ctx()
        # yt-dlp can hand back an explicit None rather than omitting the key
        configure_extraction({'url': 'http://audio.url', 'title': None})

        await app.play_audio(ctx, "http://youtube.com/video")

        ctx.send.assert_called_with("Playing: Unknown Title")

    async def test_connect_joins_deafened_and_bounded(self):
        ctx, vc = make_voice_ctx()
        configure_extraction({'url': 'http://audio.url', 'title': 'Test Video'})

        await app.play_audio(ctx, "http://youtube.com/video")

        kwargs = ctx.author.voice.channel.connect.call_args.kwargs
        # The bot only ever speaks, so it joins deafened
        self.assertTrue(kwargs['self_deaf'])
        # Connecting happens under the guild's play lock, so it must be
        # bounded more tightly than discord.py's 30s default
        self.assertGreater(kwargs['timeout'], 0)
        self.assertLess(kwargs['timeout'], 30)

    async def test_moves_to_callers_channel_when_already_connected(self):
        ctx, vc = make_voice_ctx()
        ctx.voice_client = vc
        vc.channel = MagicMock()  # bot is sitting in a different channel
        configure_extraction({'url': 'http://audio.url', 'title': 'Test Video'})

        await app.play_audio(ctx, "http://youtube.com/video")

        vc.move_to.assert_awaited_once()
        self.assertIs(vc.move_to.call_args.args[0], ctx.author.voice.channel)
        ctx.author.voice.channel.connect.assert_not_called()
        vc.play.assert_called_once()

    async def test_caller_leaving_before_connect_is_reported(self):
        ctx, vc = make_voice_ctx()
        # The caller can leave while an older clip holds the guild's play lock
        ctx.author.voice = None

        result = await app._connect_to_caller(ctx)

        self.assertIsNone(result)
        ctx.send.assert_called_with("You are not connected to a voice channel.")

    async def test_connect_error_message_hides_details(self):
        ctx, vc = make_voice_ctx()
        ctx.author.voice.channel.connect.side_effect = RuntimeError("http://internal.url")

        await app.play_audio(ctx, "http://youtube.com/video")

        message = ctx.send.call_args.args[0]
        self.assertIn("Couldn't connect", message)
        self.assertNotIn("internal.url", message)
        vc.play.assert_not_called()

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

    def test_after_playing_survives_closed_event_loop(self):
        # The audio thread outlives the event loop during shutdown, so the
        # after-play callback can fire with nothing left to schedule onto.
        vc = MagicMock()
        vc.disconnect = AsyncMock()
        app._play_generation[4321] = 7
        after = app._make_after_playing(vc, 4321, 7)

        with patch('asyncio.run_coroutine_threadsafe',
                   side_effect=RuntimeError('event loop is closed')):
            after(None)  # must not raise out of the audio thread

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
        for collection in app.SOUND_COLLECTIONS:
            self.assertIn(f"!{collection['commands'][0]}", message)

    async def test_leave_command(self):
        ctx = MagicMock()
        ctx.voice_client = AsyncMock()
        ctx.send = AsyncMock()

        await app.leave(ctx)

        ctx.voice_client.disconnect.assert_called_once()
        ctx.send.assert_called_with("Disconnected.")

    async def test_leave_when_not_in_voice(self):
        ctx = MagicMock()
        ctx.voice_client = None
        ctx.send = AsyncMock()

        await app.leave(ctx)

        ctx.send.assert_called_with("I am not in a voice channel.")

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

        message = ctx.send.call_args.args[0]
        self.assertIn("Something went wrong", message)
        self.assertNotIn("kaboom", message)  # exception details stay in the logs

    async def test_guild_removal_drops_per_guild_state(self):
        # Touch the defaultdicts the way a play command would
        _ = app._play_locks[1234]
        app._play_requests[1234] += 1
        app._play_generation[1234] += 1
        guild = MagicMock()
        guild.id = 1234

        await app.on_guild_remove(guild)

        self.assertNotIn(1234, app._play_locks)
        self.assertNotIn(1234, app._play_requests)
        self.assertNotIn(1234, app._play_generation)

    async def test_main_exits_without_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DISCORD_TOKEN', None)
            with self.assertRaises(SystemExit):
                await app._main()

    async def test_heartbeat_touches_file_while_ready(self):
        app.bot.is_closed = MagicMock(side_effect=[False, True])
        app.bot.is_ready = MagicMock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'healthy')

            await app._heartbeat(path, interval=0)

            self.assertTrue(os.path.isfile(path))

    async def test_heartbeat_skips_file_when_not_ready(self):
        app.bot.is_closed = MagicMock(side_effect=[False, True])
        app.bot.is_ready = MagicMock(return_value=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'healthy')

            await app._heartbeat(path, interval=0)

            self.assertFalse(os.path.exists(path))

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

    async def test_stays_when_member_cache_is_empty(self):
        # An empty member list means "we don't know who's here", not
        # "everyone left" — treating it as the latter would cut clips short.
        member = MagicMock()
        member.bot = False
        vc = MagicMock()
        vc.disconnect = AsyncMock()
        vc.channel.members = []
        member.guild.voice_client = vc

        await app.on_voice_state_update(member, MagicMock(), MagicMock())

        vc.disconnect.assert_not_awaited()


class TestAirhorn(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._play_locks.clear()
        app._play_requests.clear()
        app._play_generation.clear()
        app.yt_dlp.YoutubeDL.reset_mock(return_value=True, side_effect=True)

    def test_all_collection_sounds_have_files(self):
        for collection in app.SOUND_COLLECTIONS:
            for name in collection['sounds']:
                path = app._sound_path(collection, name)
                self.assertTrue(os.path.isfile(path), f"missing sound file: {path}")

    def test_all_sound_files_parse_as_dca(self):
        for collection in app.SOUND_COLLECTIONS:
            for name in collection['sounds']:
                source = app.DCASource(app._sound_path(collection, name))
                frames = 0
                while True:
                    frame = source.read()
                    if not frame:
                        break
                    frames += 1
                    self.assertLess(len(frame), 4000, f"bogus frame in {name}")
                source.cleanup()
                # Every sound should hold at least ~200ms of audio
                self.assertGreater(frames, 10, f"too few frames in {name}")

    def test_check_sound_files_reports_missing(self):
        # Nothing missing against the real sounds directory
        self.assertEqual(app._check_sound_files(), [])

        configured = sum(len(c['sounds']) for c in app.SOUND_COLLECTIONS)
        with tempfile.TemporaryDirectory() as empty:
            with patch.object(app, 'SOUNDS_DIR', empty):
                self.assertEqual(len(app._check_sound_files()), configured)

    def test_pick_sound_uses_collection_weights(self):
        with patch.object(app.random, 'choices', return_value=['truck']) as choices:
            self.assertEqual(app._pick_sound(app.AIRHORN), 'truck')

        names = choices.call_args.args[0]
        weights = choices.call_args.kwargs['weights']
        self.assertEqual(names, list(app.AIRHORN['sounds']))
        self.assertEqual(weights, list(app.AIRHORN['sounds'].values()))
        # Names and weights must stay aligned, or the odds silently shift
        self.assertEqual(len(names), len(weights))

    def test_pick_sound_only_returns_configured_names(self):
        for collection in app.SOUND_COLLECTIONS:
            for _ in range(50):
                self.assertIn(app._pick_sound(collection), collection['sounds'])

    def test_dca_source_stops_on_truncated_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'truncated.dca')
            with open(path, 'wb') as f:
                # A good frame, then a header claiming more bytes than remain
                f.write(dca_bytes(b'aa') + (5).to_bytes(2, 'little') + b'xx')

            source = app.DCASource(path)
            self.assertEqual(source.read(), b'aa')
            self.assertEqual(source.read(), b'')  # stops, does not hand back junk
            source.cleanup()

    def test_dca_source_reads_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.dca')
            with open(path, 'wb') as f:
                f.write(dca_bytes(b'abc', b'defgh'))

            source = app.DCASource(path)
            self.assertTrue(source.is_opus())
            self.assertEqual(source.read(), b'abc')
            self.assertEqual(source.read(), b'defgh')
            self.assertEqual(source.read(), b'')
            source.cleanup()

    def test_dca_source_skips_dca1_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.dca')
            metadata = b'{"dca":1}'
            with open(path, 'wb') as f:
                f.write(b'DCA1' + len(metadata).to_bytes(4, 'little') + metadata)
                f.write(dca_bytes(b'opus'))

            source = app.DCASource(path)
            self.assertEqual(source.read(), b'opus')
            self.assertEqual(source.read(), b'')
            source.cleanup()

    def test_dca_source_inserts_gap_between_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, 'a.dca')
            second = os.path.join(tmp, 'b.dca')
            with open(first, 'wb') as f:
                f.write(dca_bytes(b'aa'))
            with open(second, 'wb') as f:
                f.write(dca_bytes(b'bb'))

            source = app.DCASource(first, second)
            self.assertEqual(source.read(), b'aa')
            silence = 0
            while (frame := source.read()) == app.OPUS_SILENCE_FRAME:
                silence += 1
            self.assertEqual(silence, app.DCASource.GAP_FRAMES)
            self.assertEqual(frame, b'bb')
            self.assertEqual(source.read(), b'')
            source.cleanup()

    async def test_horn_plays_named_sound(self):
        ctx = MagicMock()
        with patch.object(app, 'play_local', AsyncMock()) as play:
            await app.play_horn(ctx, app.AIRHORN, 'truck')

        play.assert_awaited_once()
        paths = play.call_args.args[1]
        self.assertEqual(paths, [app._sound_path(app.AIRHORN, 'truck')])

    async def test_horn_rejects_unknown_sound(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(app, 'play_local', AsyncMock()) as play:
            await app.play_horn(ctx, app.AIRHORN, 'nope')

        play.assert_not_awaited()
        self.assertIn('Unknown sound', ctx.send.call_args.args[0])

    async def test_khaled_chains_into_airhorn(self):
        ctx = MagicMock()
        with patch.object(app, 'play_local', AsyncMock()) as play:
            await app.play_horn(ctx, app.KHALED)

        paths = play.call_args.args[1]
        self.assertEqual(len(paths), 2)
        self.assertTrue(os.path.basename(paths[0]).startswith('another_'))
        self.assertTrue(os.path.basename(paths[1]).startswith('airhorn_'))

    async def test_horn_reports_a_missing_sound_file(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with tempfile.TemporaryDirectory() as empty:
            with patch.object(app, 'SOUNDS_DIR', empty), \
                 patch.object(app, 'play_local', AsyncMock()) as play:
                await app.play_horn(ctx, app.AIRHORN, 'truck')

        # A partial deployment says so instead of joining to play silence
        play.assert_not_awaited()
        self.assertIn('missing', ctx.send.call_args.args[0])

    async def test_horn_plays_on_despite_a_missing_chained_sound(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with tempfile.TemporaryDirectory() as tmp:
            # Only the DJ Khaled sounds exist; the chased airhorn does not
            for name in app.KHALED['sounds']:
                open(os.path.join(tmp, f'another_{name}.dca'), 'wb').close()

            with patch.object(app, 'SOUNDS_DIR', tmp), \
                 patch.object(app, 'play_local', AsyncMock()) as play:
                await app.play_horn(ctx, app.KHALED)

        paths = play.call_args.args[1]
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.basename(paths[0]).startswith('another_'))
        ctx.send.assert_not_called()

    async def test_play_local_reports_a_lost_connection(self):
        ctx, vc = make_voice_ctx()
        vc.is_connected.return_value = False  # dropped between joining and playing

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'horn.dca')
            with open(path, 'wb') as f:
                f.write(dca_bytes(b'toot'))

            await app.play_local(ctx, [path])

        vc.play.assert_not_called()
        self.assertIn('not connected', ctx.send.call_args.args[0])

    async def test_play_local_plays_silently(self):
        ctx, vc = make_voice_ctx()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'horn.dca')
            with open(path, 'wb') as f:
                f.write(dca_bytes(b'toot'))

            await app.play_local(ctx, [path])

        vc.play.assert_called_once()
        self.assertIsInstance(vc.play.call_args.args[0], app.DCASource)
        ctx.send.assert_not_called()  # successful horn plays are silent

    async def test_play_local_supersedes_pending_extractions(self):
        ctx, vc = make_voice_ctx()
        before = app._play_requests[ctx.guild.id]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'horn.dca')
            with open(path, 'wb') as f:
                f.write(dca_bytes(b'toot'))

            await app.play_local(ctx, [path])

        # Horns claim a request slot so an in-flight extraction goes stale
        self.assertEqual(app._play_requests[ctx.guild.id], before + 1)


if __name__ == '__main__':
    unittest.main()
