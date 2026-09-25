from __future__ import annotations

from typing import Any, TYPE_CHECKING

from qgis.core import (
    QgsField,
    QgsFields,
    QgsProcessingAlgorithm,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
)
from .const import (
    BOOL_FIELD_TYPE,
    COLUMN_NAME,
    NUMBER_TYPE_DOUBLE,
    OUTPUT_LAYER_NAME,
    SOURCE_TYPE_LINE,
    SOURCE_TYPE_POLYGON,
)
from .pptl import Params, ParallelToReference

if TYPE_CHECKING:
    from qgis.core import (
        QgsProcessingContext,
        QgsProcessingFeatureSource,
        QgsProcessingFeedback,
    )


class Algorithm(QgsProcessingAlgorithm):
    OUTPUT_LAYER = "OUTPUT"
    REFERENCE_LAYER = "REFERENCE_LAYER"
    TARGET_LAYER = "TARGET_LAYER"
    LONGEST = "LONGEST"
    NO_MULTI = "NO_MULTI"
    DISTANCE = "DISTANCE"
    ANGLE = "ANGLE"

    def createInstance(self) -> Algorithm:  # noqa: N802
        return self.__class__()

    def name(self) -> str:
        return "pptl_algo"

    def displayName(self) -> str:  # noqa: N802
        return "Parallelizer"

    def group(self) -> str:
        return ""

    def groupId(self) -> str:  # noqa: N802
        return ""

    def shortHelpString(self) -> str:  # noqa: N802
        return "Rotates line or polygon features parallel to features in a reference layer (line or polygon)."

    def helpUrl(self) -> str:  # noqa: N802
        return "https://elfpkck.github.io/parallelizer/"

    def initAlgorithm(self, config: dict | None = None) -> None:  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                OUTPUT_LAYER_NAME,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.REFERENCE_LAYER,
                "Reference layer",
                [SOURCE_TYPE_LINE, SOURCE_TYPE_POLYGON],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.TARGET_LAYER,
                "Target layer",
                [SOURCE_TYPE_LINE, SOURCE_TYPE_POLYGON],
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(self.LONGEST, "Rotate by the longest segment", defaultValue=False)
        )
        self.addParameter(QgsProcessingParameterBoolean(self.NO_MULTI, "Skip multipart features", defaultValue=False))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DISTANCE,
                "Max distance from reference (in units of reference layer CRS) (optional)",
                type=NUMBER_TYPE_DOUBLE,
                minValue=0.0,
                defaultValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ANGLE,
                "Max angle (in degrees) for rotation (optional)",
                type=NUMBER_TYPE_DOUBLE,
                minValue=0.0,
                maxValue=89.9,
                defaultValue=89.9,
            )
        )

    def _create_output_fields(self, source_layer: QgsProcessingFeatureSource) -> QgsFields:
        fields = source_layer.fields()
        if fields.indexFromName(COLUMN_NAME) == -1:
            fields.append(QgsField(COLUMN_NAME, BOOL_FIELD_TYPE))
        return fields

    def processAlgorithm(  # noqa: N802
        self, parameters: dict[str, Any], context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ) -> dict[str, str]:
        target_layer = self.parameterAsSource(parameters, self.TARGET_LAYER, context)
        output_fields = self._create_output_fields(target_layer)
        sink, dest_id = self.parameterAsSink(
            parameters=parameters,
            name=self.OUTPUT_LAYER,
            context=context,
            fields=output_fields,
            geometryType=target_layer.wkbType(),
            crs=target_layer.sourceCrs(),
        )
        params = Params(
            reference_layer=self.parameterAsSource(parameters, self.REFERENCE_LAYER, context),
            target_layer=target_layer,
            by_longest=self.parameterAsBool(parameters, self.LONGEST, context),
            no_multi=self.parameterAsBool(parameters, self.NO_MULTI, context),
            distance=self.parameterAsDouble(parameters, self.DISTANCE, context),
            angle=self.parameterAsDouble(parameters, self.ANGLE, context),
            fields=output_fields,
            sink=sink,
        )
        ParallelToReference(feedback, params).run()
        return {self.OUTPUT_LAYER: dest_id}
