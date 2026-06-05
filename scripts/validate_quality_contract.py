#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "release/schemas/flashsale-quality-contract.schema.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the flashsale quality contract against its JSON schema."
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to flashsale-quality-contract.yaml",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Path to the JSON schema file.",
    )
    args = parser.parse_args()

    with args.schema.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    with args.manifest.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        for error in errors:
            path = ".".join(str(part) for part in error.absolute_path) or "<root>"
            print(f"{path}: {error.message}")
        raise SystemExit(1)

    print(f"{args.manifest} is valid")


if __name__ == "__main__":
    main()
