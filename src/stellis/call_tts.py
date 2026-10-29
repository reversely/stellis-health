"""Which voice speaks call replies: Kitten or Kokoro on the application host, or a Chatterbox server."""

import io
import ipaddress
import os
from urllib.parse import urlsplit

import httpx

MAX_AUDIO_BYTES = 16 * 1024 * 1024


class VoiceUnavailableError(RuntimeError):
    """The configured voice was explicitly rejected, without exposing provider details."""



def backend() -> str:
    value = os.environ.get("STELLIS_TTS_BACKEND", "cpu")
    if value not in {"cpu", "kokoro", "local", "nebius"}:
        raise ValueError("STELLIS_TTS_BACKEND must be cpu, kokoro, local or nebius")
    return value


def _configuration():
    kind = backend()
    if kind in {"cpu", "kokoro"}:
        raise ValueError("CPU speech uses the call engine")
    prefix = "STELLIS_CHATTERBOX_" + kind.upper()
    url = os.environ.get(prefix + "_URL", "http://127.0.0.1:8010" if kind == "local" else "")
    parsed = urlsplit(url)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (kind == "local" and loopback and parsed.scheme == "http"))):
        raise ValueError("Chatterbox requires HTTPS or a local loopback HTTP URL")
    token = os.environ.get(prefix + "_TOKEN", "")
    if kind == "nebius" and not token:
        raise ValueError("Set STELLIS_CHATTERBOX_NEBIUS_TOKEN on the server")
    headers = {"Authorization": "Bearer " + token} if token else {}
    voice = os.environ.get("STELLIS_CHATTERBOX_VOICE", "Emily.wav" if kind == "nebius" else "default")
    if not voice or len(voice)>120 or any(c in voice for c in '/\\\x00') or voice in {'.','..'}:
        raise ValueError("Choose a voice filename listed by Chatterbox")
    return url.rstrip('/'), headers, voice


def status():
    """Configuration is not proof that the endpoint or the requested voice exists."""
    kind=backend()
    if kind=='cpu':return {'backend':kind,'provider':'Kitten on application host','voice':'Kitten','configured':True}
    if kind=='kokoro':return {'backend':kind,'provider':'Kokoro on application host','voice':'bf_emma','configured':True}
    try:
        _,_,voice=_configuration()
        return {'backend':kind,'provider':'Nebius Chatterbox' if kind=='nebius' else 'Local Chatterbox','voice':voice,'configured':True}
    except ValueError:
        return {'backend':kind,'provider':'Chatterbox','voice':None,'configured':False}


def check():
    info=status()
    if info['backend'] in {'cpu','kokoro'}:return {**info,'state':'local_unverified'}
    if not info['configured']:return {**info,'state':'not_configured'}
    url,headers,voice=_configuration()
    try:
        with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
            response=client.get(url+'/v1/audio/voices',headers=headers)
            if response.status_code in {404,405}:
                # Cookbook endpoints may support speech without a voice-list route.
                models=client.get(url+'/v1/models',headers=headers)
                if models.status_code!=200:return {**info,'state':'unavailable','http_status':models.status_code}
                payload=models.json()
                data=payload.get('data',[]) if isinstance(payload,dict) else None
                if not isinstance(data,list) or not any(isinstance(m,dict) and m.get('id')=='tts-1' for m in data):return {**info,'state':'invalid_response'}
                return {**info,'state':'voice_unverified','voice_listing_supported':False}
        if response.status_code!=200:return {**info,'state':'unavailable','http_status':response.status_code}
        payload=response.json()
        voices=payload.get('voices') if isinstance(payload,dict) else None
        if not isinstance(voices,list) or len(voices)>1000:return {**info,'state':'invalid_response'}
        return {**info,'state':'voice_available' if voice in voices else 'voice_missing'}
    except (httpx.HTTPError,ValueError):return {**info,'state':'unavailable'}


def speak(text: str) -> tuple[bytes, int]:
    """Request bounded WAV audio and convert it to the browser's mono PCM."""
    import numpy as np
    import soundfile as sf
    url,headers,voice=_configuration()
    body = {"model": "tts-1", "input": text,
            "voice": voice,
            "response_format": "wav"}
    # Redirects and environment proxies must not forward credentials to another host.
    with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False) as client:
        with client.stream("POST", url + "/v1/audio/speech",
                           headers=headers, json=body) as response:
            if response.status_code == 400:
                detail=bytearray()
                for chunk in response.iter_bytes():
                    detail.extend(chunk)
                    if len(detail)>4096:break
                if len(detail)<=4096:
                    import json
                    try:reason=json.loads(detail).get('detail','')
                    except (ValueError,AttributeError):reason=''
                    if isinstance(reason,str) and reason.lower().startswith('unknown voice '):
                        raise VoiceUnavailableError('The selected voice is not installed on Chatterbox (HTTP 400)')
            if response.status_code != 200:
                raise RuntimeError(f"Chatterbox speech failed (HTTP {response.status_code})")
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_AUDIO_BYTES:
                    raise RuntimeError("Chatterbox audio exceeds the response limit")
    with sf.SoundFile(io.BytesIO(payload)) as audio:
        if audio.format != "WAV" or audio.channels != 1 or not 8000 <= audio.samplerate <= 48000:
            raise RuntimeError("Chatterbox must return mono WAV at 8 to 48 kHz")
        if not 0 < audio.frames <= 120 * audio.samplerate:
            raise RuntimeError("Chatterbox audio must contain at most 120 seconds")
        samples = audio.read(dtype="float32")
        rate = audio.samplerate
    if not np.isfinite(samples).all():
        raise RuntimeError("Chatterbox returned invalid samples")
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(), rate
