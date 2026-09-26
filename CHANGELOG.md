# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
- Pass the QGIS plugin portal's Qt6 compatibility check: legacy enum fallbacks for QGIS < 3.30/3.36 are now spelled with their enum class (no behavior change)

## [2.1.1] - 2026-09-26
- Fix the interactive map tool crashing on every single click under QGIS 4 / Qt 6 (`QMouseEvent.x()`/`y()` no longer exist); drag-rectangle was unaffected
- Fix the Processing algorithm failing on QGIS 3.22-3.36 when creating the `_rotated` output field
- Fix polygon holes (interior rings) influencing the rotation: the pivot vertex and its adjacent segments now always come from the exterior ring

## [2.1.0] - 2026-06-07
- Accept line layers as the rotation target in the Processing algorithm (the interactive map tool already supported both). **Breaking:** the Processing parameter `POLYGON_LAYER` has been renamed to `TARGET_LAYER` (saved models referencing the old key must be updated); user-facing labels switch from "Polygon layer" / "Skip multipolygons" to "Target layer" / "Skip multipart features"
- Mark the plugin as QGIS 4 compatible (`qgisMaximumVersion=4.99`) and fully scope PyQt6-required Qt enums in the map tool and settings dialog so they work on QGIS 4 / Qt 6
- Accept polygon layers as the rotation reference in both the Processing algorithm and the interactive map tool; polygon boundary rings are treated as polylines for closest-segment math. **Breaking:** the Processing parameter `LINE_LAYER` has been renamed to `REFERENCE_LAYER` (saved models referencing the old key must be updated). The map tool prefers a line under the click when both a line and polygon overlap.
- Rename the plugin to "Parallelizer" and remove the Processing-toolbox group, so the algorithm appears at the toolbox root instead of under "Algorithms for vector layers"

## [2.0.0] - 2026-06-06
- Add an interactive map tool: pick a reference line on the canvas, then click — or drag a rectangle — to rotate individual line/polygon features (or every feature in the rectangle across editable visible layers) parallel to it
- Add a Settings dialog (toggle "Rotate by longest segment"); choices persist via `QSettings`
- Reconcile reference and target geometries when layers are in different CRSes
- Register a "Polygons Parallel to Line" toolbar and Vector-menu entry

## [1.2.0] - 2026-06-01
- Enhance polygon rotation logic and geometry validation
- Fix `no_multi` regression
- Test against QGIS 4.0
- Automate releases via tag-triggered `qgis-plugin-ci` pipeline

## [1.1.0] - 2025-06-13
- Enhance UI text readability
- Skip rotation when an angle is close to zero to avoid unnecessary operations and wrong "_rotated" values
- Handle polygon vertex duplicates and internal rings properly
- Update test cases to cover new edge cases

## [1.0.0] - 2023-11-19
- Add QGIS 3 support
- Add tests
- Fix bugs

## [0.3.0] - 2017-04-05
- Migrate to Processing algorithms; full refactoring
- Major performance improvements
- Add option to write all or only selected polygons to the new layer
- Add option to rotate or skip multipolygons
- Process each polyline of a multipolyline as a separate line
- Avoid duplicating the "_rotated" field if it already exists

## [0.2.1] - 2017-03-14
- Add Russian and Ukrainian translations

## [0.2.0] - 2016-04-07
- Major performance improvements via spatial index and built-in PyQGIS methods
- Add progress bar
- Add option to rotate only selected polygons
- Add "rotated" field to the polygon layer (value `1` for rotated polygons)
- New icon
- English-only code comments

## [0.1.0] - 2016-03-31
- Initial release
