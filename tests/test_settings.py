from __future__ import annotations

import pytest
from qgis.PyQt.QtCore import QSettings

from PolygonsParallelToLine.src.settings import MapToolSettings

pytestmark = pytest.mark.usefixtures("isolate_settings")


def test_default_by_longest_is_false():
    settings = MapToolSettings()
    assert settings.by_longest is False


def test_by_longest_persists_across_instances():
    settings = MapToolSettings()
    settings.by_longest = True
    QSettings().sync()

    reloaded = MapToolSettings()
    assert reloaded.by_longest is True


def test_setting_same_value_emits_no_signal():
    settings = MapToolSettings()
    emissions: list[None] = []
    settings.changed.connect(lambda: emissions.append(None))

    settings.by_longest = False
    assert emissions == []

    settings.by_longest = True
    assert len(emissions) == 1

    settings.by_longest = True
    assert len(emissions) == 1


@pytest.mark.parametrize("attr", ["pick_reference_segment", "pick_target_segment"])
def test_default_segment_picking_flags_are_false(attr):
    settings = MapToolSettings()
    assert getattr(settings, attr) is False


@pytest.mark.parametrize("attr", ["pick_reference_segment", "pick_target_segment"])
def test_segment_picking_flags_persist_across_instances(attr):
    settings = MapToolSettings()
    setattr(settings, attr, True)
    QSettings().sync()

    reloaded = MapToolSettings()
    assert getattr(reloaded, attr) is True


@pytest.mark.parametrize("attr", ["pick_reference_segment", "pick_target_segment"])
def test_segment_picking_setter_is_idempotent(attr):
    settings = MapToolSettings()
    emissions: list[None] = []
    settings.changed.connect(lambda: emissions.append(None))

    setattr(settings, attr, False)
    assert emissions == []

    setattr(settings, attr, True)
    assert len(emissions) == 1

    setattr(settings, attr, True)
    assert len(emissions) == 1
