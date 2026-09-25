from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from qgis.PyQt.QtCore import QSettings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def isolate_settings() -> Iterator[None]:
    settings = QSettings()
    settings.remove("PolygonsParallelToLine")
    settings.sync()
    yield
    settings = QSettings()
    settings.remove("PolygonsParallelToLine")
    settings.sync()
