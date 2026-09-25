from __future__ import annotations

import dataclasses
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsProcessingException,
    QgsProcessingFeatureSource,
    QgsWkbTypes,
)

from .const import COLUMN_NAME, LINE_GEOMETRY
from .parallelizer import compute_parallel_geometry
from .reference import ReferenceLayer
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
        self.rotate_features()

    def validate_target_layer(self) -> None:
        if not self.total_number:
            msg = "Target layer is empty"
            raise QgsProcessingException(msg)

    def rotate_features(self) -> None:
        total = 100.0 / self.total_number

        for i, feature in enumerate(self.params.target_layer.getFeatures(), start=1):
            if self.feedback.isCanceled():
                break

            processed = self.process_feature(feature)
            self.params.sink.addFeature(processed, QgsFeatureSink.Flag.FastInsert)
            self.feedback.setProgress(int(i * total))

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
