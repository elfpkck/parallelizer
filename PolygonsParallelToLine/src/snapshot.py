"""
Describers for the problem report. They return plain lines without layer names, file paths, attribute
values or coordinates, so they are safe to log and to show in the report.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsProcessingFeatureSourceDefinition,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .const import COLUMN_NAME
from .diagnostics import MAX_WKT_VERTICES

if TYPE_CHECKING:
    from qgis.core import QgsProcessingContext, QgsProcessingFeatureSource
    from qgis.gui import QgsMapCanvas

    from .diagnostics import Operation

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_VALIDITY_ERRORS = 3
_MAX_VALIDATED_VERTICES = 2_000

F = TypeVar("F", bound=Callable[..., list[str]])


def _never_raises(func: F) -> F:
    """A describer must not break the map tool or a Processing run it only reports on."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> list[str]:  # noqa: ANN401
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return [f"unavailable ({type(exc).__name__})"]

    return wrapper  # type: ignore[return-value]


def _enum_name(value: Any) -> str:  # noqa: ANN401
    return str(getattr(value, "name", value))


def describe_crs(crs: QgsCoordinateReferenceSystem) -> str:
    if not crs.isValid():
        return "none"
    kind = "geographic" if crs.isGeographic() else "projected"
    units = QgsUnitTypes.toString(crs.mapUnits())
    # A custom CRS has no authid; its PROJ definition carries no personal data.
    return f"{crs.authid() or crs.toProj()} ({kind}, {units})"


@_never_raises
def _type_and_crs(wkb_type: QgsWkbTypes.Type, crs: QgsCoordinateReferenceSystem) -> list[str]:
    return [f"geometry type: {QgsWkbTypes.displayString(wkb_type)}", f"CRS: {describe_crs(crs)}"]


@_never_raises
def describe_layer(layer: QgsVectorLayer) -> list[str]:
    fields = layer.fields()
    return [
        *_type_and_crs(layer.wkbType(), layer.crs()),
        f"provider: {layer.providerType()}, storage: {layer.storageType() or 'unknown'}",
        f"features: {layer.featureCount()}, selected: {layer.selectedFeatureCount()}",
        f"editable: {layer.isEditable()}, fields: {fields.count()}, has {COLUMN_NAME}: "
        f"{fields.indexFromName(COLUMN_NAME) != -1}",
    ]


@_never_raises
def describe_source(source: QgsProcessingFeatureSource, layer: QgsVectorLayer | None) -> list[str]:
    if layer is not None:
        return describe_layer(layer)
    return [*_type_and_crs(source.wkbType(), source.sourceCrs()), f"features: {source.featureCount()}"]


@_never_raises
def describe_geometry(geom: QgsGeometry) -> list[str]:
    if geom.isNull() or geom.isEmpty():
        return ["geometry: null or empty"]
    wkb = geom.wkbType()
    abstract = geom.constGet()
    lines = [
        f"geometry: {QgsWkbTypes.displayString(wkb)}, curved: {QgsWkbTypes.isCurvedType(wkb)}",
        f"parts: {abstract.partCount()}, rings: {sum(abstract.ringCount(i) for i in range(abstract.partCount()))}, "
        f"vertices: {abstract.nCoordinates()}",
        f"length: {geom.length():.6g}, area: {geom.area():.6g}",
    ]
    if abstract.nCoordinates() > _MAX_VALIDATED_VERTICES:
        lines.append(f"validity: not checked, more than {_MAX_VALIDATED_VERTICES} vertices")
        return lines
    lines.append(f"duplicate vertices: {QgsGeometry(geom).removeDuplicateNodes()}")
    # GEOS validation is O(n log n), unlike the default QGIS validator. Its messages carry vertex positions;
    # every number is masked.
    errors = geom.validateGeometry(Qgis.GeometryValidationEngine.Geos)
    lines.append(f"GEOS valid: {not errors}")
    reasons = sorted({_NUMBER.sub("#", error.what()) for error in errors})
    if reasons:
        lines.append(f"validity problems: {'; '.join(reasons[:_MAX_VALIDITY_ERRORS])}")
    return lines


@_never_raises
def describe_project(canvas: QgsMapCanvas | None = None, project: QgsProject | None = None) -> list[str]:
    project = project or QgsProject.instance()
    snapping = project.snappingConfig()
    lines = [
        f"project CRS: {describe_crs(project.crs())}",
        f"ellipsoid: {project.ellipsoid() or 'none'}, distance units: {QgsUnitTypes.toString(project.distanceUnits())}",
        f"layers: {project.count()}",
        f"topological editing: {project.topologicalEditing()}, "
        f"avoid overlap: {_enum_name(project.avoidIntersectionsMode())}",
        f"snapping: {snapping.enabled()}, tolerance: {snapping.tolerance():g} {_enum_name(snapping.units())}",
    ]
    if canvas is not None:
        search_radius = QgsSettings().value("Map/searchRadiusMM", 2.0, type=float)
        lines.extend(
            [
                f"canvas CRS: {describe_crs(canvas.mapSettings().destinationCrs())}",
                f"canvas rotation: {canvas.rotation():g}, scale: 1:{canvas.scale():.0f}",
                f"identify search radius: {search_radius:g} mm",
            ]
        )
    return lines


@_never_raises
def describe_processing(parameters: dict[str, Any], context: QgsProcessingContext) -> list[str]:
    selected_only = sorted(
        name
        for name, value in parameters.items()
        if isinstance(value, QgsProcessingFeatureSourceDefinition) and value.selectedFeaturesOnly
    )
    options = {name: value for name, value in parameters.items() if isinstance(value, (bool, int, float))}
    return [
        f"invalid features filtering: {_enum_name(context.invalidGeometryCheck())}",
        f"selected features only: {', '.join(selected_only) or 'no'}",
        f"options: {', '.join(f'{name}={value}' for name, value in sorted(options.items())) or 'defaults'}",
    ]


def reference_near(reference: QgsGeometry, target: QgsGeometry, distance: float) -> QgsGeometry:
    """
    The part of the reference around the target: its bounding box grown by 1.5 times its size or the distance
    to the reference, whichever is larger, so the closest reference segments are well inside (not on the edge).
    At least 1% of the reference's extent, so a click point still gets a useful stretch of reference.
    """
    try:
        box, extent = target.boundingBox(), reference.boundingBox()
        margin = max(
            1.5 * max(box.width(), box.height(), distance),
            0.01 * max(extent.width(), extent.height()),
        )
        clipped = reference.intersection(QgsGeometry.fromRect(box.buffered(margin)))
    except Exception:  # noqa: BLE001
        return reference
    return reference if clipped.isNull() or clipped.isEmpty() else clipped


def add_reference_geometry(  # noqa: PLR0913
    op: Operation,
    reference: QgsGeometry,
    target: QgsGeometry,
    target_label: str,
    distance: float,
    crs: QgsCoordinateReferenceSystem | None = None,
) -> None:
    """A small reference is added once as is; a large one is clipped around each target instead."""
    if reference.isNull():
        return
    if reference.constGet().nCoordinates() <= MAX_WKT_VERTICES:
        if not any(role == "reference" for role, _ in op.geometries):
            op.add_geometry("reference", reference, crs)
    else:
        op.add_geometry(f"reference near {target_label}", reference_near(reference, target, distance), crs)
