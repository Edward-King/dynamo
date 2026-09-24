"""
Entry point: `python -m photoshare --config config.dev.yaml`

The --config flag is required (v3.0 startup sequence step 1, YAML config
proposal) -- there is no implicit default file, and a missing/unreadable
path fails startup immediately.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

from photoshare.api.main import APP_FACTORY_TARGET, CONFIG_PATH_ENV_VAR
from photoshare.config.schema import Settings, load_settings


def build_uvicorn_run_kwargs(settings: Settings) -> dict[str, Any]:
    """Build the Uvicorn options shared by single- and multi-worker startup.

    Uvicorn can only start multiple workers from an import string, not from
    an already-constructed ASGI application.  ``APP_FACTORY_TARGET`` is a
    zero-argument factory; it reads ``CONFIG_PATH_ENV_VAR`` set by ``main`` in
    each worker process.
    """
    return {
        "host": settings.server.host,
        "port": settings.server.port,
        "workers": settings.server.workers,
        "factory": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="photoshare")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a YAML config file (e.g. config.dev.yaml or config.prod.yaml). Required -- no default.",
    )
    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # Pydantic ValidationError, YAML parse errors, etc.
        print(f"FATAL: invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.logging.level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Uvicorn worker subprocesses only receive the import string, not this
    # command line's parsed arguments.  Preserve --config as the public
    # interface, then pass its absolute path through the inherited
    # environment for the zero-argument application factory.
    os.environ[CONFIG_PATH_ENV_VAR] = str(Path(args.config).resolve())
    uvicorn.run(APP_FACTORY_TARGET, **build_uvicorn_run_kwargs(settings))


if __name__ == "__main__":
    main()
