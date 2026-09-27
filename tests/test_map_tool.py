from __future__ import annotations

from typing import NoReturn

import pytest
from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.gui import QgsMapMouseEvent
from qgis.PyQt.QtCore import QEvent, QPoint, Qt  # type: ignore[import-not-found]

from PolygonsParallelToLine.src import diagnostics
from PolygonsParallelToLine.src.map_tool import ParallelToLineMapTool, _pick_segment_in_rect
from PolygonsParallelToLine.src.settings import MapToolSettings


def test_pick_segment_in_rect_returns_segment_with_largest_overlap(qgis_app):
    geom = QgsGeometry.fromWkt("LineString (0 10, 20 10, 80 10, 100 10)")
    rect = QgsRectangle(30, 0, 70, 20)
    rect_geom = QgsGeometry.fromRect(rect)

    seg = _pick_segment_in_rect(geom, rect_geom, rect.center())

    # Middle segment (20,10)→(80,10) has the longest clip inside the rect.
    assert (seg.start.x(), seg.start.y()) == (20.0, 10.0)
    assert (seg.end.x(), seg.end.y()) == (80.0, 10.0)


def test_pick_segment_in_rect_prefers_full_overlap_over_partial(qgis_app):
    geom = QgsGeometry.fromWkt("LineString (0 10, 50 10, 100 10)")
    rect = QgsRectangle(0, 0, 90, 20)
    rect_geom = QgsGeometry.fromRect(rect)

    seg = _pick_segment_in_rect(geom, rect_geom, rect.center())

    # First segment is fully inside (length 50) — beats the partial second segment (length 40).
    assert (seg.start.x(), seg.end.x()) == (0.0, 50.0)


def test_pick_segment_in_rect_falls_back_to_closest_when_rect_inside_polygon(qgis_app):
    geom = QgsGeometry.fromWkt("Polygon ((0 0, 100 0, 100 100, 0 100, 0 0))")
    rect = QgsRectangle(40, 40, 60, 60)
    rect_geom = QgsGeometry.fromRect(rect)

    seg = _pick_segment_in_rect(geom, rect_geom, rect.center())

    edges = {
        ((0.0, 0.0), (100.0, 0.0)),
        ((100.0, 0.0), (100.0, 100.0)),
        ((100.0, 100.0), (0.0, 100.0)),
        ((0.0, 100.0), (0.0, 0.0)),
    }
    got = ((seg.start.x(), seg.start.y()), (seg.end.x(), seg.end.y()))
    assert got in edges


def test_pick_segment_in_rect_picks_polygon_edge_overlapping_rect(qgis_app):
    geom = QgsGeometry.fromWkt("Polygon ((0 0, 100 0, 100 100, 0 100, 0 0))")
    # Rect that straddles the bottom edge only.
    rect = QgsRectangle(10, -10, 90, 5)
    rect_geom = QgsGeometry.fromRect(rect)

    seg = _pick_segment_in_rect(geom, rect_geom, rect.center())

    assert ((seg.start.x(), seg.start.y()), (seg.end.x(), seg.end.y())) == ((0.0, 0.0), (100.0, 0.0))


@pytest.mark.usefixtures("isolate_settings")
def test_single_click_sets_reference(qgis_app, qgis_iface, qgis_canvas, qgis_new_project):
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "ref", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LineString (0 50, 100 50)"))
    layer.dataProvider().addFeatures([feature])
    QgsProject.instance().addMapLayer(layer)
    qgis_canvas.setDestinationCrs(layer.crs())
    qgis_canvas.setLayers([layer])
    qgis_canvas.setExtent(QgsRectangle(0, 0, 100, 100))
    qgis_canvas.refresh()

    tool = ParallelToLineMapTool(qgis_iface, MapToolSettings())
    qgis_canvas.setMapTool(tool)
    try:
        pixel = qgis_canvas.getCoordinateTransform().transform(50, 50)
        pos = QPoint(round(pixel.x()), round(pixel.y()))
        left = Qt.MouseButton.LeftButton
        tool.canvasPressEvent(QgsMapMouseEvent(qgis_canvas, QEvent.Type.MouseButtonPress, pos, left, left))
        tool.canvasReleaseEvent(QgsMapMouseEvent(qgis_canvas, QEvent.Type.MouseButtonRelease, pos, left, left))

        assert tool.reference_geom is not None
    finally:
        # The canvas is session-scoped: deactivate the tool so its rubber band doesn't outlive the test.
        qgis_canvas.unsetMapTool(tool)


def test_event_handler_error_is_recorded_and_reraised(qgis_app, qgis_iface, qgis_canvas, monkeypatch):
    tool = ParallelToLineMapTool(qgis_iface, MapToolSettings())

    def broken_click(_event) -> NoReturn:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(tool, "_handle_single_click", broken_click)
    left = Qt.MouseButton.LeftButton
    event = QgsMapMouseEvent(qgis_canvas, QEvent.Type.MouseButtonRelease, QPoint(10, 10), left, left)

    with pytest.raises(RuntimeError, match="boom"):
        tool.canvasReleaseEvent(event)

    assert "RuntimeError: boom" in (diagnostics.last_error() or "")


@pytest.mark.usefixtures("isolate_settings")
def test_click_rotation_snapshot_describes_layers_features_and_math(
    qgis_app, qgis_iface, qgis_canvas, qgis_new_project
):
    reference = QgsVectorLayer("LineString?crs=EPSG:3857", "ref", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LineString (0 50, 100 50)"))
    reference.dataProvider().addFeatures([feature])
    target = QgsVectorLayer("Polygon?crs=EPSG:3857", "private houses", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("Polygon ((70 70, 88 74, 84 92, 66 88, 70 70))"))
    target.dataProvider().addFeatures([feature])
    target.startEditing()
    QgsProject.instance().addMapLayers([reference, target])
    qgis_canvas.setDestinationCrs(reference.crs())
    # Target on top: the offscreen canvas is small, so the identify radius spans much of the map.
    qgis_canvas.setLayers([target, reference])
    qgis_canvas.setExtent(QgsRectangle(0, 0, 100, 100))
    qgis_canvas.refresh()

    tool = ParallelToLineMapTool(qgis_iface, MapToolSettings())
    qgis_canvas.setMapTool(tool)
    left = Qt.MouseButton.LeftButton

    def click(x: float, y: float) -> None:
        pixel = qgis_canvas.getCoordinateTransform().transform(x, y)
        pos = QPoint(round(pixel.x()), round(pixel.y()))
        tool.canvasPressEvent(QgsMapMouseEvent(qgis_canvas, QEvent.Type.MouseButtonPress, pos, left, left))
        tool.canvasReleaseEvent(QgsMapMouseEvent(qgis_canvas, QEvent.Type.MouseButtonRelease, pos, left, left))

    try:
        click(5, 50)
        click(77, 81)
    finally:
        qgis_canvas.unsetMapTool(tool)
        target.rollBack()

    report = diagnostics.build_report()
    assert "Recent operations (newest first)\n  #1: map tool: rotate (click) (finished" in report
    assert "  #2: map tool: set reference (click)" in report
    assert "    canvas rotation: 0" in report
    assert "  Target layer\n    geometry type: Polygon" in report
    assert "  Reference geometry (set earlier)\n    geometry: LineString" in report
    assert "    outcome: rotated" in report
    assert "private houses" not in report
    block = diagnostics.geometry_block() or ""
    assert "reference: LineString (0 0, 100 0)" in block
    # One click per operation; exact values depend on the test canvas's pixel rounding.
    assert block.count("    click: Point (") == 2
