"""Renders the spoken test fixtures with the call's own voice, so speech tests are reproducible.

Each phrase becomes a 16 kHz mono WAV under tests/fixtures/speech/, with silence before and after
so voice activity detection sees a whole answer. The voice is synthetic (Kitten TTS), which tests
the recognition path end to end; it does not measure accuracy on human speech.

    STELLIS_CALL_MODELS=~/stellis-data/call-models uv run python scripts/speech_fixtures.py
"""
import wave
from pathlib import Path

import numpy as np

from stellis import intake_voice

PHRASES = {
    "name": "My name is Alex.",
    "gender": "I'm a woman.",
    "conditions": "I've been tired since December, and my GP ordered blood tests.",
}
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "speech"
LEAD_S, TAIL_S = 0.6, 2.0


def resample(samples: np.ndarray, rate: int, target: int = intake_voice.SAMPLE_RATE) -> np.ndarray:
    if rate == target:
        return samples
    times = np.arange(0, len(samples) * target / rate) * rate / target
    return np.interp(times, np.arange(len(samples)), samples)


def main() -> None:
    engine = intake_voice.Engine(intake_voice.directory())
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in PHRASES.items():
        pcm, rate = engine.speak(text)
        voice = resample(np.frombuffer(pcm, dtype="<i2").astype(np.float32), rate)
        rate = intake_voice.SAMPLE_RATE
        audio = np.concatenate([np.zeros(int(LEAD_S * rate)), voice, np.zeros(int(TAIL_S * rate))])
        with wave.open(str(OUT / f"{name}.wav"), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(np.clip(audio, -32768, 32767).astype("<i2").tobytes())
        print(f"{name}.wav: {text}")


if __name__ == "__main__":
    main()
