"""Command line entry point for the local browser app."""

from __future__ import annotations

import argparse
import copy
import logging
import os

import uvicorn

from stellis import intake_voice, models, tools
from stellis.processing import model_paths


def main() -> None:
    """Start the browser app or report local speech-tool readiness."""
    parser = argparse.ArgumentParser(prog="stellis")
    parser.add_argument("--port", type=int, default=8765, help="port for the local browser app")
    command = parser.add_subparsers(dest="command")
    serve = command.add_parser("serve", help="Start the local browser app (the default)")
    serve.add_argument("--port", type=int, default=8765)
    command.add_parser("setup", help="Download the speech models now")
    sync_serve = command.add_parser("sync-serve", help="Start the separate development account service")
    sync_serve.add_argument("--port", type=int, default=8769)
    command.add_parser("doctor", help="Check the speech tools and models")
    launch = command.add_parser("launch", help="Launch the local or cloud NemoClaw version")
    launch.add_argument("version", choices=("local", "cloud"))
    launch.add_argument("--env-file", help="configuration file (default: .private/<version>.env)")
    launch.add_argument("--port", type=int, default=None)
    mode = launch.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="create a private configuration template")
    mode.add_argument("--check", action="store_true", help="validate configuration without starting services")
    args = parser.parse_args()
    args.command = args.command or "serve"

    if args.command == "launch":
        from stellis import launch
        try:
            launch.run(args)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        return

    if args.command == "doctor":
        print(f"ffmpeg: {tools.ffmpeg()}")
        print("whisper.cpp: pywhispercpp")
        for label, model in zip(("whisper model", "segmentation model", "speaker embedding model"), model_paths()):
            print(f"{label}: {model if model.is_file() else 'missing (downloads when Stellis starts)'}")
        return
    if args.command == "setup":
        if not models.missing() and not intake_voice.missing():
            print(f"Speech models are ready in {models.directory()}")
            return
        print(f"Downloading speech models to {models.directory()} …")
        models.install()
        state = models.status()
        if state["state"] != "ready":
            raise SystemExit(f"Model download failed: {state['error']}")
        print("Speech models are ready.")
        intake_voice.install()
        if intake_voice.status()["state"] != "ready":
            raise SystemExit(f"Call model download failed: {intake_voice.status()['error']}")
        return

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    # Loopback unless a hosted deployment sets STELLIS_HOST behind its own TLS proxy.
    host = os.environ.get("STELLIS_HOST", "127.0.0.1")
    if args.command == "serve":
        os.environ.setdefault("STELLIS_AUTH_MODE", "required")
        models.ensure_in_background()
        intake_voice.ensure_in_background()
        print(f"Stellis Health is running at http://{host}:{args.port}/")
    target = ("stellis.sync_server:app" if args.command == "sync-serve" else
              "stellis.account_gateway:app" if os.environ.get("STELLIS_AUTH_MODE") == "required" else
              "stellis.server:app")
    uvicorn.run(target, host=host, port=args.port, log_config=log_config(), **proxy_options())


class QuerylessAccessFilter(logging.Filter):
    """Drop query strings from access log lines; they can hold search text and other health content."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            record.args = (client, method, str(path).split("?", 1)[0], version, status)
        return True


def log_config() -> dict[str, object]:
    """Return uvicorn's logging setup with query strings removed from access lines."""
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["filters"] = {"queryless": {"()": QuerylessAccessFilter}}
    config["handlers"]["access"]["filters"] = ["queryless"]
    return config


def proxy_options() -> dict[str, object]:
    """Trust X-Forwarded-Proto only from the hosts a TLS proxy deployment names, so origin checks see https."""
    trusted = os.environ.get("STELLIS_FORWARDED_ALLOW_IPS", "").strip()
    return {"proxy_headers": True, "forwarded_allow_ips": trusted} if trusted else {"proxy_headers": False}
