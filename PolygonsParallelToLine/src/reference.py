from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from qgis.core import QgsPoint, QgsProcessingException, QgsSpatialIndex, QgsWkbTypes

from .const import LINE_GEOMETRY, POLYGON_GEOMETRY

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qgis.core import (
        QgsFeature,
        QgsGeometry,
        QgsPointXY,
        QgsProcessingFeatureSource,
    )


class ReferenceFeature:
    def __init__(self, feature: QgsFeature):
        self.geom: QgsGeometry = feature.geometry()

    @classmethod
    def from_geometry(cls, geom: QgsGeometry) -> ReferenceFeature:
        obj = cls.__new__(cls)
        obj.geom = geom
        return obj

    def get_closest_segment(self, point_xy: QgsPointXY) -> Segment:
        sqr_dist, _, next_vertex_idx, _ = self.geom.closestSegmentWithContext(point_xy)
        if sqr_dist < 0 or next_vertex_idx <= 0:
            msg = f"Reference geometry has no valid segment near {point_xy}"
            raise QgsProcessingException(msg)
        start, end = self.geom.vertexAt(next_vertex_idx - 1), self.geom.vertexAt(next_vertex_idx)
        return Segment(start=start, end=end)


class ReferenceLayer:
    def __init__(self, source: QgsProcessingFeatureSource):
        self.id_feature_map: dict[int, QgsFeature] = {x.id(): x for x in source.getFeatures()}
        self.spatial_index = QgsSpatialIndex(flags=QgsSpatialIndex.Flag.FlagStoreFeatureGeometries)
        self.spatial_index.addFeatures(self.id_feature_map.values())

    def get_closest_feature(self, point: QgsPointXY) -> ReferenceFeature:
        closest_id = self.spatial_index.nearestNeighbor(point, 1)

        if not closest_id:
            msg = f"No reference features found near point {point}"
            raise QgsProcessingException(msg)

        return ReferenceFeature(self.id_feature_map[closest_id[0]])


class Segment:
    def __init__(self, start: QgsPoint, end: QgsPoint):
        self.start = start
        self.end = end

    @cached_property
    def length(self) -> float:
        return self.start.distance(self.end)

    @cached_property
    def azimuth(self) -> float:
        # QgsPoint.azimuth returns -180..180
        return self.start.azimuth(self.end)


def iter_segments(geom: QgsGeometry) -> Iterator[Segment]:
    # Per-ring iteration so polygons with interior rings (or multipart geometries)
    # never produce a spurious segment that jumps between rings/parts.
    for part in geom.asGeometryCollection() or [geom]:
        gtype = QgsWkbTypes.geometryType(part.wkbType())
        if gtype == LINE_GEOMETRY:
            rings = part.asMultiPolyline() if part.isMultipart() else [part.asPolyline()]
        elif gtype == POLYGON_GEOMETRY:
            polygons = part.asMultiPolygon() if part.isMultipart() else [part.asPolygon()]
            rings = [ring for poly in polygons for ring in poly]
        else:
            continue
        for ring in rings:
            for i in range(len(ring) - 1):
                yield Segment(start=QgsPoint(ring[i]), end=QgsPoint(ring[i + 1]))
