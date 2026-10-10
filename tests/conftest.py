import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.db import get_engine

SAMPLE_WORKBOOK = Path(__file__).resolve().parent.parent / "Claude outputs" / "SHIRORO_05102026.xlsx"


@pytest.fixture(scope="session")
def engine():
    return get_engine()


@pytest.fixture
def sample_path():
    return SAMPLE_WORKBOOK
