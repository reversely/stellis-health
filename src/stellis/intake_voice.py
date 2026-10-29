"""Speech for the intake call: listening, transcribing and speaking.

The browser streams 16 kHz microphone audio over a WebSocket. Silero VAD finds
where each answer ends, Moonshine transcribes it, and the agent's reply is
spoken one clause or sentence at a time. Kitten CPU speech is the default;
operators can select Kokoro on the same CPU (bf_emma), or local or Nebius
Chatterbox. Recognition and activity detection remain local; Chatterbox
receives only the reply text.
Audio capture follows the deployment debug-data setting.

Each turn logs how long each stage took, without any content.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from stellis import call_audio_cache, call_tts, debug_data, intake, storage

_log = logging.getLogger(__name__)

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
# name, download URL, SHA-256 of the download, whether it is a tar.bz2 folder.
MODELS = (
    ("silero_vad.onnx", _RELEASES + "asr-models/silero_vad.onnx",
     "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6", False),
    ("sherpa-onnx-moonshine-base-en-int8",
     _RELEASES + "asr-models/sherpa-onnx-moonshine-base-en-int8.tar.bz2",
     "21870cecaa2e44e4e2bf63e02d1072bed183ccd10284871353bd9d24dad14e5e", True),
    ("kitten-nano-en-v0_2-fp16", _RELEASES + "tts-models/kitten-nano-en-v0_2-fp16.tar.bz2",
     "0345a8a2f4a710cb8f7912c9a731ded8b3e1e69b33a871efa95c2e64651518fe", True),
)
# Kokoro v1.0 on CPU, the voice of Stellis (STELLIS_TTS_BACKEND=kokoro); downloaded only when selected.
KOKORO = ("kokoro-multi-lang-v1_0", _RELEASES + "tts-models/kokoro-multi-lang-v1_0.tar.bz2",
          "60703707bc05ccd48d60e1eb723f38b07d6e474795a0b6a588d7220400e0f7a4", True)
# bf_emma's speaker number in that package; its voice data matches Kokoro v1.0's bf_emma exactly.
KOKORO_VOICE = 21
SAMPLE_RATE = 16000
VAD_WINDOW = 512
# Silence that ends an answer. Shorter splits answers at a breath.
END_SILENCE_S = 0.7
# Audio kept before detected speech: the detector starts late, and without
# it the first word is clipped ("Type 2 diabetes" heard as "2 diabetes").
PRE_ROLL_S = 0.3
HISTORY_S = 30
# Speech this long while the agent talks interrupts it.
BARGE_IN_S = 0.6
# Speech that overlapped the agent's audio this long without stopping it is
# not an answer: two barge-in checks found none of the person's words in it.
MAX_OVERLAP_S = 2 * BARGE_IN_S
# Speech that starts this soon after an answer ends continues that answer.
FOLLOW_ON_S = 1.5
# Kitten's voices: 0 to 7 alternate male and female.
VOICE = 3
DENVER_VOICE = 0
THREADS = 2
MAX_FRAME_BYTES = 64_000
RECOGNITION_TIMEOUT_S = 15
SYNTHESIS_TIMEOUT_S = 15
REPLY_PIECE_TIMEOUT_S = 20
REPLY_TIMEOUT_S = 90
MAX_QUEUED_ANSWERS = 2
# An answer that repeats this share of the agent's word pairs, in order, is its own echo.
ECHO_OVERLAP = 0.6
# Speech recognition invents words for noise and for the agent's voice from
# the speakers. Text faster than this, in words a second, is invented.
MAX_WORDS_PER_S = 6

_STATE = {"state": "idle", "error": None}
_STATE_LOCK = threading.Lock()
_ENGINE_LOCK = threading.Lock()
_engine: "Engine | None" = None
_calls = 0


def directory() -> Path:
    """Where the call's speech models live; the gateway shares one folder."""
    configured = os.environ.get("STELLIS_CALL_MODELS")
    return Path(configured) if configured else storage.base_dir() / "models" / "call"


def models() -> tuple:
    """The call models this host needs: Kokoro only when it is the selected voice."""
    return MODELS + ((KOKORO,) if call_tts.backend() == "kokoro" else ())


def missing() -> list[str]:
    return [name for name, _, _, _ in models() if not (directory() / name).exists()]


def status() -> dict[str, object]:
    with _STATE_LOCK:
        state = dict(_STATE)
    if not missing():
        state.update(state="ready", error=None)
    return state


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def install() -> None:
    """Downloads each missing model, checks it, and leaves nothing half-written."""
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        for name, url, digest, archive in models():
            if (folder / name).exists():
                continue
            with _STATE_LOCK:
                _STATE.update(state="downloading", error=None)
            with tempfile.TemporaryDirectory(dir=folder) as work:
                download = Path(work) / "download"
                with urllib.request.urlopen(url, timeout=60) as response, download.open("wb") as out:
                    shutil.copyfileobj(response, out, 1 << 20)
                if _digest(download) != digest:
                    raise RuntimeError(f"{name} did not match its published checksum")
                if archive:
                    with tarfile.open(download) as bundle:
                        bundle.extractall(work, filter="data")
                    shutil.move(Path(work) / name, folder / name)
                else:
                    shutil.move(download, folder / name)
        with _STATE_LOCK:
            _STATE.update(state="ready", error=None)
    except Exception as exc:  # Network or disk errors show in setup and retry on the next start.
        with _STATE_LOCK:
            _STATE.update(state="failed", error=str(exc) or "Download failed")


def ensure_in_background() -> None:
    if missing():
        threading.Thread(target=install, name="stellis-call-models", daemon=True).start()


class Engine:
    """The loaded speech models, shared by the calls in this process."""

    def __init__(self, folder: Path) -> None:
        import sherpa_onnx

        asr = folder / "sherpa-onnx-moonshine-base-en-int8"
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(asr / "preprocess.onnx"), encoder=str(asr / "encode.int8.onnx"),
            uncached_decoder=str(asr / "uncached_decode.int8.onnx"),
            cached_decoder=str(asr / "cached_decode.int8.onnx"), tokens=str(asr / "tokens.txt"),
            num_threads=THREADS)
        voice = folder / "kitten-nano-en-v0_2-fp16"
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kitten=sherpa_onnx.OfflineTtsKittenModelConfig(
                    model=str(voice / "model.fp16.onnx"), voices=str(voice / "voices.bin"),
                    tokens=str(voice / "tokens.txt"), data_dir=str(voice / "espeak-ng-data")),
                num_threads=THREADS)))
        self.kokoro = None
        if call_tts.backend() == "kokoro":
            voice = folder / KOKORO[0]
            # The British lexicon alone suits bf_emma; espeak-ng covers words it lacks.
            self.kokoro = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                        model=str(voice / "model.onnx"), voices=str(voice / "voices.bin"),
                        tokens=str(voice / "tokens.txt"), data_dir=str(voice / "espeak-ng-data"),
                        dict_dir=str(voice / "dict"), lexicon=str(voice / "lexicon-gb-en.txt")),
                    num_threads=THREADS)))
        self.vad_config = sherpa_onnx.VadModelConfig()
        self.vad_config.silero_vad.model = str(folder / "silero_vad.onnx")
        self.vad_config.silero_vad.min_silence_duration = END_SILENCE_S
        self.vad_config.silero_vad.min_speech_duration = 0.25
        self.vad_config.silero_vad.window_size = VAD_WINDOW
        self.vad_config.sample_rate = SAMPLE_RATE
        self.vad_config.num_threads = 1
        self._sherpa = sherpa_onnx
        self._lock = threading.Lock()

    def vad(self):
        return self._sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def transcribe(self, samples: np.ndarray) -> str:
        with self._lock:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            self.recognizer.decode_stream(stream)
            # Moonshine sometimes wraps an answer in stray quote marks.
            return " ".join(stream.result.text.split()).strip(" \"'")

    def speak(self, text: str, voice: int = VOICE) -> tuple[bytes, int]:
        """Returns 16-bit mono PCM and its sample rate."""
        if call_tts.backend() not in {"cpu", "kokoro"} and voice == VOICE:
            return call_tts.speak(text)
        engine, speaker = self.tts, voice
        if voice == VOICE and getattr(self, "kokoro", None) is not None:
            engine, speaker = self.kokoro, KOKORO_VOICE
        with self._lock:
            audio = engine.generate(text, sid=speaker, speed=1.0)
        pcm = np.clip(np.asarray(audio.samples, dtype=np.float32), -1, 1)
        return (pcm * 32767).astype("<i2").tobytes(), int(audio.sample_rate)


def acquire() -> Engine:
    """Loads the models for a call, or reuses the ones already loaded.

    Raises:
        RuntimeError: The models are not downloaded yet.
    """
    global _engine, _calls
    with _ENGINE_LOCK:
        if missing():
            raise RuntimeError("Voice is still downloading")
        if _engine is None:
            _engine = Engine(directory())
        _calls += 1
        return _engine


def release() -> None:
    """Frees the models when the last call in this process ends."""
    global _engine, _calls
    with _ENGINE_LOCK:
        _calls = max(0, _calls - 1)
        if _calls == 0:
            _engine = None


def is_echo(heard: str, spoken: str) -> bool:
    """Whether an answer is the agent's own voice picked up by the microphone.

    An echo repeats the agent's words in their order, so word pairs are compared: an answer that
    reuses the agent's words in its own sentence ("I walked 20 minutes this morning" after "Walk 20
    minutes, 0 of 4 this week") is the person speaking.
    """
    clean = lambda text: [w for w in (w.strip(".,?!'\"").lower() for w in text.split()) if w]
    words, said = clean(heard), clean(spoken)
    if len(words) < 2:
        return bool(words) and words[0] in said
    pairs, said_pairs = list(zip(words, words[1:])), set(zip(said, said[1:]))
    return sum(pair in said_pairs for pair in pairs) / len(pairs) >= ECHO_OVERLAP


class Call:
    """One call over a WebSocket.

    Args:
        send_json: Sends a JSON message to the browser.
        send_bytes: Sends audio to the browser.
        engine: The speech models, or None to run the call by text only.
        script: Module that keeps the conversation, with ``load``,
            ``opening``, ``add_person_turn``, ``extend_person_turn``,
            ``drop_agent_reply``, ``add_agent_turn``, ``reply`` and
            ``update_notes`` as ``intake`` defines them. ``reply`` may
            also yield dicts, which go to the browser as ``action``
            messages; ``update_notes`` may be None for a call without a
            notepad.
    """

    def __init__(self, send_json, send_bytes, engine: Engine | None, script=intake) -> None:
        self.send_json = send_json
        self.send_bytes = send_bytes
        self.engine = engine
        self.script = script
        self.vad = engine.vad() if engine else None
        self.pending = np.zeros(0, dtype=np.float32)
        # Recent audio, so each answer can start a little before detection.
        self.history = np.zeros(0, dtype=np.float32)
        self.history_start = 0
        self.speaker = True
        self.replying: asyncio.Task | None = None
        self.notes: asyncio.Task | None = None
        self.notes_again = False
        # The current turn's spoken text, to recognise its echo.
        self.speaking = ""
        self.speaking_turn = -1
        # The history sample where the person's current speech started, and
        # the sample count at which to check it for a barge-in next.
        self.speech_sample: int | None = None
        self.next_check = 0
        self.checking: asyncio.Task | None = None
        self.answer_ended: float | None = None
        self.speaking_until = 0.0
        # When the agent's audio last stopped, and whether the person stopped
        # it by talking over it.
        self.played_until = 0.0
        self.barged_in = False
        # Seconds of the current speech heard while the agent's audio played.
        self.overlap_s = 0.0
        self.discard_reply = False
        self.speech_started: float | None = None
        self.turn = 0
        self.timings: dict[int, dict[str, float]] = {}
        self.ended = False
        self.detecting = False
        self.retryable = False
        self.script_ended = False
        self.recognizing: asyncio.Task | None = None
        self.answers = asyncio.Queue(maxsize=MAX_QUEUED_ANSWERS)

    async def start(self) -> None:
        state = self.script.load()
        await self.send_json({"type": "ready", "voice": self.engine is not None,
                              "session_id":state.get("session_id"),"review_day":state.get("context",{}).get("today"),
                              "notes": state.get("notes", []),
                              "revision": state.get("notes_revision", 0)})
        for action in getattr(self.script, "opening_actions", list)():
            await self.send_json({"type": "action", **action})
        text = self.script.opening()
        self.replying = asyncio.create_task(self._say_all([text], record=False))

    async def audio(self, frame: bytes) -> None:
        """Feeds microphone audio; a finished answer starts the agent's reply."""
        if self.ended or self.vad is None or not frame or len(frame) > MAX_FRAME_BYTES or len(frame) % 2:
            return
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
        self.pending = np.concatenate([self.pending, samples])
        while self.vad is not None and len(self.pending) >= VAD_WINDOW:
            window, self.pending = self.pending[:VAD_WINDOW], self.pending[VAD_WINDOW:]
            self.vad.accept_waveform(window)
            self._remember(window)
            await self._check_barge_in()
            detected = self.vad.is_speech_detected()
            if detected != self.detecting:
                self.detecting = detected
                if not self._busy():
                    await self.send_json({'type':'state', 'state':'hearing' if detected else 'listening'})
            await self._drain_segments()

    async def _drain_segments(self, ended_at: float | None = None) -> None:
        while self.vad is not None and not self.vad.empty():
            segment = self._with_pre_roll(self.vad.front.start, len(self.vad.front.samples))
            self.vad.pop()
            self.speech_started = self.speech_sample = None
            finished = ended_at if ended_at is not None else time.perf_counter() - END_SILENCE_S
            overlap, self.overlap_s = self.overlap_s, 0.0
            await self._queue_answer(segment, finished, overlap)

    async def _listening_state(self) -> None:
        await self.send_json({'type':'state', 'state':'hearing' if self.detecting else 'listening'})

    async def _queue_answer(self, segment: np.ndarray, ended_at: float, overlap: float) -> None:
        if self.answers.full():
            await self.send_json({'type':'error', 'message':'Please wait for this answer to finish'})
            return
        self.answers.put_nowait((segment, ended_at, overlap))
        if self.recognizing is None or self.recognizing.done():
            self.recognizing = asyncio.create_task(self._recognize_answers())

    async def _recognize_answers(self) -> None:
        # Recognition runs outside the receive loop so End and typed answers stay usable.
        while not self.answers.empty() and self.engine is not None:
            segment, ended_at, overlap = self.answers.get_nowait()
            try:
                await self._answer_audio(segment, ended_at, overlap)
            except Exception:
                _log.exception('Call answer processing failed')
                await self._voice_failed()

    async def finish_answer(self) -> None:
        """Flushes speech when the person confirms that their answer is finished."""
        if self.vad is None or not self.detecting:
            return
        detector = self.vad
        detector.flush()
        await self._drain_segments(time.perf_counter())
        detector.reset()
        self.history = np.zeros(0, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.history_start = 0
        self.speech_started = self.speech_sample = None
        self.overlap_s = 0.0
        self.detecting = False
        await self.send_json({'type':'state', 'state':'transcribing'})

    async def _voice_failed(self) -> None:
        # A timed-out native worker can still hold the shared engine lock.
        # This call uses text for the remainder rather than queue more work behind it.
        self.engine = self.vad = None
        self.detecting = False
        while not self.answers.empty():
            self.answers.get_nowait()
        await self.send_json({'type':'mic', 'ok':False})
        await self.send_json({'type':'error', 'message':'Type your answer below to continue this call'})
        await self.send_json({'type':'state', 'state':'typing'})

    def _remember(self, window: np.ndarray) -> None:
        self.history = np.concatenate([self.history, window])
        excess = len(self.history) - HISTORY_S * SAMPLE_RATE
        if excess > 0:
            self.history = self.history[excess:]
            self.history_start += excess

    def _with_pre_roll(self, start: int, length: int) -> np.ndarray:
        """An answer's audio from PRE_ROLL_S before the detector's start."""
        begin = max(self.history_start, start - int(PRE_ROLL_S * SAMPLE_RATE))
        return self.history[begin - self.history_start:start + length - self.history_start].copy()

    async def _check_barge_in(self) -> None:
        """Stops the agent when the person talks over it.

        Speech over the agent is transcribed first: the agent's own voice
        from the speakers reaches the microphone too, and must not stop it.
        """
        if not self.vad.is_speech_detected():
            self.speech_started = self.speech_sample = None
            return
        now = time.perf_counter()
        if now < self.played_until:
            self.overlap_s += VAD_WINDOW / SAMPLE_RATE
        self.speech_started = self.speech_started or now
        heard_until = self.history_start + len(self.history)
        if self.speech_sample is None:
            self.speech_sample = heard_until
            self.next_check = heard_until + int(BARGE_IN_S * SAMPLE_RATE)
        if (self._busy() and heard_until >= self.next_check
                and (self.checking is None or self.checking.done())):
            self.next_check = heard_until + int(BARGE_IN_S * SAMPLE_RATE)
            segment = self._with_pre_roll(self.speech_sample, heard_until - self.speech_sample)
            self.checking = asyncio.create_task(self._confirm_barge_in(segment))

    async def _confirm_barge_in(self, segment: np.ndarray) -> None:
        try:
            text = await asyncio.wait_for(asyncio.to_thread(self.engine.transcribe, segment), RECOGNITION_TIMEOUT_S)
        except Exception:
            _log.warning('Barge-in recognition failed', exc_info=True)
            return
        if not self.ended and len(text.split()) >= 2 and not is_echo(text, self.speaking) and self._busy():
            self.barged_in = True
            await self._interrupt()

    def _busy(self) -> bool:
        """Whether the agent is preparing a reply or its audio is still playing."""
        writing = self.replying is not None and not self.replying.done()
        return writing or time.perf_counter() < self.speaking_until

    async def _interrupt(self) -> None:
        if not self._busy():
            return
        if self.replying is not None and not self.replying.done():
            self.replying.cancel()
            try:
                await self.replying
            except asyncio.CancelledError:
                pass
        self.speaking_until = 0.0
        self.played_until = min(self.played_until, time.perf_counter())
        await self.send_json({"type": "stop"})

    async def _answer_audio(self, segment: np.ndarray, ended_at: float, overlap: float = 0.0) -> None:
        seconds = len(segment) / SAMPLE_RATE
        started_at = ended_at - seconds
        # Finish the earlier interruption check before starting a replacement reply.
        # Its delayed result must not interrupt the reply to this same answer.
        if self.checking is not None and not self.checking.done():
            await self.checking
        if not self._busy():
            await self.send_json({'type':'state', 'state':'transcribing'})
        try:
            text = await asyncio.wait_for(asyncio.to_thread(self.engine.transcribe, segment), RECOGNITION_TIMEOUT_S)
        except Exception:
            _log.warning('Answer recognition failed', exc_info=True)
            await self._voice_failed()
            return
        stt_done = time.perf_counter()
        await asyncio.to_thread(debug_data.save_answer_audio, segment, SAMPLE_RATE, text)
        if not self._is_answer(text, seconds, overlap):
            if not self._busy() and overlap < MAX_OVERLAP_S and not is_echo(text, self.speaking):
                await self.send_json({'type':'error', 'message':'Please repeat your answer or type it below'})
            # A dropped answer always returns the call to listening; it never stays on transcribing.
            if not self._busy():
                await self._listening_state()
            return
        # An answer that starts before the agent speaks again, or soon after
        # the last one ends, is the rest of the last one: the agent drops the
        # reply it started and replies to the whole answer once.
        follow_on = (self.answer_ended is not None and self._busy()
                     and (started_at - self.answer_ended < FOLLOW_ON_S
                          or self.speaking_turn != self.turn))
        self.answer_ended = ended_at
        await self._answer(text, {"speech_end": ended_at, "stt_done": stt_done}, follow_on)

    def _is_answer(self, text: str, seconds: float, overlap: float) -> bool:
        """Whether recognised speech is the person's answer.

        Drops the agent's own echo, text too fast to be speech, and speech
        that overlapped the agent's audio for MAX_OVERLAP_S without stopping
        it: recognition turned noise or the agent's voice into those words.
        """
        words = len(text.split())
        if len(text) < 2 or is_echo(text, self.speaking) or words / max(seconds, 0.1) > MAX_WORDS_PER_S:
            return False
        return self.barged_in or overlap < MAX_OVERLAP_S

    async def text(self, text: str) -> None:
        """A typed answer, handled like a spoken one."""
        pending = [task for task in (self.recognizing, self.checking) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        while not self.answers.empty():
            self.answers.get_nowait()
        now = time.perf_counter()
        await self._answer(text, {"speech_end": now, "stt_done": now}, False, typed=True)

    async def _answer(self, text: str, timings: dict[str, float], follow_on: bool, typed: bool = False) -> None:
        """Starts the reply to an answer.

        A follow-on is the rest of the previous answer after a pause: the
        agent drops what it started to say and replies to the whole answer.
        """
        if self.ended:
            return
        self.discard_reply = follow_on
        await self._interrupt()
        turn = self.turn if follow_on else self.turn + 1
        try:
            if follow_on:
                self.script.drop_agent_reply()
                text = self.script.extend_person_turn(text)
                turn = self.turn
            else:
                text = self.script.add_person_turn(text)
                # Only speech can be misheard: a script that reads answers back skips typed ones.
                if typed and hasattr(self.script, "typed"):
                    self.script.typed = True
        except ValueError as exc:
            await self.send_json({"type": "error", "message": str(exc)})
            await self._listening_state()
            return
        self.turn = turn
        self.retryable = False
        self.timings[turn] = timings
        self.discard_reply = False
        self.barged_in = False
        await self.send_json({"type": "heard", "text": text, "turn": turn})
        self._refresh_notes()
        self.replying = asyncio.create_task(self._reply(turn))

    async def retry_reply(self) -> None:
        """Retries the reply while keeping the accepted answer exactly once."""
        if self.ended or not self.retryable:
            return
        self.retryable = False
        await self._interrupt()
        self.script.drop_agent_reply()
        now = time.perf_counter()
        self.timings[self.turn] = {'speech_end':now, 'stt_done':now}
        await self.send_json({'type':'retrying', 'turn':self.turn})
        self.replying = asyncio.create_task(self._reply(self.turn))

    async def _reply_pieces(self, timings):
        stream = self.script.reply(timings)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(anext(stream), REPLY_PIECE_TIMEOUT_S)
                except StopAsyncIteration:
                    break
        finally:
            await stream.aclose()

    async def _reply_error(self, message: str) -> None:
        self.retryable = True
        await self.send_json({'type':'error', 'message':message, 'retryable':True})

    async def _reply(self, turn: int) -> None:
        reply_started = time.perf_counter()
        await self.send_json({"type": "state", "state": "thinking"})
        # Each piece with the time its audio starts playing.
        spoken: list[tuple[str, float]] = []
        acted = cut = False
        try:
            async with asyncio.timeout(REPLY_TIMEOUT_S), aclosing(self._reply_pieces(self.timings[turn])) as replies:
                async for piece in replies:
                    if isinstance(piece, dict):
                        await self.send_json({"type": "action", "turn":turn, **piece})
                        acted = True
                        continue
                    if intake.END_MARK in piece:
                        self.ended = True
                        await self.send_json({'type':'ending'})
                    spoken.append((piece, await self._say(piece, turn)))
                    if self.ended:
                        break
            # A typed answer saved as written replies with actions alone.
            if not spoken and not acted:
                raise intake.IntakeError('No reply arrived')
        except asyncio.CancelledError:
            cut = True
            raise
        except TimeoutError:
            _log.warning('Call reply timed out after %.3f seconds', time.perf_counter() - reply_started)
            await self._reply_error('The reply timed out')
        except intake.IntakeError as exc:
            await self._reply_error(str(exc))
        except Exception:  # Any other failure ends the reply, not the call.
            _log.exception("Intake reply failed")
            await self._reply_error("The reply failed")
        finally:
            if not self.discard_reply:
                # An interrupted reply keeps only the pieces that started playing.
                now = time.perf_counter()
                self.script.add_agent_turn(" ".join(piece for piece, start in spoken if not cut or start <= now))
        if self.ended:
            await self._end_script()
            await self.send_json({"type": "ended"})
        else:
            await self._listening_state()

    async def _say_all(self, pieces: list[str], record: bool) -> None:
        for piece in pieces:
            await self._say(piece, 0)
        if record:
            self.script.add_agent_turn(" ".join(pieces))
        if getattr(self.script, "end_after_opening", False):
            self.ended = True
            await self.send_json({"type": "ending"})
            await self._end_script()
            await self.send_json({"type": "ended"})
            return
        await self._listening_state()

    async def _say(self, piece: str, turn: int) -> float:
        """Sends one piece of speech; returns when its audio starts playing."""
        text = piece.replace(intake.END_MARK, "").strip()
        start = max(time.perf_counter(), self.speaking_until)
        if not text:
            return start
        self.speaking = f"{self.speaking} {text}" if turn == self.speaking_turn else text
        self.speaking_turn = turn
        audio = rate = None
        if self.engine is not None and self.speaker:
            try:
                cache_key = getattr(self.script, 'speech_cache_key', lambda text: None)(text)
                audio, rate = await asyncio.wait_for(
                    asyncio.to_thread(call_audio_cache.speak, cache_key, text, self.engine.speak), SYNTHESIS_TIMEOUT_S)
            except Exception as exc:
                _log.warning('Call synthesis failed: %s', type(exc).__name__)
                self.speaker = False
                if isinstance(exc, TimeoutError) and call_tts.backend() == 'cpu':
                    await self._voice_failed()
                else:
                    await self.send_json({'type':'error', 'message':'Speech is unavailable for this call'})
        stages = self.timings.get(turn)
        if stages is not None and "first_audio" not in stages:
            stages["first_audio"] = time.perf_counter()
        await self.send_json({"type": "say", "text": text, "turn": turn, "rate": rate,
                              "audio": audio is not None})
        if audio is not None:
            await self.send_bytes(audio)
            # The browser plays pieces back to back; this is when the last ends.
            seconds = len(audio) / 2 / rate
            start = max(time.perf_counter(), self.speaking_until)
            self.speaking_until = self.played_until = start + seconds
        return start

    def playing(self, turn: int) -> None:
        """The browser started playing a turn's first audio; logs its stages."""
        stages = self.timings.get(turn)
        # A reply dropped for a follow-on answer reports before its replacement.
        if not stages or "first_audio" not in stages:
            return
        del self.timings[turn]
        stages["playing"] = time.perf_counter()
        start = stages["speech_end"]
        report = {key: round((value - start) * 1000) for key, value in stages.items() if key != "speech_end"}
        _log.info("intake turn latency %s", json.dumps(report))
        with (storage.data_dir() / "intake-latency.jsonl").open("a") as log:
            log.write(json.dumps(report) + "\n")

    def _refresh_notes(self) -> None:
        if self.script.update_notes is None:
            return
        if self.notes is not None and not self.notes.done():
            self.notes_again = True
            return
        self.notes = asyncio.create_task(self._update_notes())

    async def _update_notes(self) -> None:
        while True:
            self.notes_again = False
            try:
                state = await self.script.update_notes()
                await self.send_json({"type": "notes", "notes": state["notes"],
                                      "revision": state["notes_revision"]})
            except intake.IntakeError:
                pass
            if not self.notes_again:
                return

    async def message(self, data: dict) -> None:
        """Handles a JSON message from the browser."""
        kind = data.get("type")
        if kind == "retry_reply":
            await self.retry_reply()
        elif kind == "text":
            await self.text(str(data.get("text", "")))
        elif kind == "finish_answer":
            await self.finish_answer()
        elif kind == "speaker":
            self.speaker = bool(data.get("on"))
        elif kind == "playing" and isinstance(data.get("turn"), int):
            self.playing(data["turn"])
        elif kind == "played":
            # The browser finished the queued speech, which can be sooner
            # than estimated, for example after a stall.
            self.speaking_until = 0.0
            self.played_until = min(self.played_until, time.perf_counter())
            if self.detecting and not self._busy():
                await self._listening_state()
        elif kind == "interrupt":
            await self._interrupt()

    async def _end_script(self) -> None:
        if self.script_ended:
            return
        end = getattr(self.script, 'end', None)
        try:
            if end is not None:
                await asyncio.to_thread(end)
            self.script_ended = True
        except Exception:
            _log.exception('Call completion could not be saved')

    async def close(self) -> None:
        tasks = [t for t in (self.replying, self.notes, self.checking, self.recognizing) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._end_script()
