[![PR Validation](https://github.com/elfpkck/parallelizer/actions/workflows/test.yaml/badge.svg?event=pull_request)](https://github.com/elfpkck/parallelizer/actions/workflows/test.yaml)
[![codecov](https://codecov.io/gh/elfpkck/parallelizer/graph/badge.svg?token=QEHFI3XE08)](https://codecov.io/gh/elfpkck/parallelizer)

[![QGIS](https://qgis.github.io/qgis-uni-navigation/logo.svg)](https://qgis.org)

# Parallelizer - QGIS Python Plugin

A QGIS plugin that rotates polygons (and lines) to be parallel to a reference feature (line or polygon — polygon boundary rings are treated as polylines). Two entry points: a batch Processing algorithm that operates on whole layers, and an interactive map tool that lets you pick a reference feature on the canvas and then click — or drag-rectangle — individual line/polygon features to rotate them in place.

[Parallelizer Plugin on QGIS Plugins Web Portal](https://plugins.qgis.org/plugins/PolygonsParallelToLine/)

![Plugin Demo][intro]

## Table of Contents
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Features](#features)
- [Algorithm](#algorithm)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Requirements](#requirements)
- [Best Practices](#best-practices)
- [Development](#development)
- [License](#license)
- [Support](#support)

## Installation

### Method 1: QGIS Plugin Manager (Recommended)
1. Open QGIS
2. Go to **Plugins** → **Manage and Install Plugins**
3. Search for "Parallelizer"
4. Click **Install Plugin**

![Installation Guide][install]

### Method 2: Manual Installation
1. Download the plugin from the [QGIS Plugins Repository](https://plugins.qgis.org/plugins/PolygonsParallelToLine/)
2. Extract to your QGIS plugins directory
3. Restart QGIS and enable the plugin

## Quick Start

### Batch (Processing algorithm)
1. **Access the Plugin**: Go to **Processing** → **Toolbox** → **Parallelizer**
2. **Select Input Layers**: Choose your target layer (line or polygon) and a reference layer (line or polygon)
3. **Configure Parameters**: Set distance and angle thresholds as needed
4. **Run**: Execute the algorithm to generate aligned features

![How to Access][open]

### Interactive (map tool)
1. Open the **Parallelizer** toolbar (also reachable from **Vector** → **Parallelizer**) and click the **Parallel to Line (interactive)** action.
2. Click a line or polygon feature, or drag a rectangle over one, to set it as the reference. The reference is highlighted on the canvas (polygons get an outline plus translucent fill); if both a line and a polygon are under the click, the line wins.

   ![Set a reference][interactive_activate]

3. Toggle editing on the layers you want to modify, then click a single line/polygon to rotate it...

   ![Rotate features one click at a time][interactive_single_click]

   ...or drag a rectangle to rotate every line/polygon feature that intersects it across all editable visible layers.

   ![Rotate every feature in a rectangle][interactive_drag_rectangle]

4. To align to one specific segment instead of the nearest one, turn on **Pick reference segment** (the reference becomes the segment you click) and/or **Pick target segment** (the target edge you click is the one made parallel).

   ![Pick a single reference or target segment][interactive_pick_segment]

5. Use the **Settings…** action on the same toolbar to choose the rotation strategy (*Rotate by longest segment*) and the segment-picking modes. Settings persist via `QSettings`.

   ![Settings dialog][interactive_settings]

6. Right-click or press **Esc** to clear the reference; press **Esc** again to deactivate the tool.

## Features

- **Two modes**: batch Processing algorithm for whole layers, plus an interactive map-canvas tool for one-off rotations  
- **Automatic Rotation**: Rotates polygons or lines to align with the nearest reference edge (line or polygon ring)  
- **Line-target support**: both batch and interactive modes can rotate line features, not just polygons  
- **Bulk drag-rectangle**: rotate every line/polygon intersecting a rectangle across all editable visible layers — wrapped per-layer in undo-able edit commands  
- **CRS-aware**: reference and targets across layers in different CRSes are reconciled via `QgsCoordinateTransform`  
- **QGIS 4 / Qt 6 compatible**: declared via `qgisMaximumVersion=4.99`, with Qt enums fully scoped for PyQt6  
- **Distance-based Filtering**: Optional maximum distance constraint (batch mode)  
- **Angle Threshold Control**: Configurable angle threshold that gates which features are rotated (batch mode)  
- **Multipart Handling**: Multipart features (multipolygons / multilines) are processed (or can be skipped) — see Keynotes for behavior  
- **Rotation Tracking**: Adds a `_rotated` boolean field marking which features were modified (batch mode)  

## Algorithm

### Polygon targets

The plugin processes each polygon using the following steps:

1. **Closest Reference Selection**: Finds the reference feature (line or polygon) whose geometry is the nearest neighbor of the polygon centroid

2. **Distance Check**: If `Max distance from reference` > 0 and the polygon-to-reference geometry distance exceeds it → skip rotation (note: when the target polygon overlaps the reference polygon, the distance is 0 and rotation runs even at small thresholds)

3. **Vertex Analysis**: Identifies the polygon vertex closest to the nearest reference

4. **Segment Evaluation**: Takes the two polygon segments adjacent to that vertex

5. **Angle Calculation**: Computes the signed angle (delta azimuth) between the closest reference segment (any ring of a polygon reference counts) and each adjacent polygon segment

6. **Rotation Decision** (each delta is compared against `Max angle`):
   - If both deltas are within `Max angle`:
     - **Longest segment mode**: Rotates by the delta of the longer segment (falls back to smallest delta if lengths are equal)
     - **Default mode**: Rotates by the smaller delta
   - If only one delta is within `Max angle`: Rotates by that delta
   - If neither delta is within `Max angle`: No rotation is applied
   - Rotations whose magnitude is effectively zero are skipped (avoids spurious `_rotated=True`)

7. **Output Generation**: Writes the (possibly rotated) geometry with a `_rotated` boolean attribute

![Default Usage][default_usage]

### Line targets

For line targets, one segment of the target line is selected — the longest when `Rotate by longest segment` is enabled, otherwise the segment whose delta-azimuth to the closest reference segment is smallest. The whole line is rotated around its centroid by that delta, unless the delta exceeds `Max angle` (in which case no rotation is applied).

### Keynotes
- Rotation center is the target feature's centroid (for multipart features: the overall centroid of the whole multi-geometry)
- Interior rings and duplicate vertices are ignored when picking the rotation pivot for polygon targets, but they are preserved in the output geometry
- Multipart features are rotated as a single rigid body around the overall centroid by the angle chosen from the part that contains the closest vertex (polygons) or picked segment (lines) — individual parts are not aligned independently
- The `_rotated` field indicates transformation status (boolean)

![Rotated Field][_rotated]

## Configuration

### Max Distance from Reference
- **Type**: Float (optional)
- **Range**: ≥ 0.0
- **Default**: 0.0 (processes all features)
- **Unit**: Reference layer CRS units

When set to 0.0, all features are processed regardless of distance.

![Distance Configuration][distance]

### Max Angle for Rotation
- **Type**: Float (optional)  
- **Range**: 0.0 - 89.9 degrees
- **Default**: 89.9
- **Purpose**: Threshold on the rotation angle (the delta azimuth between a target segment and the closest reference segment). A feature is only rotated when at least one candidate delta is within this threshold; otherwise it is left unrotated. For polygons the two segments adjacent to the closest vertex are evaluated; for lines a single picked target segment is evaluated. The applied rotation is bounded by this threshold.

![Angle Configuration][angle]

### Rotate by Longest Segment
- **Type**: Boolean
- **Default**: False
- **Behavior**: Prefer the longest segment when picking the rotation. For polygons this only kicks in when both adjacent segments are within Max angle (otherwise the in-threshold one wins); for lines the longest segment of the target is picked outright.

![Longest Segment Option][by_longest]

### Skip Multipart Features
- **Type**: Boolean
- **Default**: False
- **Purpose**: When enabled, multipart features (multipolygons / multilines) are passed through to the output unchanged (with `_rotated=False`) instead of being rotated. They are not removed from the output layer.

## Usage Examples

### Basic Usage
**Input**: Building polygons + Road centerlines  
**Output**: Buildings aligned parallel to the nearest roads

### Advanced Filtering
**Max Distance**: 50.0 (meters)  
**Max Angle**: 45.0 (degrees)  
**Result**: Only buildings within 50 m of roads, with rotation ≤ 45°

## Requirements

- **QGIS**: 3.0 or higher (compatible with QGIS 4.x / Qt 6)
- **Dependencies**: Standard QGIS processing framework

### Compatibility
- Windows
- macOS  
- Linux

## Best Practices

⚠️ **Important**: Validate and fix geometry errors before running the plugin for optimal results.

### Recommended Workflow
1. **Prepare Data**: Ensure target and reference layers are in the same CRS
2. **Fix Geometries**: Use **Processing Toolbox** → **Vector geometry** → **Fix Geometries**
3. **Test Parameters**: Start with default settings on a small dataset
4. **Batch Process**: Apply to full dataset with optimized parameters

### Performance Tips
- Use spatial indexes for large datasets
- Consider processing in smaller batches for huge datasets
- Test different parameter combinations on representative samples

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed development setup and contribution guidelines.

## License

Copyright (C) 2016-2026 by Andrii Liekariev

This project is licensed under the [GNU General Public License v2.0 or later](LICENSE.txt).

## Support

- 🐛 [Report Issues](https://github.com/elfpkck/parallelizer/issues)
- 💬 [Discussion Forum](https://github.com/elfpkck/parallelizer/discussions)

---

**Made with ❤️ for the QGIS community**

<!-- Image References -->
[intro]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/intro.gif
[install]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/install.gif
[open]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/open.gif
[default_usage]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/default_usage.gif
[_rotated]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/_rotated.png
[distance]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/distance.gif
[angle]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/angle.gif
[by_longest]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/by_longest.gif
[interactive_activate]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/interactive_activate.gif
[interactive_single_click]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/interactive_single_click.gif
[interactive_drag_rectangle]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/interactive_drag_rectangle.gif
[interactive_pick_segment]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/interactive_pick_segment.gif
[interactive_settings]: https://raw.githubusercontent.com/elfpkck/pptl_images/refs/heads/master/interactive_settings.gif
