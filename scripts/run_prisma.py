#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "prisma"
ROOT_SCHEMA_FILE = SCHEMA_PATH / "schema.prisma"
DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql://test:test@localhost:15432/flashsale"

COMMANDS: dict[str, list[str]] = {
    "format": ["prisma", "format"],
    "validate": ["prisma", "validate"],
    "generate": ["prisma", "generate", "--generator", "client"],
    "migrate-deploy": ["prisma", "migrate", "deploy"],
    "migrate-status": ["prisma", "migrate", "status"],
}

MIGRATE_COMMANDS = {"migrate-deploy", "migrate-status"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Prisma schema and migration commands for flashsale."
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    return parser.parse_args()


def run_command(command_name: str) -> None:
    env = os.environ.copy()
    env.setdefault(DATABASE_URL_ENV, DEFAULT_DATABASE_URL)
    schema_path = ROOT_SCHEMA_FILE if command_name in MIGRATE_COMMANDS else SCHEMA_PATH
    command = [*COMMANDS[command_name], "--schema", str(schema_path)]
    print(
        f"[flashsale-prisma] command={command_name} "
        f"schema={schema_path.relative_to(REPO_ROOT)} "
        f"url_env={DATABASE_URL_ENV}",
        flush=True,
    )
    subprocess.run(command, check=True, cwd=REPO_ROOT, env=env)


def main() -> int:
    args = parse_args()
    try:
        run_command(args.command)
    except subprocess.CalledProcessError as exc:
        return int(exc.returncode or 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
