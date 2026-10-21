"""The nitpick-core command line.

    nitpick-core serve                 run the service
    nitpick-core agents                list the agents in the registry
    nitpick-core dispatch <handle>     queue one agent by hand
    nitpick-core version
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitpick-core",
        description="Run voice agents from a minidb registry, dispatched through Prefect.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the service.")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--minidb", default=None, help="Path to the minidb database with the registry."
    )

    agents = subparsers.add_parser("agents", help="List the agents in the registry.")
    agents.add_argument("--minidb", default=None)
    agents.add_argument("--json", action="store_true", help="Print raw JSON.")

    dispatch = subparsers.add_parser("dispatch", help="Queue one agent by hand.")
    dispatch.add_argument("handle")
    dispatch.add_argument("--room", default=None)
    dispatch.add_argument("--minidb", default=None)
    dispatch.add_argument("--url", default=None, help="The running service to dispatch to.")

    version = subparsers.add_parser("version", help="Print the version.")
    version.set_defaults(handler=_version)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "serve":
        return _serve(args)
    if args.command == "agents":
        return _agents(args)
    if args.command == "dispatch":
        return _dispatch(args)
    if args.command == "version":
        return _version(args)
    return 1


def _config(args: argparse.Namespace):
    config = load_config()
    if getattr(args, "minidb", None):
        config.minidb_path = args.minidb
    if getattr(args, "host", None):
        config.host = args.host
    if getattr(args, "port", None):
        config.port = args.port
    return config


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    config = _config(args)
    app = create_app(config)
    logging.info(
        "nitpick-core listening on http://%s:%d (registry: %s)",
        config.host,
        config.port,
        config.minidb_path,
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    return 0


def _agents(args: argparse.Namespace) -> int:
    from .registry import get_registry

    config = _config(args)
    try:
        agents = get_registry(config).list_agents()
    except Exception as exc:  # noqa: BLE001 - a missing database is a normal case
        print(f"could not read the registry: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(agents, indent=2, default=str))
        return 0

    if not agents:
        print("no agents in the registry")
        return 0

    print(f"{len(agents)} agent(s):")
    for agent in agents:
        print(
            f"  {agent.get('handle', '?'):<20} {agent.get('name', '?'):<28} "
            f"llm={agent.get('model') or '-'} stt={agent.get('stt') or '-'} "
            f"tts={agent.get('tts') or '-'}"
        )
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    import httpx

    config = _config(args)
    base = args.url or f"http://127.0.0.1:{config.port}"
    payload: dict = {"handle": args.handle}
    if args.room:
        payload["room_id"] = args.room

    try:
        response = httpx.post(f"{base}/tasks", json=payload, timeout=30.0)
    except httpx.HTTPError as exc:
        print(f"could not reach {base}: {exc}", file=sys.stderr)
        return 1

    if response.status_code >= 400:
        print(f"dispatch failed: {response.text}", file=sys.stderr)
        return 1

    print(json.dumps(response.json(), indent=2, default=str))
    return 0


def _version(_args: argparse.Namespace) -> int:
    from . import __version__

    print(f"nitpick-core {__version__}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
