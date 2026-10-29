"""Account-local audio cache for explicitly registered public tour narration."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import wave

from stellis import call_tts, storage

_log = logging.getLogger(__name__)
MAX_BYTES = call_tts.MAX_AUDIO_BYTES


def _path(key: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('Invalid narration cache key')
    kind = call_tts.backend()
    prefix = 'STELLIS_CHATTERBOX_' + kind.upper()
    identity = [key, kind, os.environ.get(prefix + '_URL', ''),
                os.environ.get('STELLIS_CHATTERBOX_VOICE', 'default'),
                os.environ.get('STELLIS_TTS_CACHE_REVISION', '1'),
                'kitten-nano-en-v0_2-fp16:3']
    name = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return storage.data_dir() / 'tour-audio' / (name + '.wav')


def speak(key: str | None, text: str, synthesize) -> tuple[bytes, int]:
    """Reuses validated mono audio or synthesizes and atomically caches a public line."""
    if key is None:
        return synthesize(text)
    if key != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError('Narration cache key does not match its text')
    path = _path(key)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Cached audio exceeds the limit')
        with wave.open(str(path), 'rb') as audio:
            rate = audio.getframerate()
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or not 8000 <= rate <= 48000 or not 0 < audio.getnframes() <= rate * 120):
                raise ValueError('Invalid cached audio')
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != audio.getnframes() * 2:
                raise ValueError('Incomplete cached audio')
            return pcm, rate
    except (OSError, ValueError, EOFError, wave.Error):
        pass
    pcm, rate = synthesize(text)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.wav', delete=False) as handle:
            temporary = Path(handle.name)
            with wave.open(handle, 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(rate)
                audio.writeframes(pcm)
        os.replace(temporary, path)
    except OSError:
        _log.warning('Tour audio cache write failed', exc_info=True)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _log.warning('Tour audio cache cleanup failed', exc_info=True)
    return pcm, rate
