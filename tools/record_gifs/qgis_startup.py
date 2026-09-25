"""
Runs inside QGIS (via `qgis --code`) to build the demo scene for the GIF recorder.

Generates in-memory demo layers, loads the plugin, and writes the screen positions of
features and toolbar buttons to `$OUT_DIR/points.json` so `driver.py` can move a real
X cursor to them. While recording, it keeps `$OUT_DIR/ui.json` up to date with the
positions of transient UI (menus, the Processing toolbox and algorithm widget, dialogs,
combo-box popups, layer-tree checkboxes) and clears the message bar on request.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path
import random
import traceback
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QModelIndex, QPoint, QTimer  # type: ignore[import-not-found]
from qgis.PyQt.QtGui import QColor  # type: ignore[import-not-found]
from qgis.PyQt.QtWidgets import (  # type: ignore[import-not-found]
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QMenu,
    QToolBar,
    QTreeView,
    QWidget,
)
import qgis.utils  # type: ignore[import-not-found]

from PolygonsParallelToLine.src.const import OUTPUT_LAYER_NAME

if TYPE_CHECKING:
    from types import TracebackType

OUT_DIR = Path(os.environ["OUT_DIR"])
SCENARIO = os.environ["SCENARIO"]
PROCESSING_SCENARIOS = {"open", "default_usage", "distance", "angle", "by_longest"}
ALGORITHM_WINDOW_SIZE = (820, 640)
EXTENT = QgsRectangle(-30, -20, 430, 230)
ROAD = [QgsPointXY(-20, 110), QgsPointXY(110, 90), QgsPointXY(230, 125), QgsPointXY(420, 150)]
# Map-space corners of the drag rectangles used by the drag_rectangle scenario.
DRAG_ANCHORS = {
    "north_box_a": (-5, 150),
    "north_box_b": (205, 215),
    "north_box_c": (215, 150),
    "north_box_d": (410, 215),
    "south_box_a": (-5, 90),
    "south_box_b": (410, -5),
}
# Per-theme QGIS UI theme, canvas background, and layer colors (kept legible on the canvas).
THEMES = {
    "light": {
        "ui": "default",
        "canvas": "#ffffff",
        "building": "#e8c9a0",
        "building_outline": "#8a5a2b",
        "fence": "#6b3d8f",
        "road": "#555555",
        "output": "#e4572e",
    },
    "dark": {
        "ui": "Night Mapping",
        "canvas": "#232629",
        "building": "#c9a57a",
        "building_outline": "#f0dcc0",
        "fence": "#c39bf0",
        "road": "#9aa0a6",
        "output": "#ff8c42",
    },
}
THEME = THEMES[os.environ["THEME"]]
VISIBLE_TOOLBARS = {"mFileToolBar", "mMapNavToolBar", "mDigitizeToolBar", "PolygonsParallelToLineToolBar"}

iface = qgis.utils.iface
_poll_timer = QTimer()
_last_snapshot = [""]


def rotated_rect(cx: float, cy: float, width: float, height: float, degrees: float) -> QgsGeometry:
    center = QgsPointXY(cx, cy)
    geom = QgsGeometry.fromRect(QgsRectangle.fromCenterAndSize(center, width, height))
    geom.rotate(degrees, center)
    return geom


def add_layer(kind: str, name: str, geoms: list[QgsGeometry], symbol: QgsFillSymbol | QgsLineSymbol) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", name, "memory")
    features = []
    for geom in geoms:
        feature = QgsFeature()
        feature.setGeometry(geom)
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.renderer().setSymbol(symbol)
    QgsProject.instance().addMapLayer(layer)
    return layer


def building_fill(color: str) -> QgsFillSymbol:
    return QgsFillSymbol.createSimple(
        {"color": color, "outline_color": THEME["building_outline"], "outline_width": "0.5"}
    )


def road_points(points: dict[str, tuple[float, float]]) -> QgsGeometry:
    for i, (a, b) in enumerate(itertools.pairwise(ROAD)):
        points[f"road{i}"] = ((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
    return QgsGeometry.fromPolylineXY(ROAD)


def add_road(road: QgsGeometry) -> None:
    add_layer("LineString", "Road", [road], QgsLineSymbol.createSimple({"color": THEME["road"], "width": "2.2"}))


def offset_from_road(road: QgsGeometry, x: float, offset: float) -> float:
    probe_y = 0 if offset > 0 else 300
    return road.nearestPoint(QgsGeometry.fromPointXY(QgsPointXY(x, probe_y))).asPoint().y() + offset


def build_interactive_scene() -> dict[str, tuple[float, float]]:
    """Add demo layers for the map-tool scenarios; return named map-space points the driver can click."""
    rng = random.Random(7)  # noqa: S311
    points: dict[str, tuple[float, float]] = {}
    road = road_points(points)

    buildings = []
    for side, xs, offset, size in (
        ("north", (20, 70, 125, 180, 250, 310, 370), 45, (30, 18)),
        ("south", (30, 90, 160, 225, 300, 365), -45, (26, 16)),
    ):
        for i, x in enumerate(xs):
            cy = offset_from_road(road, x, offset) + rng.uniform(-5, 5)
            buildings.append(rotated_rect(x, cy, *size, rng.uniform(-35, 35)))
            points[f"{side}{i}"] = (x, cy)

    fences = []
    for i, (x, y) in enumerate(((60, 20), (200, 40), (330, 60))):
        a = math.radians(rng.uniform(-40, 40))
        dx, dy = 25 * math.cos(a), 25 * math.sin(a)
        fences.append(QgsGeometry.fromPolylineXY([QgsPointXY(x - dx, y - dy), QgsPointXY(x + dx, y + dy)]))
        points[f"fence{i}"] = (x, y)
    points.update(DRAG_ANCHORS)

    add_layer("Polygon", "Buildings", buildings, building_fill(THEME["building"])).startEditing()
    fence_line = QgsLineSymbol.createSimple({"color": THEME["fence"], "width": "0.9"})
    add_layer("LineString", "Fences", fences, fence_line).startEditing()
    add_road(road)
    return points


def build_processing_scene() -> dict[str, tuple[float, float]]:
    """
    Add demo layers for the Processing scenarios.

    Distances from the road (20..90) and rotations (up to +-80 deg) vary widely so the
    max-distance, max-angle and longest-segment parameters each visibly change the output.
    """
    rng = random.Random(11)  # noqa: S311
    points: dict[str, tuple[float, float]] = {}
    road = road_points(points)
    buildings = []
    for sign in (1, -1):
        for x in range(10, 400, 42):
            cy = offset_from_road(road, x, sign * rng.uniform(22, 90))
            buildings.append(rotated_rect(x, cy, 28, 13, rng.uniform(-80, 80)))
    add_layer("Polygon", "Buildings", buildings, building_fill(THEME["building"]))
    add_road(road)
    return points


def style_output_layer(layer: QgsVectorLayer) -> None:
    if layer.name() == OUTPUT_LAYER_NAME:
        layer.renderer().setSymbol(building_fill(THEME["output"]))
        layer.triggerRepaint()


def tidy_window() -> None:
    QgsApplication.setUITheme(THEME["ui"])
    canvas_color = QColor(THEME["canvas"])
    QgsProject.instance().setBackgroundColor(canvas_color)
    iface.mapCanvas().setCanvasColor(canvas_color)
    window = iface.mainWindow()
    for dock in window.findChildren(QDockWidget):
        if dock.objectName() != "Layers":
            dock.hide()
    for toolbar in window.findChildren(QToolBar):
        toolbar.setVisible(toolbar.objectName() in VISIBLE_TOOLBARS)
    window.showMaximized()


def export_positions(plugin: Any, points: dict[str, tuple[float, float]]) -> None:  # noqa: ANN401
    iface.messageBar().clearWidgets()  # the bar would otherwise shift the canvas mid-recording
    canvas = iface.mapCanvas()
    canvas.setExtent(EXTENT)
    canvas.refresh()
    to_pixel = canvas.getCoordinateTransform()
    out: dict[str, list[int]] = {}
    for name, (x, y) in points.items():
        pixel = to_pixel.transform(x, y)
        screen = canvas.mapToGlobal(QPoint(int(pixel.x()), int(pixel.y())))
        out[name] = [screen.x(), screen.y()]
    for name, action in (
        ("btn_tool", plugin.parallelize_action),
        ("btn_pick_ref", plugin.pick_reference_action),
        ("btn_pick_target", plugin.pick_target_action),
        ("btn_settings", plugin.settings_action),
    ):
        out[name] = click_point(plugin.toolbar.widgetForAction(action))
    (OUT_DIR / "points.json").write_text(json.dumps(out))
    (OUT_DIR / "ready").touch()


def clean(text: str) -> str:
    return text.replace("&", "").replace("\u2026", "").strip()


def at(widget: QWidget, point: QPoint) -> list[int]:
    screen = widget.mapToGlobal(point)
    return [screen.x(), screen.y()]


def click_point(widget: QWidget) -> list[int]:
    """Where to click a widget: a checkbox's box sits at its left edge, anything else at its center."""
    if isinstance(widget, QCheckBox):
        return at(widget, QPoint(10, widget.height() // 2))
    return at(widget, widget.rect().center())


def item_view_positions(view: QAbstractItemView, prefix: str, parent: QModelIndex, out: dict[str, list[int]]) -> None:
    """Record visible rows (recursing into expanded tree nodes); tree leaves get an extra `leaf:` prefix."""
    model = view.model()
    is_tree = isinstance(view, QTreeView)
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        rect = view.visualRect(index)
        has_children = model.rowCount(index) > 0
        if not rect.isEmpty():
            leaf = "leaf:" if is_tree and not has_children else ""
            out.setdefault(f"{prefix}{leaf}{clean(str(index.data()))}", at(view.viewport(), rect.center()))
        if is_tree and has_children and view.isExpanded(index):
            item_view_positions(view, prefix, index, out)


def input_widget(field: QWidget) -> QWidget:
    """The actual input inside a composite parameter widget (layer pickers and numbers sit next to extra buttons)."""
    for kind in (QAbstractSpinBox, QComboBox):
        if isinstance(field, kind):
            return field
        child = field.findChild(kind)
        if child is not None:
            return child
    return field


def algorithm_widget_positions(widget: QWidget, out: dict[str, list[int]]) -> None:
    for name, wrapper in getattr(widget.mainWidget(), "wrappers", {}).items():
        field = wrapper.wrappedWidget()
        if field is None or not field.isVisible():
            continue
        out[f"param:{name}"] = click_point(input_widget(field))


def menu_positions(out: dict[str, list[int]]) -> None:
    menu_bar = iface.mainWindow().menuBar()
    for action in menu_bar.actions():
        out[f"menubar:{clean(action.text())}"] = at(menu_bar, menu_bar.actionGeometry(action).center())
    popup = QApplication.activePopupWidget()
    if isinstance(popup, QMenu):
        for action in popup.actions():
            if action.text():
                out[f"menu:{clean(action.text())}"] = at(popup, popup.actionGeometry(action).center())
    elif popup is not None:  # e.g. a layer combo box's drop-down list
        for view in popup.findChildren(QAbstractItemView):
            item_view_positions(view, "popup:", QModelIndex(), out)


def toolbox_positions(out: dict[str, list[int]]) -> None:
    for dock in iface.mainWindow().findChildren(QDockWidget):
        if dock.isVisible() and hasattr(dock, "algorithmTree"):  # the Processing toolbox
            out["toolbox:search"] = click_point(dock.searchBox)
            item_view_positions(dock.algorithmTree, "toolbox:", QModelIndex(), out)


def visible_algorithm_widgets() -> list[QWidget]:
    return [w for w in QApplication.allWidgets() if w.isVisible() and hasattr(w, "runButton")]


def dialog_positions(algorithm_widgets: list[QWidget], out: dict[str, list[int]]) -> None:
    """Parameters of the Processing algorithm widget, plus buttons/checkboxes of it and any modal dialog."""
    for widget in algorithm_widgets:
        algorithm_widget_positions(widget, out)
    modal = QApplication.activeModalWidget()
    for dialog in [*algorithm_widgets, *([modal] if modal else [])]:
        for button in dialog.findChildren(QAbstractButton):
            if button.isVisible() and button.text():
                kind = "checkbox" if isinstance(button, QCheckBox) else "button"
                out.setdefault(f"{kind}:{clean(button.text())}", click_point(button))


def layer_tree_positions(out: dict[str, list[int]]) -> None:
    tree_view = iface.layerTreeView()
    for node in QgsProject.instance().layerTreeRoot().findLayers():
        rect = tree_view.visualRect(tree_view.node2index(node))
        if not rect.isEmpty():  # the visibility checkbox sits at the start of the row
            out[f"layer:{node.name()}"] = at(tree_view.viewport(), QPoint(rect.left() + 10, rect.center().y()))


def ui_positions(algorithm_widgets: list[QWidget]) -> dict[str, list[int]]:
    """Screen positions of the transient UI the driver may need, keyed by stable names."""
    out: dict[str, list[int]] = {}
    menu_positions(out)
    toolbox_positions(out)
    dialog_positions(algorithm_widgets, out)
    layer_tree_positions(out)
    return out


def enlarge_algorithm_windows(algorithm_widgets: list[QWidget]) -> None:
    """Grow each newly opened algorithm window once: its default size hides the numeric parameters."""
    for widget in algorithm_widgets:
        window = widget.window()
        if not window.property("pptl_enlarged"):
            window.resize(*ALGORITHM_WINDOW_SIZE)
            window.setProperty("pptl_enlarged", True)  # noqa: FBT003


def poll_driver_requests() -> None:
    # QTimers keep firing inside modal dialogs' nested event loops, so this also runs while one is open.
    clear_request = OUT_DIR / "clear_messages"
    if clear_request.exists():
        clear_request.unlink()
        iface.messageBar().clearWidgets()
    # One widget-tree walk per poll: it is the expensive part and runs every 300 ms while recording.
    algorithm_widgets = visible_algorithm_widgets()
    enlarge_algorithm_windows(algorithm_widgets)
    snapshot = json.dumps(ui_positions(algorithm_widgets))
    if snapshot != _last_snapshot[0]:
        tmp = OUT_DIR / "ui.json.tmp"
        tmp.write_text(snapshot)
        tmp.replace(OUT_DIR / "ui.json")
        _last_snapshot[0] = snapshot


def log_plugin_errors() -> None:
    """Copy exceptions QGIS would only show in its message log to a file record.sh checks."""
    original = qgis.utils.showException

    def show_exception(
        exc_type: type[BaseException], value: BaseException, tb: TracebackType, *args: object, **kwargs: object
    ) -> object:
        with (OUT_DIR / "python_errors.txt").open("a") as fh:
            fh.write("".join(traceback.format_exception(exc_type, value, tb)))
        return original(exc_type, value, tb, *args, **kwargs)

    qgis.utils.showException = show_exception


def main() -> None:
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    log_plugin_errors()
    points = build_processing_scene() if SCENARIO in PROCESSING_SCENARIOS else build_interactive_scene()
    QgsProject.instance().layerWasAdded.connect(style_output_layer)  # type: ignore[attr-defined]
    qgis.utils.loadPlugin("PolygonsParallelToLine")
    qgis.utils.startPlugin("PolygonsParallelToLine")
    plugin = qgis.utils.plugins["PolygonsParallelToLine"]
    tidy_window()

    # Give the maximized window time to settle before reading widget positions.
    QTimer.singleShot(4000, lambda: export_positions(plugin, points))
    _poll_timer.timeout.connect(poll_driver_requests)
    _poll_timer.start(300)


try:
    main()
except Exception:  # noqa: BLE001
    (OUT_DIR / "startup_error.txt").write_text(traceback.format_exc())
