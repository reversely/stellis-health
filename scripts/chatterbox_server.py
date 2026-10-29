"""Serve local Chatterbox with the Nebius cookbook's WAV speech contract."""

import hmac
import io
import os
import threading
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

_model = None
_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    global _model
    import torch
    from chatterbox.tts import ChatterboxTTS

    device = os.environ.get("CHATTERBOX_DEVICE", "cuda")
    if device not in {"cpu", "cuda"}:
        raise ValueError("CHATTERBOX_DEVICE must be cpu or cuda")
    _model = ChatterboxTTS.from_pretrained(device=device)
    # Load GPU kernels and audio processing before accepting a caller's reply.
    with torch.inference_mode():
        _model.generate("Hello. What would you like to focus on today?",
                        cfg_weight=0.5, exaggeration=0.5)
    yield
    _model = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def authorize(authorization):
    token = os.environ.get("CHATTERBOX_TOKEN", "")
    if token and not hmac.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(401, "Authentication required")


class Speech(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    model: str = "tts-1"
    voice: str = "default"
    response_format: str = "wav"
    speed: float = 1.0


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {"object": "list", "data": [{"id": "tts-1", "object": "model"}]}


def voice_files():
    configured=os.environ.get('CHATTERBOX_VOICES_DIR')
    if not configured:return {}
    root=Path(configured).expanduser().resolve()
    return {p.name:p.resolve() for p in root.glob('*.wav') if p.is_file() and p.resolve().parent==root}


@app.get('/v1/audio/voices')
def voices(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {'voices':['default',*sorted(voice_files())]}


@app.post("/v1/audio/speech")
def speech(request: Speech, authorization: str | None = Header(default=None)):
    import soundfile as sf
    import torch

    authorize(authorization)
    references=voice_files()
    if (request.model != "tts-1" or request.voice not in {'default',*references}
            or request.response_format != "wav" or request.speed != 1.0):
        raise HTTPException(400, "Use tts-1, a listed voice, WAV and speed 1")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "Speech server is busy")
    try:
        with torch.inference_mode():
            audio = _model.generate(request.input, cfg_weight=0.5, exaggeration=0.5,
                                    **({'audio_prompt_path':str(references[request.voice])} if request.voice!='default' else {}))
        output = io.BytesIO()
        sf.write(output, audio.detach().cpu().float().numpy().reshape(-1),
                 _model.sr, format="WAV", subtype="PCM_16")
        return Response(output.getvalue(), media_type="audio/wav")
    finally:
        _lock.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("CHATTERBOX_PORT", "8010")), access_log=False)
