"""Product-service test wrapper for the shared compose integration helpers."""

# pylint: disable=duplicate-code

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_support_module() -> ModuleType:
    """Load the shared compose integration helper module from flashsale/scripts."""
    repo_root = Path(__file__).resolve().parents[2]
    support_path = repo_root / "scripts" / "integration_test_support.py"
    spec = importlib.util.spec_from_file_location(
        "_flashsale_integration_test_support", support_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Unable to load integration test support from {support_path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SUPPORT = _load_support_module()

FlashsaleIntegrationClient = _SUPPORT.FlashsaleIntegrationClient
reset_services = _SUPPORT.reset_services
wait_for_stack = _SUPPORT.wait_for_stack

__all__ = ["FlashsaleIntegrationClient", "reset_services", "wait_for_stack"]
