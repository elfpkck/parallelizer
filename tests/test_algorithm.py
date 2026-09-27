from __future__ import annotations

import pytest
from qgis import processing
from qgis.core import QgsFeature, QgsGeometry, QgsProcessingContext, QgsProcessingException, QgsVectorLayer

from PolygonsParallelToLine.src import diagnostics, pptl
from PolygonsParallelToLine.src.algorithm import Algorithm


@pytest.fixture(scope="module")
def algorithm_instance():
    """Fixture to create an instance of the Algorithm class."""
    return Algorithm()


def test_algorithm_initialization(algorithm_instance):
    """Test if the Algorithm instance initializes correctly."""
    assert algorithm_instance.name() == "pptl_algo"
    assert algorithm_instance.displayName() == "Parallelizer"
    assert algorithm_instance.group() == ""
    assert algorithm_instance.groupId() == ""
    assert algorithm_instance.shortHelpString() == (
        "Rotates line or polygon features parallel to features in a reference layer (line or polygon)."
    )
    assert algorithm_instance.helpUrl() == "https://elfpkck.github.io/parallelizer/"


def test_algorithm_create_instance(algorithm_instance):
    """Test if the createInstance method creates a new instance of the algorithm."""
    new_instance = algorithm_instance.createInstance()
    assert isinstance(new_instance, Algorithm)


def test_failed_processing_run_snapshots_the_failing_feature(qgis_processing, monkeypatch):
    reference = QgsVectorLayer("LineString?crs=EPSG:3857", "ref", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LineString (0 50, 100 50)"))
    reference.dataProvider().addFeatures([feature])
    target = QgsVectorLayer("Polygon?crs=EPSG:3857", "target", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("Polygon ((40 60, 58 64, 54 82, 36 78, 40 60))"))
    target.dataProvider().addFeatures([feature])

    def broken(*_args: object, **kwargs: object) -> None:
        trace = kwargs.get("trace")
        if isinstance(trace, dict):
            trace["reached"] = "before the failure"
        msg = "boom"
        raise ValueError(msg)

    monkeypatch.setattr(pptl, "compute_parallel_geometry", broken)
    params = {"REFERENCE_LAYER": reference, "TARGET_LAYER": target, "OUTPUT": "TEMPORARY_OUTPUT"}

    with pytest.raises(QgsProcessingException):
        processing.run(algOrName=Algorithm(), parameters=params, context=QgsProcessingContext())

    report = diagnostics.build_report()
    assert "Operation that failed\n  failed: processing run (failed" in report
    assert "  Failed feature\n    id: 1" in report
    assert "    reached: before the failure" in report
    assert "    invalid features filtering: " in report
    block = diagnostics.geometry_block() or ""
    # The first reference vertex is the origin, whatever order the geometries were recorded in.
    assert "reference: LineString (0 0, 100 0)" in block
    assert "target: Polygon ((40 10, " in block
