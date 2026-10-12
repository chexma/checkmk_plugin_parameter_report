import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def site_with_test_hosts() -> None:
    root = os.environ.get("OMD_ROOT")
    if not root or not (Path(root) / "var/check_mk/autochecks/spike-0000.mk").exists():
        pytest.skip("needs the test hosts: tests/fixtures/create_test_hosts.sh")
