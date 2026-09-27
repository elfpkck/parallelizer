from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolIdentify, QgsMapToolIdentifyFeature, QgsRubberBand
from qgis.PyQt.QtCore import QPoint, Qt  # type: ignore[import-not-found]
from qgis.PyQt.QtGui import QColor  # type: ignore[import-not-found]

from .const import LINE_GEOMETRY, POLYGON_GEOMETRY, SUPPORTED_GEOMETRIES
from .diagnostics import MAX_TRACED_FEATURES, capture_errors, current_operation, logger, operation
from .parallelizer import compute_parallel_geometry
from .reference import ReferenceFeature, Segment, iter_segments
from .snapshot import add_reference_geometry, describe_crs, describe_geometry, describe_layer, describe_project

if TYPE_CHECKING:
    from qgis.gui import QgisInterface, QgsMapMouseEvent
    from qgis.PyQt.QtGui import QKeyEvent  # type: ignore[import-not-found]

    from .diagnostics import Operation
    from .settings import MapToolSettings


def _closest_segment_of(geom: QgsGeometry, point_xy: QgsPointXY) -> Segment:
    return ReferenceFeature.from_geometry(geom).get_closest_segment(point_xy)


def _pick_segment_in_rect(geom: QgsGeometry, rect_geom: QgsGeometry, rect_center: QgsPointXY) -> Segment:
    best: Segment | None = None
    best_length = 0.0
    for seg in iter_segments(geom):
        seg_geom = QgsGeometry.fromPolyline([seg.start, seg.end])
        clipped = seg_geom.intersection(rect_geom)
        if clipped.isNull() or clipped.isEmpty():
            continue
        length = clipped.length()
        if length > best_length or best is None:
            best_length = length
            best = seg
    if best is not None:
        return best
    return _closest_segment_of(geom, rect_center)


Kind = Literal["line", "polygon"]


class ParallelToLineMapTool(QgsMapToolIdentifyFeature):
    REFERENCE_COLOR = QColor(255, 140, 0, 200)
    REFERENCE_FILL = QColor(255, 140, 0, 60)
    REFERENCE_WIDTH = 3
    SELECTION_STROKE = QColor(0, 128, 255, 220)
    SELECTION_FILL = QColor(0, 128, 255, 50)
    DRAG_THRESHOLD_PX = 5
    REFERENCE_CLEARED_MSG = "Reference cleared. Click a line or polygon feature to set a new reference."

    def __init__(self, iface: QgisInterface, settings: MapToolSettings) -> None:
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.settings = settings
        self.reference_geom: QgsGeometry | None = None
        self.reference_rubber_band: QgsRubberBand | None = None
        self._selection_rubber_band: QgsRubberBand | None = None
        self._drag_start_pos: QPoint | None = None
        self._drag_start_point: QgsPointXY | None = None
        self._is_dragging: bool = False
        self._reference_sections: list[tuple[str, list[str]]] = []

    @capture_errors
    def activate(self) -> None:
        super().activate()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._show_message("Click or drag-rectangle on a line or polygon to set the reference.", Qgis.MessageLevel.Info)

    @capture_errors
    def deactivate(self) -> None:
        self._clear_reference()
        self._cancel_drag()
        super().deactivate()

    @capture_errors
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._is_dragging:
                self._cancel_drag()
                return
            if self.reference_geom is not None:
                self._clear_reference()
                self._show_message(self.REFERENCE_CLEARED_MSG, Qgis.MessageLevel.Info)
            else:
                self.iface.mapCanvas().unsetMapTool(self)
            return
        super().keyPressEvent(event)

    @capture_errors
    def canvasPressEvent(self, event: QgsMapMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_start_pos = event.originalPixelPoint()
        self._drag_start_point = event.originalMapPoint()

    @capture_errors
    def canvasMoveEvent(self, event: QgsMapMouseEvent) -> None:
        if self._drag_start_pos is None:
            return
        if not self._is_dragging:
            delta = event.originalPixelPoint() - self._drag_start_pos
            if abs(delta.x()) < self.DRAG_THRESHOLD_PX and abs(delta.y()) < self.DRAG_THRESHOLD_PX:
                return
            self._is_dragging = True
            self._start_selection_band()
        self._update_selection_band(event.originalMapPoint())

    @capture_errors
    def canvasReleaseEvent(self, event: QgsMapMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel_drag()
            self._clear_reference()
            self._show_message(self.REFERENCE_CLEARED_MSG, Qgis.MessageLevel.Info)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        was_dragging = self._is_dragging
        drag_start = self._drag_start_point
        self._cancel_drag()

        with operation("map tool: click on nothing to use", idle=True, watchdog=True):
            if was_dragging and drag_start is not None:
                release_point = event.originalMapPoint()
                rect = QgsRectangle(drag_start, release_point)
                if self.reference_geom is None:
                    self._set_reference_from_rect(rect)
                else:
                    self._rotate_features_in_rect(rect)
                return

            self._handle_single_click(event)

    def _handle_single_click(self, event: QgsMapMouseEvent) -> None:
        # QMouseEvent.x()/y() were removed in Qt 6 and pos() is deprecated there; originalPixelPoint() works on
        # both and, like originalMapPoint(), ignores any snapping.
        pos = event.originalPixelPoint()
        results = self.identify(
            pos.x(),
            pos.y(),
            QgsMapToolIdentify.IdentifyMode.TopDownAll,
            QgsMapToolIdentify.Type.VectorLayer,
        )
        if not results:
            return

        map_point = event.originalMapPoint()

        if self.reference_geom is None:
            # Prefer a line feature under the click; fall back to a polygon only if
            # no line was hit, so the existing line-reference UX never regresses.
            for preferred in SUPPORTED_GEOMETRIES:
                for result in results:
                    layer = result.mLayer
                    if not isinstance(layer, QgsVectorLayer):
                        continue
                    if layer.geometryType() != preferred:
                        continue
                    self._set_reference_from_feature(layer, result.mFeature, map_point)
                    return
            return

        for result in results:
            layer = result.mLayer
            if not isinstance(layer, QgsVectorLayer):
                continue
            geom_type = layer.geometryType()
            if geom_type not in SUPPORTED_GEOMETRIES:
                continue
            self._rotate_target(layer, result.mFeature, geom_type, map_point)
            return

    def _set_reference_from_feature(self, layer: QgsVectorLayer, feature: QgsFeature, map_point: QgsPointXY) -> None:
        ref_geom = feature.geometry()
        click_layer = self._point_in_layer_crs(map_point, layer)
        if self.settings.pick_reference_segment:
            segment = _closest_segment_of(ref_geom, click_layer)
            ref_geom = QgsGeometry.fromPolylineXY([QgsPointXY(segment.start), QgsPointXY(segment.end)])
        self._set_reference(ref_geom, layer, via="click", marker=QgsGeometry.fromPointXY(click_layer))
        self._show_message(
            "Reference set. Click or drag-rectangle to rotate line/polygon features.",
            Qgis.MessageLevel.Success,
        )

    def _rotate_target(
        self,
        layer: QgsVectorLayer,
        feature: QgsFeature,
        geom_type: QgsWkbTypes.GeometryType,
        map_point: QgsPointXY,
    ) -> None:
        op = self._begin("map tool: rotate (click)", with_reference=True)
        op.add("Target layer", describe_layer(layer))
        if not layer.isEditable():
            logger.info("Click rotation skipped: target layer not editable")
            self._show_message(
                f"Layer '{layer.name()}' is not in edit mode; toggle editing to rotate features.",
                Qgis.MessageLevel.Warning,
            )
            return

        kind: Kind = "line" if geom_type == LINE_GEOMETRY else "polygon"
        click_layer = self._point_in_layer_crs(map_point, layer)
        target_segment: Segment | None = None
        if self.settings.pick_target_segment:
            target_segment = _closest_segment_of(feature.geometry(), click_layer)
        reference = self._reference_for_layer(layer)
        trace = self._trace_feature(op, reference, feature, layer.crs())
        op.add_geometry("click", QgsGeometry.fromPointXY(click_layer), layer.crs())
        rotated = compute_parallel_geometry(
            reference,
            feature.geometry(),
            kind,
            by_longest=self.settings.by_longest,
            target_segment=target_segment,
            trace=trace,
        )
        if rotated is None:
            logger.info("Click rotation: %s already parallel, pick_target_segment=%s", kind, target_segment is not None)
            self._show_message("Feature already parallel; no rotation applied.", Qgis.MessageLevel.Info)
            return

        layer.beginEditCommand("Parallel to Line")
        ok = layer.changeGeometry(feature.id(), rotated)
        if ok:
            layer.endEditCommand()
            layer.triggerRepaint()
            logger.info("Click rotation: %s rotated, pick_target_segment=%s", kind, target_segment is not None)
        else:
            layer.destroyEditCommand()
            logger.warning("Click rotation: changeGeometry failed for a %s (provider %s)", kind, layer.providerType())
            self._show_message("Failed to update feature geometry.", Qgis.MessageLevel.Warning)

    def _set_reference_from_rect(self, map_rect: QgsRectangle) -> None:
        if map_rect.isEmpty():
            return

        canvas = self.iface.mapCanvas()
        # Walk line layers first; only consider polygon layers if no line was found,
        # so a polygon under a line does not steal the reference.
        found: list[tuple[QgsVectorLayer, QgsFeature, QgsRectangle, QgsGeometry]] = []
        for preferred in SUPPORTED_GEOMETRIES:
            for canvas_layer in canvas.layers():
                if not isinstance(canvas_layer, QgsVectorLayer):
                    continue
                if canvas_layer.geometryType() != preferred:
                    continue
                layer_rect = self._transform_rect_to_layer(map_rect, canvas_layer)
                rect_geom = QgsGeometry.fromRect(layer_rect)
                request = QgsFeatureRequest().setFilterRect(layer_rect)
                found.extend(
                    (canvas_layer, feature, layer_rect, rect_geom)
                    for feature in canvas_layer.getFeatures(request)
                    if feature.geometry().intersects(rect_geom)
                )
            if found:
                break

        if not found:
            self._show_message("No line or polygon feature in the selection.", Qgis.MessageLevel.Info)
            return

        layer, feature, layer_rect, rect_geom = found[0]
        ref_geom = feature.geometry()
        if self.settings.pick_reference_segment:
            segment = _pick_segment_in_rect(ref_geom, rect_geom, layer_rect.center())
            ref_geom = QgsGeometry.fromPolylineXY([QgsPointXY(segment.start), QgsPointXY(segment.end)])
        self._set_reference(ref_geom, layer, via="rectangle", marker=rect_geom)
        suffix = f" ({len(found)} features in selection; using topmost)" if len(found) > 1 else ""
        self._show_message(
            f"Reference set{suffix}. Click or drag-rectangle to rotate line/polygon features.",
            Qgis.MessageLevel.Success,
        )

    def _rotate_features_in_rect(self, map_rect: QgsRectangle) -> None:
        if map_rect.isEmpty() or self.reference_geom is None:
            return

        canvas = self.iface.mapCanvas()
        rotated_count = 0
        non_editable: list[str] = []
        op = self._begin("map tool: rotate (rectangle)", with_reference=True)

        for canvas_layer in canvas.layers():
            if not isinstance(canvas_layer, QgsVectorLayer):
                continue
            geom_type = canvas_layer.geometryType()
            if geom_type not in SUPPORTED_GEOMETRIES:
                continue

            layer_rect = self._transform_rect_to_layer(map_rect, canvas_layer)
            rect_geom = QgsGeometry.fromRect(layer_rect)
            request = QgsFeatureRequest().setFilterRect(layer_rect)
            features = [f for f in canvas_layer.getFeatures(request) if f.geometry().intersects(rect_geom)]
            if not features:
                continue

            op.add(f"Target layer ({len(features)} feature(s) in the rectangle)", describe_layer(canvas_layer))
            if not canvas_layer.isEditable():
                non_editable.append(canvas_layer.name())
                continue

            kind: Kind = "line" if geom_type == LINE_GEOMETRY else "polygon"
            layer_rotated = self._rotate_layer_features(canvas_layer, features, kind, layer_rect=layer_rect)
            rotated_count += layer_rotated

        logger.info(
            "Rectangle rotation: %d feature(s) rotated, %d non-editable layer(s) skipped",
            rotated_count,
            len(non_editable),
        )
        self._report_bulk_result(rotated_count, non_editable)

    def _rotate_layer_features(
        self,
        layer: QgsVectorLayer,
        features: list[QgsFeature],
        kind: Kind,
        *,
        layer_rect: QgsRectangle | None = None,
    ) -> int:
        reference = self._reference_for_layer(layer)
        op = current_operation()
        pick_target = self.settings.pick_target_segment and layer_rect is not None
        rect_geom = QgsGeometry.fromRect(layer_rect) if layer_rect is not None else None
        rect_center = layer_rect.center() if layer_rect is not None else None
        if rect_geom is not None and not any(role == "rectangle" for role, _ in op.geometries):
            op.add_geometry("rectangle", rect_geom, layer.crs())

        layer.beginEditCommand("Parallel to Line (bulk)")
        layer_rotated = 0
        try:
            for feature in features:
                feature_geom = feature.geometry()
                target_segment: Segment | None = None
                if pick_target and rect_geom is not None and rect_center is not None:
                    target_segment = _pick_segment_in_rect(feature_geom, rect_geom, rect_center)
                rotated = compute_parallel_geometry(
                    reference,
                    feature_geom,
                    kind,
                    by_longest=self.settings.by_longest,
                    target_segment=target_segment,
                    trace=self._trace_feature(op, reference, feature, layer.crs()),
                )
                if rotated is None:
                    continue
                if layer.changeGeometry(feature.id(), rotated):
                    layer_rotated += 1
        except Exception:
            layer.destroyEditCommand()
            raise

        if layer_rotated:
            layer.endEditCommand()
            layer.triggerRepaint()
        else:
            layer.destroyEditCommand()
        return layer_rotated

    def _report_bulk_result(self, rotated_count: int, non_editable: list[str]) -> None:
        if rotated_count > 0 and non_editable:
            joined = ", ".join(non_editable)
            self._show_message(
                f"Rotated {rotated_count} feature(s). Skipped non-editable layer(s): {joined}.",
                Qgis.MessageLevel.Success,
            )
        elif rotated_count > 0:
            self._show_message(f"Rotated {rotated_count} feature(s).", Qgis.MessageLevel.Success)
        elif non_editable:
            joined = ", ".join(non_editable)
            self._show_message(
                f"No features rotated. Non-editable layer(s) in selection: {joined}.",
                Qgis.MessageLevel.Warning,
            )
        else:
            self._show_message("No features to rotate in the selection.", Qgis.MessageLevel.Info)

    def _point_in_layer_crs(self, map_point: QgsPointXY, layer: QgsVectorLayer) -> QgsPointXY:
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        layer_crs = layer.crs()
        if not (map_crs.isValid() and layer_crs.isValid()) or map_crs == layer_crs:
            return map_point
        transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        return transform.transform(map_point)

    def _transform_rect_to_layer(self, map_rect: QgsRectangle, layer: QgsVectorLayer) -> QgsRectangle:
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        layer_crs = layer.crs()
        if map_crs == layer_crs:
            return map_rect
        transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        return transform.transformBoundingBox(map_rect)

    def _begin(self, name: str, *, with_reference: bool = False) -> Operation:
        """Name the running operation and snapshot the project, plus the reference set by an earlier click."""
        op = current_operation()
        op.describe_as(name)
        op.add("Project and canvas", describe_project(self.iface.mapCanvas()))
        if with_reference:
            for title, lines in self._reference_sections:
                op.add(f"{title} (set earlier)", lines)
        return op

    @staticmethod
    def _trace_feature(
        op: Operation, reference: QgsGeometry, feature: QgsFeature, crs: QgsCoordinateReferenceSystem
    ) -> dict[str, Any] | None:
        """Snapshot one target (up to MAX_TRACED_FEATURES per operation) and return its rotation trace."""
        if op.traced_features >= MAX_TRACED_FEATURES:
            return None
        op.traced_features += 1
        label = f"target {op.traced_features}"
        geom = feature.geometry()
        distance = reference.distance(geom)
        op.add(
            f"Target feature {op.traced_features}",
            [f"id: {feature.id()}", f"distance to reference: {distance:.6g}", *describe_geometry(geom)],
        )
        add_reference_geometry(op, reference, geom, label, distance, crs)
        op.add_geometry(label, geom, crs)
        return op.add_trace(f"Rotation math for target feature {op.traced_features}")

    def _set_reference(self, geom: QgsGeometry, layer: QgsVectorLayer, *, via: str, marker: QgsGeometry) -> None:
        source_crs = layer.crs()
        self._reference_sections = [
            ("Reference layer", describe_layer(layer)),
            ("Reference geometry", describe_geometry(geom)),
        ]
        op = self._begin(f"map tool: set reference ({via})")
        for title, lines in self._reference_sections:
            op.add(title, lines)
        add_reference_geometry(op, geom, marker, via, geom.distance(marker), source_crs)
        op.add_geometry(via, marker, source_crs)
        self._clear_reference()
        reference = QgsGeometry(geom)
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if source_crs.isValid() and map_crs.isValid() and source_crs != map_crs:
            transform = QgsCoordinateTransform(source_crs, map_crs, QgsProject.instance())
            reference.transform(transform)
        self.reference_geom = reference
        wkb = QgsWkbTypes.geometryType(reference.wkbType())
        rubber_band = QgsRubberBand(self.iface.mapCanvas(), wkb)
        rubber_band.setColor(self.REFERENCE_COLOR)
        rubber_band.setWidth(self.REFERENCE_WIDTH)
        if wkb == POLYGON_GEOMETRY:
            rubber_band.setFillColor(self.REFERENCE_FILL)
        rubber_band.setToGeometry(self.reference_geom)
        self.reference_rubber_band = rubber_band
        logger.info(
            "Reference set by %s: %s with %d vertices, pick_reference_segment=%s, layer CRS %s, canvas CRS %s",
            via,
            QgsWkbTypes.displayString(reference.wkbType()),
            0 if reference.isNull() else reference.constGet().nCoordinates(),
            self.settings.pick_reference_segment,
            describe_crs(source_crs),
            describe_crs(map_crs),
        )

    def _reference_for_layer(self, layer: QgsVectorLayer) -> QgsGeometry:
        if self.reference_geom is None:
            msg = "reference must be set before rotation"
            raise RuntimeError(msg)
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        layer_crs = layer.crs()
        if not (map_crs.isValid() and layer_crs.isValid()) or map_crs == layer_crs:
            return self.reference_geom
        transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        result = QgsGeometry(self.reference_geom)
        result.transform(transform)
        return result

    def _clear_reference(self) -> None:
        self.reference_geom = None
        if self.reference_rubber_band is not None:
            self.iface.mapCanvas().scene().removeItem(self.reference_rubber_band)
            self.reference_rubber_band = None

    def _start_selection_band(self) -> None:
        canvas = self.iface.mapCanvas()
        band = QgsRubberBand(canvas, POLYGON_GEOMETRY)
        band.setColor(self.SELECTION_STROKE)
        band.setFillColor(self.SELECTION_FILL)
        band.setWidth(1)
        self._selection_rubber_band = band

    def _update_selection_band(self, current_point: QgsPointXY) -> None:
        if self._selection_rubber_band is None or self._drag_start_point is None:
            return
        rect = QgsRectangle(self._drag_start_point, current_point)
        self._selection_rubber_band.setToGeometry(QgsGeometry.fromRect(rect))

    def _clear_selection_band(self) -> None:
        if self._selection_rubber_band is None:
            return
        self.iface.mapCanvas().scene().removeItem(self._selection_rubber_band)
        self._selection_rubber_band = None

    def _cancel_drag(self) -> None:
        self._clear_selection_band()
        self._drag_start_pos = None
        self._drag_start_point = None
        self._is_dragging = False

    def _show_message(self, text: str, level: Qgis.MessageLevel) -> None:
        self.iface.messageBar().pushMessage("Parallel to Line", text, level=level, duration=10)
