#!/usr/bin/env python3
from __future__ import annotations

import atexit
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from integration_test_support import (
    BASE_ORDER_URL,
    BASE_PRODUCT_URL,
    BASE_USER_URL,
    COMPOSE_FILE,
    wait_for_stack,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def log(message: str) -> None:
    print(f"[flashsale-compose] {message}")


def require_cmd(command: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(f"Missing required command: {command}")


def cleanup() -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "down",
            "-v",
            "--remove-orphans",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def run_tests(start_dir: Path) -> None:
    top_level_dir = start_dir.parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(start_dir),
            "-t",
            str(top_level_dir),
            "-p",
            "*_integration.py",
        ],
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run flashsale Docker Compose integration tests."
    )
    parser.add_argument(
        "--suite",
        choices=["order", "product", "user", "all"],
        default="all",
        help="Choose which integration test suite to execute.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    require_cmd("docker")
    require_cmd("python3")

    atexit.register(cleanup)

    log("Starting flashsale integration stack")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
        check=True,
    )

    wait_for_stack()
    log(f"Services ready: {BASE_USER_URL}, {BASE_PRODUCT_URL}, {BASE_ORDER_URL}")

    if args.suite in {"product", "all"}:
        log("Running product-service integration tests")
        run_tests(REPO_ROOT / "flashsale" / "product-service" / "tests" / "integration")

    if args.suite in {"order", "all"}:
        log("Running order-service integration tests")
        run_tests(REPO_ROOT / "flashsale" / "order-service" / "tests" / "integration")

    if args.suite in {"user", "all"}:
        log("Running user-service integration tests")
        run_tests(REPO_ROOT / "flashsale" / "user-service" / "tests" / "integration")

    log("Integration PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
