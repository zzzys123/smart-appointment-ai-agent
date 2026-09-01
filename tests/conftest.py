"""Test-layer policy: online tests require an explicit opt-in."""

import pytest


LAYER_MARKERS = ("unit", "integration", "online")


def pytest_addoption(parser):
    parser.addoption(
        "--run-online",
        action="store_true",
        default=False,
        help="run tests that call real model providers and may incur cost",
    )


def pytest_collection_modifyitems(config, items):
    run_online = config.getoption("--run-online")
    skip_online = pytest.mark.skip(
        reason="requires --run-online and valid provider credentials"
    )
    for item in items:
        layers = [name for name in LAYER_MARKERS if item.get_closest_marker(name)]
        if not layers:
            item.add_marker(pytest.mark.unit)
        if item.get_closest_marker("online") and not run_online:
            item.add_marker(skip_online)
