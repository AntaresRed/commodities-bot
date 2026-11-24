import os

import pytest

from bot.config import load_instruments, load_settings


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="network test; set RUN_NETWORK_TESTS=1 to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def instruments():
    return load_instruments()


@pytest.fixture(scope="session")
def inst(instruments):
    return instruments.by_symbol()
