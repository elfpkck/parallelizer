from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest
from qgis.core import QgsApplication
from qgis.gui import QgsMessageBar
from qgis.PyQt.QtCore import QCoreApplication  # type: ignore[import-not-found]

from PolygonsParallelToLine.src import diagnostics
from PolygonsParallelToLine.src.plugin import Plugin
from PolygonsParallelToLine.src.provider import Provider


@pytest.fixture(scope="function")
def plugin_instance():
    """
    Fixture to create and return a Plugin instance.
    Cleaning up processing providers registered within the QgsApplication's processing registry.
    """
    yield Plugin()
    _remove_providers()


def _remove_providers() -> None:
    for provider in QgsApplication.processingRegistry().providers():
        QgsApplication.processingRegistry().removeProvider(provider)


def test_plugin_initialization(plugin_instance):
    """Test if the provider is None on Plugin initialization."""
    assert plugin_instance.provider is None


def test_init_processing(plugin_instance):
    """Test if initProcessing sets up the provider correctly."""
    plugin_instance.initProcessing()
    assert isinstance(plugin_instance.provider, Provider)
    assert plugin_instance.provider in QgsApplication.processingRegistry().providers()


def test_init_gui(plugin_instance):
    """Test if initGui initializes processing properly."""
    plugin_instance.initGui()
    assert isinstance(plugin_instance.provider, Provider)
    assert plugin_instance.provider in QgsApplication.processingRegistry().providers()


def test_unload(plugin_instance):
    """Test if unload removes the provider from the processing registry."""
    plugin_instance.initProcessing()
    provider = plugin_instance.provider
    plugin_instance.unload()
    assert plugin_instance.provider is None
    assert provider not in QgsApplication.processingRegistry().providers()


def _patch_iface(qgis_iface, monkeypatch) -> None:
    # pytest-qgis's mock iface lacks the plugin menu APIs, and its message bar can't show widgets.
    monkeypatch.setattr(qgis_iface, "addPluginToVectorMenu", Mock(), raising=False)
    monkeypatch.setattr(qgis_iface, "removePluginVectorMenu", Mock(), raising=False)
    bar = QgsMessageBar()
    monkeypatch.setattr(qgis_iface, "messageBar", lambda: bar)


@pytest.fixture
def gui_plugin(qgis_iface, isolate_settings, monkeypatch):
    _patch_iface(qgis_iface, monkeypatch)
    plugin = Plugin(qgis_iface)
    plugin.initGui()
    yield plugin
    plugin.unload()
    _remove_providers()


def test_init_gui_adds_report_action_and_logging(gui_plugin):
    gui_plugin.iface.addPluginToVectorMenu.assert_any_call("Parallelizer", gui_plugin.report_action)
    assert diagnostics.logger.handlers


def test_unload_removes_report_action_and_logging(gui_plugin):
    report_action = gui_plugin.report_action

    gui_plugin.unload()

    gui_plugin.iface.removePluginVectorMenu.assert_any_call("Parallelizer", report_action)
    assert gui_plugin.report_action is None
    assert diagnostics.logger.handlers == []


def test_recorded_error_offers_report_once_per_interval(gui_plugin):
    bar = gui_plugin.iface.messageBar()

    diagnostics.notifier.error_recorded.emit()
    diagnostics.notifier.error_recorded.emit()
    QCoreApplication.processEvents()

    assert len(bar.items()) == 1


def test_error_from_worker_thread_is_offered_on_main_thread(gui_plugin):
    bar = gui_plugin.iface.messageBar()

    worker = threading.Thread(target=diagnostics.notifier.error_recorded.emit)
    worker.start()
    worker.join()
    assert bar.items() == []

    QCoreApplication.processEvents()
    assert len(bar.items()) == 1


@pytest.mark.usefixtures("isolate_settings")
def test_previous_session_crash_is_offered_at_startup(qgis_iface, monkeypatch):
    _patch_iface(qgis_iface, monkeypatch)
    monkeypatch.setattr(diagnostics, "previous_session_problem", lambda: "QGIS closed unexpectedly last time")
    plugin = Plugin(qgis_iface)
    plugin.initGui()
    try:
        items = qgis_iface.messageBar().items()
        assert len(items) == 1
        assert "closed unexpectedly" in items[0].text()
    finally:
        plugin.unload()
        _remove_providers()
