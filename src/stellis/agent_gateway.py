"""Cloud NemoClaw/OpenClaw gateway and the separate direct inference adapter."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import nullcontext
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx

from stellis import runtime_trace, runtime_profile, token_quota
from stellis import privacy


class GatewayError(Exception):
    """A safe runtime error without credentials or remote response bodies."""


def _settings() -> tuple[str, str]:
    # A gateway credential grants operator access. Bind it to one Stellis account.
    account = os.environ.get("STELLIS_ACCOUNT_ID", "")
    owner = os.environ.get("STELLIS_AGENT_GATEWAY_ACCOUNT_ID", "")
    endpoint = os.environ.get("STELLIS_AGENT_GATEWAY_URL", "").strip().rstrip("/")
    token = os.environ.get("STELLIS_AGENT_GATEWAY_TOKEN", "").strip()
    parsed = urlsplit(endpoint)
    if (not account or not owner or account != owner or not token or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise GatewayError("Connect the cloud agent service first")
    return endpoint, token


def configured() -> bool:
    from stellis import storage
    if runtime_profile.direct() or storage.in_demo():
        from stellis import ai
        return bool(ai._key())
    try:
        _settings()
        return True
    except (GatewayError, ValueError):
        return False


def _reply(payload: object) -> str:
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise GatewayError("Agent reply failed. Try again.")
    output = payload.get("output")
    if not isinstance(output, list):
        raise GatewayError("Agent reply failed. Try again.")
    texts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
            continue
        for part in item["content"]:
            if (isinstance(part, dict) and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)):
                texts.append(part["text"])
    answer = "\n".join(texts).strip()
    if not answer:
        raise GatewayError("Agent reply failed. Try again.")
    model = payload.get("model")
    if isinstance(model, str) and model.startswith("openclaw"):
        model = None  # A routing alias is not a concrete inference model.
    usage = payload.get("usage")
    if isinstance(usage, dict):
        usage = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"),
                 "total": usage.get("total_tokens")}
    runtime_trace.report(model=model, usage=usage)
    token_quota.record(usage, model)
    return answer


# The question and the notebook or report it is about, set by a caller so a stored demo reply can be
# listed by question once the budget is spent.
QUESTION: ContextVar[tuple[str, str] | None] = ContextVar("question", default=None)


async def turn(prompt: str, session_id: str, *, instructions: str | None = None, response_format: dict | None = None, model_role: str = "agent") -> tuple[str, str]:
    """Records execution timing for a turn without copying its content."""
    context = nullcontext() if runtime_trace.active() else runtime_trace.span("agent_request")
    with context:
        runtime_trace.event("model_requested")
        result = await _turn(prompt, session_id, instructions=instructions,response_format=response_format,model_role=model_role) if instructions or response_format or model_role!="agent" else await _turn(prompt, session_id)
        runtime_trace.event("model_replied")
        return result


async def _turn(prompt: str, session_id: str, *, instructions: str | None = None, response_format: dict | None = None, model_role: str = "agent") -> tuple[str, str]:
    """Run one isolated job via OpenClaw's documented Responses endpoint.

    Closing this request cancels the gateway run, including on task cancellation.
    No retries occur here: callers own durable jobs and answer-format retries.
    """
    from stellis import storage
    # The direct path checks the allowance once it knows the request: a demo reply already stored costs nothing.
    if not (runtime_profile.direct() or storage.in_demo()) and (message := token_quota.exceeded()):
        raise GatewayError(message)
    if runtime_profile.direct() or storage.in_demo():
        return await _direct_turn(prompt, session_id, instructions=instructions,response_format=response_format,model_role=model_role) if instructions or response_format or model_role!="agent" else await _direct_turn(prompt, session_id)
    endpoint, token = _settings()
    if not session_id or len(session_id) > 200 or any(c not in
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in session_id):
        raise GatewayError("Invalid agent session")
    headers = {"Authorization": "Bearer " + token,
               "x-openclaw-session-key": "stellis-" + session_id}
    if runtime_profile.model_id(model_role):
        # OpenClaw's documented backend-model override preserves managed execution.
        provider = os.environ.get("STELLIS_AGENT_MODEL_PROVIDER", os.environ.get("STELLIS_CLOUD_MODEL_PROVIDER", "ollama-local" if runtime_profile.local_inference() else "nebius")).strip()
        headers["x-openclaw-model"] = provider + "/" + runtime_profile.model_id(model_role)
    try:
        async with asyncio.timeout(150):
            async with httpx.AsyncClient(transport=privacy.AsyncTransport(), timeout=150, follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", endpoint + "/v1/responses", headers=headers,
                        json={"model": "openclaw/main", "input": prompt, "stream": False, **({"instructions": instructions} if instructions else {})}) as response:
                    if response.status_code != 200:
                        raise GatewayError("Cloud agent service is unavailable. Try again.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1_048_576:
                            raise GatewayError("Agent reply was too long. Try again.")
        return _reply(json.loads(body)), session_id
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise GatewayError("Agent reply timed out. Try again.") from exc
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise GatewayError("Cloud agent reply failed. Try again.") from exc


def request_options(role):
    """Reason through clinical source comparisons; keep tool/planning turns bounded."""
    if role in {'review','summary'}:return {'temperature':0.2,'max_tokens':2048,'chat_template_kwargs':{'enable_thinking':False}}
    reasons=role in {'report','answers','research'}
    return {'temperature':0.2,'max_tokens':16384 if reasons else 4096,'chat_template_kwargs':{'enable_thinking':reasons}}


class _CutOff(Exception):
    """The model stopped at its token limit."""


async def _complete(url: str, key: str, body: dict, provider: str) -> str:
    """One chat completion's text, or GatewayError; _CutOff when it stopped at its token limit."""
    if (message := token_quota.exceeded()) and not privacy.replayable(url, body):
        raise GatewayError(message)
    try:
        async with asyncio.timeout(150):
            async with httpx.AsyncClient(transport=privacy.AsyncTransport(), timeout=150, follow_redirects=False, trust_env=False) as client:
                response = await client.post(url, headers={"Authorization": "Bearer " + key}, json=body)
        if response.status_code == 429:
            raise GatewayError(f"{provider} rate limit reached. Try again later.")
        if response.status_code != 200:
            raise GatewayError(f"{provider} is unavailable. Try again.")
        data = response.json()
        runtime_trace.report(model=data.get("model"), usage=data.get("usage"))
        token_quota.record(data.get("usage"), data.get("model"))
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise _CutOff
        answer = (choice["message"]["content"] or "").strip()
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise GatewayError("Agent reply timed out. Try again.") from exc
    except (httpx.HTTPError, ValueError, UnicodeError, KeyError, IndexError, TypeError) as exc:
        raise GatewayError("Agent reply failed. Try again.") from exc
    if not answer:
        raise GatewayError("Agent reply failed. Try again.")
    return answer


async def _direct_turn(prompt: str, session_id: str, *, instructions: str | None = None, response_format: dict | None = None, model_role: str = "agent") -> tuple[str, str]:
    """Run a legacy direct or demo completion without a managed agent sandbox.

    The prompt already carries the agent's instructions, memories and numbered sources,
    so the caller's citation checks and retries apply unchanged.
    """
    from stellis import ai
    key = ai._key()
    provider = "Local inference" if runtime_profile.local_inference() else "Token Factory"
    if not key:
        raise GatewayError("Add a Nebius API key first")
    body = {"model": runtime_profile.model_id(model_role), "messages": ([{"role":"system","content":instructions}] if instructions else []) + [{"role": "user", "content": prompt}],
            **request_options(model_role)}
    if response_format:body["response_format"]=response_format
    url = runtime_profile.inference_url() + "/chat/completions"
    try:
        answer = await _complete(url, key, body, provider)
    except _CutOff:
        # Reasoning can use up the whole reply on a long history (demo, 2026-10-30): one more try answers
        # without it, rather than showing the person an error.
        if not body.get("chat_template_kwargs", {}).get("enable_thinking"):
            raise GatewayError("Agent reply was too long. Try again.") from None
        try:
            answer = await _complete(url, key, {**body, "max_tokens": 4096, "chat_template_kwargs": {"enable_thinking": False}}, provider)
        except _CutOff:
            raise GatewayError("Agent reply was too long. Try again.") from None
    if label := QUESTION.get():
        privacy.index_stored(url, body, *label)
    return answer, session_id
