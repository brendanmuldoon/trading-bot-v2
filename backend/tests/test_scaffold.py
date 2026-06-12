"""Scaffold sanity test: all backend packages import cleanly."""

import importlib

import pytest

PACKAGES = [
    "backend",
    "backend.api",
    "backend.broker",
    "backend.models",
    "backend.risk",
    "backend.strategies",
    "backend.workflows",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_package_imports(package: str) -> None:
    importlib.import_module(package)
