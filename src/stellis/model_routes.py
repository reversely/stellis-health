"""Persistent model choices per inference role and deployment profile."""
from __future__ import annotations

import json
import os
import re
from stellis import storage

ROLES = {
    "naming": ("Document naming", "completion"),
    "review": ("Evidence review", "completion"),
    "care": ("Care review", "completion"),
    "report": ("Report explanations", "completion"),
    "agent": ("Managed agents", "tools"),
    "transcript": ("Transcript AI", "completion"),
    "categories": ("Categories", "completion"),
    "planning": ("Notebook planning", "completion"),
}


def path():
    from stellis import runtime_profile
    profile = "local" if runtime_profile.local_inference() else runtime_profile.selected() if runtime_profile.selected() in {"cloud", "direct"} else "legacy"
    return storage.base_dir() / f"model-routes-{profile}.json"


def choices():
    file = path()
    if not file.exists():
        return {}
    data = json.loads(file.read_text())
    if not isinstance(data, dict):
        raise ValueError("Saved model routes are unreadable")
    # Saved files can name a role that a later release removed.
    data = {role: model for role, model in data.items() if role in ROLES}
    validate(data)
    return data


def validate(routes):
    if not isinstance(routes, dict) or any(role not in ROLES for role in routes):
        raise ValueError("Choose a listed inference role")
    if "naming" in routes and "nemotron" not in str(routes["naming"]).casefold():
        raise ValueError("Document naming requires a Nemotron model")
    for model in routes.values():
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
            raise ValueError("Enter a valid model ID")


def save(routes):
    """Atomically replace already validated role choices; callers own runtime guards."""
    validate(routes)
    file = path(); temporary = file.with_suffix(".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(routes, output)
        os.replace(temporary, file)
    finally:
        temporary.unlink(missing_ok=True)
