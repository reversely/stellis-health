"""On-device English transcription with whisper.cpp through pywhispercpp.

The result has the same shape as `whisper-cli -ojf` JSON, so word timing and
loop repair read it unchanged: a list of passages with millisecond offsets,
text, and tokens carrying their own offsets and probability. Beam search with
five beams and best-of-five matches whisper-cli's defaults.
"""

from __future__ import annotations

import contextvars
import os
import threading
from pathlib import Path
from typing import Callable

import numpy as np

_MODELS: dict[str, object] = {}
_LOCK = threading.Lock()
# Set by the processing job: called with the fraction of the audio decoded so far.
PROGRESS: contextvars.ContextVar[Callable[[float], None] | None] = contextvars.ContextVar("asr_progress", default=None)


def _model(path: Path):
    from pywhispercpp.model import Model

    key = str(path)
    # pywhispercpp exits the process on a path it cannot load, so check first.
    if not Path(key).is_file():
        raise RuntimeError("Local speech model is unavailable")
    with _LOCK:
        if key not in _MODELS:
            # Strategy 1 is beam search in whisper.cpp's sampling enum.
            _MODELS[key] = Model(key, params_sampling_strategy=1, redirect_whispercpp_logs_to=None)
        return _MODELS[key]


def _text(value: object) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _params(vocabulary: list[str] | None, max_context: int | None, threads: int) -> dict[str, object]:
    params: dict[str, object] = {"language": "en", "n_threads": threads, "token_timestamps": True,
                                 "beam_search": {"beam_size": 5, "patience": -1.0}, "greedy": {"best_of": 5},
                                 "print_progress": False, "print_realtime": False}
    if vocabulary:
        params["initial_prompt"] = "Vocabulary: " + ", ".join(vocabulary)
    if max_context is not None:
        params["n_max_text_ctx"] = max_context
    return params


def transcribe(audio: np.ndarray, model_path: Path, vocabulary: list[str] | None = None,
               max_context: int | None = None, threads: int | None = None) -> list[dict[str, object]]:
    """Transcribe mono 16 kHz float audio into whisper-cli shaped passages."""
    import _pywhispercpp as native

    # Every core the host gives this process; a fixed count starves larger hosts and oversubscribes small ones.
    params = _params(vocabulary, max_context, threads or len(os.sched_getaffinity(0)))
    model = _model(model_path)
    report = PROGRESS.get()
    seconds = len(audio) / 16000

    def passage(segment) -> None:
        # whisper.cpp times passages in hundredths of a second.
        if report and seconds:
            report(min(1.0, segment.t1 / 100 / seconds))

    with _LOCK:
        model.transcribe(np.ascontiguousarray(audio, dtype=np.float32), new_segment_callback=passage, **params)
        context = model._ctx
        passages = []
        for index in range(native.whisper_full_n_segments(context)):
            tokens = []
            for position in range(native.whisper_full_n_tokens(context, index)):
                data = native.whisper_full_get_token_data(context, index, position)
                tokens.append({"text": _text(native.whisper_full_get_token_text(context, index, position)),
                               "offsets": {"from": int(data.t0) * 10, "to": int(data.t1) * 10}, "p": float(data.p)})
            passages.append({"offsets": {"from": int(native.whisper_full_get_segment_t0(context, index)) * 10,
                                         "to": int(native.whisper_full_get_segment_t1(context, index)) * 10},
                             "text": _text(native.whisper_full_get_segment_text(context, index)), "tokens": tokens})
    return passages
