from qgis.core import Qgis, QgsProcessing, QgsProcessingParameterNumber, QgsWkbTypes

COLUMN_NAME = "_rotated"
OUTPUT_LAYER_NAME = "Output layer with rotated features"

# Scoped enums below were added in QGIS 3.30/3.36; older releases only expose the legacy aliases.
try:
    LINE_GEOMETRY = Qgis.GeometryType.Line
    POLYGON_GEOMETRY = Qgis.GeometryType.Polygon
except AttributeError:
    LINE_GEOMETRY = QgsWkbTypes.LineGeometry
    POLYGON_GEOMETRY = QgsWkbTypes.PolygonGeometry

# Line first: where both are candidates, a line is preferred.
SUPPORTED_GEOMETRIES = (LINE_GEOMETRY, POLYGON_GEOMETRY)

try:
    SOURCE_TYPE_LINE = Qgis.ProcessingSourceType.VectorLine
    SOURCE_TYPE_POLYGON = Qgis.ProcessingSourceType.VectorPolygon
    NUMBER_TYPE_DOUBLE = Qgis.ProcessingNumberParameterType.Double
except AttributeError:
    SOURCE_TYPE_LINE = QgsProcessing.TypeVectorLine
    SOURCE_TYPE_POLYGON = QgsProcessing.TypeVectorPolygon
    NUMBER_TYPE_DOUBLE = QgsProcessingParameterNumber.Double

# QgsField(name, QMetaType.Type) needs QGIS 3.38+.
if Qgis.QGIS_VERSION_INT >= 33800:  # noqa: PLR2004
    from qgis.PyQt.QtCore import QMetaType  # type: ignore[import-not-found]

    BOOL_FIELD_TYPE = QMetaType.Type.Bool
else:
    from qgis.PyQt.QtCore import QVariant  # type: ignore[import-not-found]

    BOOL_FIELD_TYPE = QVariant.Bool
