"""Inspect configured routes and switch only the owner's installed local models."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timezone

import httpx

from stellis import deployment
from stellis import agent_runtime, ai, call_tts, categorization, runtime_profile, runtime_trace, storage, recording_preview, processing, model_routes
from stellis import privacy

_SWITCH_LOCK = threading.Lock()


def _signature():
    values = [runtime_profile.selected(),runtime_profile.agent_backend(),runtime_profile.inference_provider(),call_tts.status(), *[runtime_profile.model_id(role) for role in model_routes.ROLES],
              os.environ.get("STELLIS_NEMOCLAW_SANDBOX", ""),
              os.environ.get("NEMOCLAW_GATEWAY_PORT", ""),
              os.environ.get("STELLIS_AGENT_GATEWAY_URL", ""),
              os.environ.get("STELLIS_LOCAL_INFERENCE_URL", ""),
              os.environ.get("STELLIS_CHATTERBOX_NEBIUS_URL", ""),
              os.environ.get("STELLIS_CHATTERBOX_NEBIUS_TOKEN", ""),
              os.environ.get("STELLIS_CHATTERBOX_LOCAL_URL", ""),
              os.environ.get("STELLIS_CHATTERBOX_LOCAL_TOKEN", ""),
              os.environ.get("STELLIS_AGENT_GATEWAY_TOKEN", ""), ai._key() or ""]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def can_switch():
    return (runtime_profile.local_inference() and runtime_profile.agent_backend()=='nemoclaw' and os.environ.get("STELLIS_ACCOUNT_ID") == "1"
            and bool(os.environ.get("STELLIS_NEMOCLAW_SANDBOX")))


def status():
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM runtime_observations WHERE id = 1").fetchone()
    observed = json.loads(row["result_json"]) if row and row["signature"] == _signature() else None
    speech=call_tts.status()
    return {"profile": runtime_profile.selected(), "deployment":runtime_profile.deployment(),"agent_backend":runtime_profile.agent_backend(),"local_inference": runtime_profile.local_inference(),
            "agent_execution": runtime_profile.agent_execution(),
            "speech_execution": "Application host", "tts_execution":speech['provider'],"tts":speech,
            "agent_configured": agent_runtime.configured(),
            "inference_provider": runtime_profile.inference_provider(),
            "inference_model": runtime_profile.model_id(), "agent_model": runtime_profile.model_id("agent"), "inference_configured": bool(ai._key()),
            "category_model": categorization._model_id(), "can_switch": can_switch(),
            "budget": deployment.budget(), "operator": runtime_profile.operator(),
            "routes": [{"role":role,"label":label,"model":runtime_profile.model_id(role),
                        "capability":capability,"editable": role != "agent" or can_switch() or runtime_profile.remote_agents(),
                        "observed": (observed or {}).get("routes",{}).get(role)}
                       for role,(label,capability) in model_routes.ROLES.items()],
            "recommended": [] if runtime_profile.local_inference() else
                [{"model": model, "label": label} for model, label in runtime_profile.RECOMMENDED],
            "speech": {"live_installed": recording_preview.installed(), **processing.capabilities()["features"]},
            "observed": observed, "traces": runtime_trace.recent()}


def _cli(args, timeout=25):
    command = agent_runtime._command()
    try:
        result = subprocess.run([command, *args], capture_output=True, text=True,
                                env=agent_runtime._cli_env(command), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("The local agent runtime did not respond") from exc
    if result.returncode or len(result.stdout) > 1_048_576:
        raise RuntimeError("The local agent runtime check failed")
    return result.stdout


def _agent_route():
    try:
        result = json.loads(_cli(["inference", "get", "--json"]))
        if not isinstance(result, dict):
            raise ValueError()
        return {key: result[key][:200] for key in ("provider", "model")
                if isinstance(result.get(key), str)}
    except (ValueError, TypeError) as exc:
        raise RuntimeError("The agent runtime returned an unreadable route") from exc


def check():
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "models": [],
              "inference": {"state": "not_configured"}, "agent": {"state": "not_configured"}}
    if ai._key():
        try:
            with httpx.Client(transport=privacy.Transport(), timeout=12, trust_env=False, follow_redirects=False) as client:
                response = client.get(runtime_profile.inference_url() + "/models",
                                      headers={"Authorization": "Bearer " + ai._key()})
            if response.status_code != 200:
                raise ValueError()
            items = response.json()["data"]
            if not isinstance(items, list):
                raise ValueError()
            result["models"] = sorted({item["id"] for item in items if isinstance(item, dict)
                                      and isinstance(item.get("id"), str) and len(item["id"]) <= 200})[:100]
            present = runtime_profile.model_id() in result["models"]
            result["inference"] = {"state": "available" if present else "model_missing"}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            result["inference"] = {"state": "unavailable"}
    result['tts']=call_tts.check()
    if runtime_profile.direct():
        result['agent']={'state':result['inference']['state'],'execution':'direct','model':runtime_profile.model_id('agent')}
        if result['agent']['state'] not in {'unavailable','not_configured'}:result['agent']['state']='available' if runtime_profile.model_id('agent') in result['models'] else 'model_missing'
    elif agent_runtime.configured():
        if runtime_profile.remote_agents():
            # Configuration is not proof of gateway health or the cloud's inference route.
            result["agent"] = {"state": "remote_unverified"}
        else:
            try:
                route = _agent_route()
                result["agent"] = {"state": "reported", **route}
                if runtime_profile.local_inference() and (route.get("model") != runtime_profile.model_id("agent")
                                                         or route.get("provider") != "ollama-local"):
                    result["agent"]["state"] = "mismatch"
            except RuntimeError:
                result["agent"] = {"state": "unavailable"}
    result["routes"] = {}
    for role in model_routes.ROLES:
        model = runtime_profile.model_id(role)
        state = result["inference"]["state"]
        if not model:
            state = "not_configured"
        elif state not in {"unavailable", "not_configured"}:
            state = "available" if model in result["models"] else "model_missing"
        result["routes"][role] = {"state": state}
    result["routes"]["agent"] = result["agent"]
    with storage.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO runtime_observations VALUES (1, ?, ?, ?)",
                     (_signature(), result["checked_at"], json.dumps(result)))
    return status()


def _capabilities(model):
    base = runtime_profile.inference_url().removesuffix("/v1")
    try:
        with httpx.Client(transport=privacy.Transport(), timeout=15, trust_env=False, follow_redirects=False) as client:
            if runtime_profile.inference_provider()!='Ollama':
                response=client.post(runtime_profile.inference_url()+'/chat/completions',headers={'Authorization':'Bearer '+ai._key()},json={
                    'model':model,'messages':[{'role':'user','content':'Call the check_connection tool once.'}],
                    'tools':[{'type':'function','function':{'name':'check_connection','parameters':{'type':'object','properties':{},'additionalProperties':False}}}],
                    'tool_choice':{'type':'function','function':{'name':'check_connection'}},'max_tokens':128,'stream':False})
                response.raise_for_status();choice=response.json()['choices'][0]['message']
                return ['completion','tools'] if any(c.get('function',{}).get('name')=='check_connection' for c in choice.get('tool_calls',[])) else ['completion']
            response = client.post(base + "/api/show", json={"model":model})
        response.raise_for_status()
        return response.json().get("capabilities", [])
    except (httpx.HTTPError, ValueError, TypeError,KeyError,IndexError) as exc:
        raise RuntimeError("Could not check this model's capabilities") from exc


def _apply_agent(model):
    _cli(["inference", "set", "--provider", "ollama-local", "--model", model,
          "--sandbox", os.environ["STELLIS_NEMOCLAW_SANDBOX"]], timeout=180)
    route = _agent_route()
    if route.get("model") != model or route.get("provider") != "ollama-local":
        raise RuntimeError("The agent runtime did not confirm the selected model")


def save_routes(changes):
    model_routes.validate(changes)
    if not changes:
        raise ValueError("Choose a model route to save")
    if "agent" in changes and not (can_switch() or runtime_profile.remote_agents()):
        raise ValueError("Managed model switching is available to the deployment owner")
    if not _SWITCH_LOCK.acquire(blocking=False):
        raise ValueError("A model change is already running")
    try:
        with runtime_trace.model_change():
            current = check()
            observed = current["observed"]
            if runtime_profile.local_inference():
                capabilities = {}
                for role,model in changes.items():
                    if model not in observed["models"]:
                        raise ValueError("Choose an installed model. Check connections to refresh the list.")
                    if model not in capabilities:
                        capabilities[model] = _capabilities(model)
                    required = model_routes.ROLES[role][1]
                    if required not in capabilities[model]:
                        raise ValueError(f"{model} does not support {required}, required for {model_routes.ROLES[role][0]}.")
            elif observed["models"]:
                if any(model not in observed["models"] for model in changes.values()):
                    raise ValueError("Choose a model listed by Token Factory")
            previous = None
            if runtime_profile.local_inference() and runtime_profile.agent_backend()=='nemoclaw' and "agent" in changes:
                route = observed["agent"]
                if route.get("provider") != "ollama-local" or not route.get("model"):
                    raise ValueError("The managed agent must use the local Ollama provider")
                previous = route["model"]
            try:
                if previous and previous != changes["agent"]:
                    _apply_agent(changes["agent"])
                model_routes.save({**model_routes.choices(), **changes})
            except (RuntimeError, OSError, ValueError) as exc:
                if previous and previous != changes["agent"]:
                    try:
                        _apply_agent(previous)
                    except RuntimeError:
                        raise RuntimeError("Model change failed and the previous route could not be restored. Check connections.") from exc
                raise RuntimeError("Model change failed. Previous settings were retained.") from exc
        return check()
    finally:
        _SWITCH_LOCK.release()


def switch_model(model):
    """Compatibility for the previous coupled local-model control."""
    if not can_switch():
        raise ValueError("Model switching is available to the local deployment owner")
    return save_routes({role:model for role in ("agent","transcript","categories","planning")})
