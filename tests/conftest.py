from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from qgis.PyQt.QtCore import QSettings

from PolygonsParallelToLine.src import diagnostics

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def reset_diagnostics() -> Iterator[None]:
    diagnostics.teardown_logging()
    yield
    diagnostics.teardown_logging()


@pytest.fixture
def isolate_settings() -> Iterator[None]:
    settings = QSettings()
    settings.remove("PolygonsParallelToLine")
    settings.sync()
    yield
    settings = QSettings()
    settings.remove("PolygonsParallelToLine")
    settings.sync()
