#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def install_psycopg_stub() -> None:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_rows_stub = types.ModuleType("psycopg.rows")
    setattr(psycopg_stub, "connect", None)
    setattr(psycopg_rows_stub, "dict_row", object())
    sys.modules["psycopg"] = psycopg_stub
    sys.modules["psycopg.rows"] = psycopg_rows_stub


def clear_service_modules() -> None:
    for module_name in tuple(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]


def export_service_openapi(service_dir: Path, output_path: Path) -> None:
    clear_service_modules()
    install_psycopg_stub()

    sys.path.insert(0, str(service_dir))
    try:
        app_module = importlib.import_module("app.main")
        spec = app_module.app.openapi()
    finally:
        sys.path.pop(0)

    output_path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def main() -> int:
    services = {
        REPO_ROOT
        / "flashsale"
        / "order-service": REPO_ROOT
        / "flashsale"
        / "order-service"
        / "openapi.yaml",
        REPO_ROOT
        / "flashsale"
        / "product-service": REPO_ROOT
        / "flashsale"
        / "product-service"
        / "openapi.yaml",
    }

    for service_dir, output_path in services.items():
        export_service_openapi(service_dir, output_path)
        print(f"wrote {output_path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
