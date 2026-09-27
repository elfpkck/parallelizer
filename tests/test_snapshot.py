from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingFeatureSourceDefinition,
    QgsVectorLayer,
)

from PolygonsParallelToLine.src import diagnostics, snapshot


def test_describe_geometry_counts_parts_rings_and_vertices():
    geom = QgsGeometry.fromWkt(
        "MultiPolygon (((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 3 2, 3 3, 2 3, 2 2)), ((20 0, 30 0, 30 10, 20 0)))"
    )

    lines = snapshot.describe_geometry(geom)

    assert "geometry: MultiPolygon, curved: False" in lines
    assert "parts: 2, rings: 3, vertices: 14" in lines
    assert "GEOS valid: True" in lines


def test_describe_geometry_masks_numbers_in_validity_problems():
    bowtie = QgsGeometry.fromWkt("Polygon ((0 0, 10 10, 10 0, 0 10, 0 0))")

    lines = snapshot.describe_geometry(bowtie)

    assert "GEOS valid: False" in lines
    problems = next(line for line in lines if line.startswith("validity problems"))
    assert not any(char.isdigit() for char in problems)


def test_describe_geometry_null_and_failure():
    assert snapshot.describe_geometry(QgsGeometry()) == ["geometry: null or empty"]
    assert snapshot.describe_geometry(None) == ["unavailable (AttributeError)"]


def test_describe_layer_has_no_name():
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326&field=_rotated:boolean", "secret layer name", "memory")

    lines = snapshot.describe_layer(layer)

    assert "CRS: EPSG:4326 (geographic, degrees)" in lines
    assert "provider: memory, storage: Memory storage" in lines
    assert "has _rotated: True" in lines[-1]
    assert not any("secret" in line for line in lines)


def test_describe_crs_custom_uses_proj_definition():
    crs = QgsCoordinateReferenceSystem.fromProj("+proj=tmerc +lon_0=17 +ellps=GRS80 +units=m")

    assert snapshot.describe_crs(crs).startswith("+proj=tmerc")
    assert snapshot.describe_crs(QgsCoordinateReferenceSystem()) == "none"


def test_describe_project_with_canvas(qgis_canvas, qgis_new_project):
    qgis_canvas.setRotation(15)
    try:
        lines = snapshot.describe_project(qgis_canvas)
    finally:
        qgis_canvas.setRotation(0)

    assert any(line.startswith("topological editing: False") for line in lines)
    assert any(line.startswith("canvas rotation: 15") for line in lines)


def test_describe_processing_lists_selected_only_and_options():
    parameters = {
        "TARGET_LAYER": QgsProcessingFeatureSourceDefinition("layer-id", selectedFeaturesOnly=True),
        "LONGEST": True,
        "DISTANCE": 5.0,
        "OUTPUT": "TEMPORARY_OUTPUT",
    }

    lines = snapshot.describe_processing(parameters, QgsProcessingContext())

    assert "selected features only: TARGET_LAYER" in lines
    assert "options: DISTANCE=5.0, LONGEST=True" in lines


def test_reference_near_clips_a_large_reference_around_the_target():
    reference = QgsGeometry.fromPolylineXY([QgsPointXY(x, 0) for x in range(5000)])
    target = QgsGeometry.fromWkt("Polygon ((2500 10, 2510 10, 2510 20, 2500 20, 2500 10))")

    near = snapshot.reference_near(reference, target, reference.distance(target))

    assert near.constGet().nCoordinates() < 200
    assert near.distance(target) == reference.distance(target)


def test_add_reference_geometry_small_once_large_clipped():
    target = QgsGeometry.fromWkt("Point (2505 15)")
    small = QgsGeometry.fromWkt("LineString (0 0, 5000 0)")
    large = QgsGeometry.fromPolylineXY([QgsPointXY(x, 0) for x in range(5000)])
    with diagnostics.operation("rotate") as op:
        snapshot.add_reference_geometry(op, small, target, "target 1", 15.0)
        snapshot.add_reference_geometry(op, small, target, "target 2", 15.0)
        snapshot.add_reference_geometry(op, large, target, "target 3", 15.0)

    assert [role for role, _ in op.geometries] == ["reference", "reference near target 3"]


def test_reference_near_a_point_keeps_a_useful_stretch():
    reference = QgsGeometry.fromPolylineXY([QgsPointXY(x, 0) for x in range(5000)])

    near = snapshot.reference_near(reference, QgsGeometry.fromWkt("Point (2500 0.5)"), 0.5)

    assert near.length() >= 90
    assert near.constGet().nCoordinates() < 200
