from __future__ import annotations

import contextlib
import dataclasses
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsProcessingFeatureSource,
    QgsWkbTypes,
)

from .const import COLUMN_NAME, LINE_GEOMETRY
from .diagnostics import UserInputError, current_operation, logger
from .parallelizer import compute_parallel_geometry
from .reference import ReferenceLayer
from .snapshot import add_reference_geometry, describe_crs, describe_geometry
from .target import Target

if TYPE_CHECKING:
    from qgis.core import QgsProcessingFeedback


@dataclasses.dataclass
class Params:
    reference_layer: QgsProcessingFeatureSource
    target_layer: QgsProcessingFeatureSource
    by_longest: bool
    no_multi: bool
    distance: float
    angle: float
    fields: QgsFields
    sink: QgsFeatureSink


class ParallelToReference:
    def __init__(self, feedback: QgsProcessingFeedback, params: Params):
        self.feedback = feedback
        self.params = params
        self.total_number: int = self.params.target_layer.featureCount()

    @cached_property
    def reference_layer(self) -> ReferenceLayer:
        return ReferenceLayer(self.params.reference_layer)

    @cached_property
    def target_kind(self) -> Literal["line", "polygon"]:
        gtype = QgsWkbTypes.geometryType(self.params.target_layer.wkbType())
        return "line" if gtype == LINE_GEOMETRY else "polygon"

    def run(self) -> None:
        # pydevd_pycharm.settrace("127.0.0.1", port=53100, stdoutToServer=True, stderrToServer=True) # noqa: ERA001
        self.validate_target_layer()
        self.log_run_summary()
        self.rotate_features()

    def validate_target_layer(self) -> None:
        if not self.total_number:
            msg = "Target layer is empty"
            raise UserInputError(msg)

    def log_run_summary(self) -> None:
        reference, target = self.params.reference_layer, self.params.target_layer
        message = (
            f"Processing run: reference {QgsWkbTypes.displayString(reference.wkbType())} "
            f"({describe_crs(reference.sourceCrs())}), target {QgsWkbTypes.displayString(target.wkbType())} "
            f"({describe_crs(target.sourceCrs())}), {self.total_number} feature(s), "
            f"by_longest={self.params.by_longest}, no_multi={self.params.no_multi}, "
            f"distance={self.params.distance}, angle={self.params.angle}"
        )
        self.feedback.pushDebugInfo(message)
        logger.info(message)

    def rotate_features(self) -> None:
        total = 100.0 / self.total_number
        rotated_index = self.params.fields.indexFromName(COLUMN_NAME)
        processed_count = rotated_count = 0

        for i, feature in enumerate(self.params.target_layer.getFeatures(), start=1):
            if self.feedback.isCanceled():
                break

            try:
                processed = self.process_feature(feature)
            except Exception:
                with contextlib.suppress(Exception):
                    self.note_failed_feature(feature)
                raise
            self.params.sink.addFeature(processed, QgsFeatureSink.Flag.FastInsert)
            self.feedback.setProgress(int(i * total))
            processed_count = i
            rotated_count += bool(processed.attribute(rotated_index))

        logger.info(
            "Processing run finished: %d of %d feature(s) processed, %d rotated%s",
            processed_count,
            self.total_number,
            rotated_count,
            " (canceled)" if self.feedback.isCanceled() else "",
        )
        current_operation().add(
            "Result",
            [
                f"processed: {processed_count} of {self.total_number}, rotated: {rotated_count}",
                f"canceled: {self.feedback.isCanceled()}",
            ],
        )

    def note_failed_feature(self, feature: QgsFeature) -> None:
        """Snapshot the feature that failed; re-running it with a trace records the math up to the failure."""
        op = current_operation()
        target_geom = feature.geometry()
        op.add("Failed feature", [f"id: {feature.id()}", *describe_geometry(target_geom)])
        op.add_geometry("target", target_geom)
        reference_geom = self.reference_layer.get_closest_feature(Target(feature).center_xy).geom
        op.add("Closest reference feature", describe_geometry(reference_geom))
        add_reference_geometry(op, reference_geom, target_geom, "target", reference_geom.distance(target_geom))
        # The caller suppresses errors: a re-run that fails again still leaves the trace filled up to that point.
        compute_parallel_geometry(
            reference_geom,
            target_geom,
            self.target_kind,
            by_longest=self.params.by_longest,
            angle_threshold=self.params.angle,
            trace=op.add_trace("Rotation math for the failed feature"),
        )

    def process_feature(self, feature: QgsFeature) -> QgsFeature:
        target = Target(feature)

        if self.params.no_multi and target.is_multi:
            return self.create_new_feature(target)

        # Selected by centroid distance; an edge of the target may be nearer to a different reference.
        closest_reference = self.reference_layer.get_closest_feature(target.center_xy)

        if self.params.distance and closest_reference.geom.distance(target.geom) > self.params.distance:
            return self.create_new_feature(target)

        rotated_geom = compute_parallel_geometry(
            closest_reference.geom,
            target.geom,
            self.target_kind,
            by_longest=self.params.by_longest,
            angle_threshold=self.params.angle,
        )
        if rotated_geom is not None:
            target.apply_rotated_geometry(rotated_geom)
        return self.create_new_feature(target)

    def create_new_feature(self, target: Target) -> QgsFeature:
        new_feature = QgsFeature(self.params.fields)
        new_feature.setGeometry(target.geom)
        new_feature.setAttribute(COLUMN_NAME, target.is_rotated)
        return new_feature
