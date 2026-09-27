# CLAUDE.md

## Project

QGIS Processing plugin `PolygonsParallelToLine` (id `pptl`): rotates polygons and lines to be parallel to the nearest segment of a reference line or polygon layer. Distributed via the QGIS plugin portal. Releases are tag-driven via `.github/workflows/release.yaml`; `qgis-plugin-ci` patches `metadata.txt`'s `version=` and `changelog=` in the zip at build time. In-repo `version=` strings are placeholders (`0.0.0`).

## Environment

Tests require QGIS Python bindings, so everything runs inside the `qgis_pptl` Docker container (image `qgis/qgis:<version>`, pinned once by `ARG QGIS_IMAGE` in `Dockerfile` and read by the `Makefile`; repo mounted at `/pptl`). The host `.venv` is *shadowed* inside the container by an anonymous volume — host installs are not visible to the container.

See `Makefile` for build/run/install/test targets (all `make` invocations assume the container is running). Use `make test-all` as the default for running the suite — it covers both the unit tests and the `perf`-marked smoke test in one invocation. Reach for the narrower `make test` / `make test-perf` only when intentionally scoping to one slice.

Single test inside the container:

```shell
docker exec -t qgis_pptl sh -c "cd /pptl && uv run --no-dev pytest /pptl/tests/test_azimuth.py[::test_name] --qgis_disable_gui"
```

Lint/format runs host-side via `pre-commit`. `ruff` is `select = ["ALL"]` with per-file ignores in `pyproject.toml` — when adding ignores, narrow per-file rather than expanding globals.

## Architecture

QGIS discovery chain:

```
PolygonsParallelToLine/__init__.py::classFactory → Plugin.initProcessing → Provider.loadAlgorithms → Algorithm
```

`Algorithm` (`src/algorithm.py`) declares the processing UI (parameter IDs `REFERENCE_LAYER` — accepts line or polygon layers, `TARGET_LAYER` — accepts line or polygon layers, `LONGEST`, `NO_MULTI`, `DISTANCE`, `ANGLE`) and delegates to `ParallelToReference` in `src/pptl.py` via a `Params` dataclass (fields `target_layer`, `by_longest`, `no_multi`, `distance`, `angle`).

`ParallelToReference.process_feature` per-feature pipeline:

1. Wrap feature in `Target` (`src/target.py`) — `geom`, `is_multi`, `center_xy`, `is_rotated`.
2. Wrap reference source in `ReferenceLayer` (`src/reference.py`) — `QgsSpatialIndex` (with `FlagStoreFeatureGeometries`) plus an `id → QgsFeature` dict for `get_closest_feature(point)`. Polygon references are handled transparently: `closestSegmentWithContext` walks all rings (exterior + interior, all parts).
3. Short-circuit (no rotation) if `params.distance` is truthy and centroid→reference distance exceeds it, or if `is_multi and no_multi`.
4. Delegate the rotation math to `compute_parallel_geometry` (`src/parallelizer.py`) with the target's geometry kind. For polygon targets it runs `TargetRotator` (`src/rotator.py`): finds the polygon vertex closest to the reference, derives the two adjacent polygon segments, and computes delta-azimuths against the closest reference segment using helpers in `src/azimuth.py`. For line targets it picks one segment of the target (`_pick_target_segment`) and rotates by the delta vs. the closest reference segment.
5. Polygon rotation strategy: if both adjacent deltas are within `angle_threshold`, rotate by the longest segment (when `by_longest`) or by the smallest angle; if only one is within threshold, rotate by that one. Line rotation strategy: `by_longest` picks the longest segment, otherwise the segment with the smallest angle vs. the reference; the rotation is skipped if `|delta| > angle_threshold`. Rotations within `ABSOLUTE_TOLERANCE` of 0 are skipped (avoids spurious `_rotated=True`).
6. Append a feature to the output sink with a `_rotated` boolean attribute (name in `src/const.py::COLUMN_NAME`).

Invariants:

- Azimuth math (`src/azimuth.py`) normalizes to `[0, 180]` then `[-90, 90]` so the delta is the minimal signed rotation.
- `Segment.length` / `Segment.azimuth` are `cached_property` — segments are immutable after construction.
- Interior rings and duplicate vertices are stripped *only* in the cached `Target.outline` copy, used by both `get_closest_vertex` and `get_adjacent_segments`, so they don't influence the rotation pivot; the original geometry is preserved for the actual rotate.

## Map tool

Alongside the Processing algorithm, `Plugin.initGui` registers a dedicated "Parallelizer" `QToolBar` (objectName `PolygonsParallelToLineToolBar`) with two `QAction`s: the interactive parallelize toggle that activates `ParallelToLineMapTool` (`src/map_tool.py`) on the canvas, and a Settings action that opens `MapToolSettingsDialog` (`src/settings_dialog.py`) to edit `MapToolSettings` (`src/settings.py`). `MapToolSettings` owns live tool state (`by_longest`, `pick_reference_segment`, `pick_target_segment`) and persists each via `QSettings` under the `PolygonsParallelToLine/map_tool/` prefix, emitting `changed` on every write so the toolbar's checkable actions and the dialog's checkboxes stay in sync; the map tool reads these flags at each operation. The tool has two states: first a click or drag-rectangle on a line or polygon sets the reference (`canvasReleaseEvent`; highlighted with a `QgsRubberBand`); after that, a single click identifies and rotates one line/polygon target, while a click-and-drag draws a selection rectangle (`QgsRubberBand` polygon) and on release rotates every line/polygon feature whose geometry intersects the rectangle across all editable visible layers, wrapped per-layer in `beginEditCommand`/`endEditCommand`. Single clicks use `QgsMapToolIdentify.TopDownAll` so the tool walks through identify results top-down and picks the first one whose geometry type matches the current state's needs (line or polygon when no reference is set, with line-first preference: a line under the click wins over a polygon; line or polygon when one is). The drag-rectangle reference path applies the same line-first preference across visible layers. All cross-CRS work goes through `QgsCoordinateTransform` against `QgsProject.instance()`: the reference is stored in the map (canvas) CRS (transformed once at set time from the source layer's CRS) and `_reference_for_layer` re-projects it into each target layer's CRS at rotation time, while filter rectangles are transformed via `transformBoundingBox`. All math is delegated to `compute_parallel_geometry` in `src/parallelizer.py`, which reuses `ReferenceFeature`, `Segment`, `calc_delta_azimuth`, and `TargetRotator` from the batch pipeline; pivot is the target's centroid. Right-click or `Esc` clears the reference (or cancels an in-progress drag). The toolbar additionally exposes two checkable actions (also mirrored as checkboxes in the settings dialog) to pick a single segment of the reference / target. With pick mode on, a single click identifies the segment closest to the click point; a drag-rectangle picks the segment of the topmost matching feature (reference role) or one segment per intersecting feature (target role) via `_pick_segment_in_rect`, which picks the segment with the largest overlap with the rectangle and falls back to the segment closest to the rectangle's center when no segment actually intersects (e.g., a rectangle sitting inside a polygon's interior). When the target-segment flag is on, the chosen `Segment` is passed through as `target_segment=` to `compute_parallel_geometry`, bypassing the usual `_pick_target_segment` selection.

## Diagnostics

`src/diagnostics.py` owns the plugin log (`logging.getLogger("parallelizer")`, set up in `Plugin.initProcessing`, torn down in `unload`): a handler to `QgsMessageLog` (tag `Parallelizer`) and a rotating file `<QGIS profile>/parallelizer/parallelizer.log`. `@capture_errors` wraps the map tool's Qt event handlers and `Algorithm.processAlgorithm`: it records unexpected exceptions (not `UserInputError`) and re-raises, and `diagnostics.notifier` makes `Plugin` offer "Report a problem…" in the message bar (queued, rate-limited). `ReportDialog` (`src/report_dialog.py`) shows `build_report()` for the user to edit, then copies it and opens a pre-filled GitHub issue (`issue_url`; `.github/ISSUE_TEMPLATE/bug_report.yml`, field `diagnostics`) or a `mailto:` link to `contact.REPORT_EMAIL` (`mailto_url`; empty in the repo and filled in from the `REPORT_EMAIL` secret by `release.yaml` only for the package, so never commit an address there; empty hides the email option; a much shorter length limit for mail clients); both go through `fit_report`, which drops the oldest log lines first. **Send report** (`src/report_sender.py`, `ReportSender`) POSTs a JSON payload (schema 1, report capped at `MAX_REPORT_BYTES`) through `QgsNetworkAccessManager` to `contact.REPORT_ENDPOINT` (filled from the `REPORT_ENDPOINT` secret like the email; empty hides the button). The receiver is `tools/report_worker/` (Cloudflare Worker, `node --test`), which files a private GitHub issue and must never store or log IPs or report content; the privacy promises it has to keep are in `site/privacy.md`. Nothing is ever sent automatically; keep it that way (GDPR/ePrivacy). Log calls must never include layer names, file paths, attribute values or coordinates; counts, geometry types, CRS authids, flags and versions are fine. `scrub()` is a best-effort second line of defense, not a license to log sensitive data. Raise `diagnostics.UserInputError` (a `QgsProcessingException`) for problems with the user's input so they are not recorded as bugs; a plain `QgsProcessingException` is recorded. Exception messages must not embed coordinates either. Each map tool release and each Processing run is a `diagnostics.operation(...)`; code inside adds sections via `current_operation()` using the describers in `src/snapshot.py` (fail-safe, no names/paths/coordinates). `compute_parallel_geometry`/`TargetRotator` take an optional `trace` dict for the rotation math; Processing passes none per feature and only re-runs the failing feature with a trace (`ParallelToReference.note_failed_feature`), keeping the hot loop free. The last `MAX_KEPT_OPERATIONS` non-idle operations are kept, plus the failed one. `setup_logging` also arms `faulthandler` into `crash.log` unless someone else owns it; QGIS usually does, writing to `<temp>/qgis-python-crash-info-<pid>` for its crash dialog, and the next session reads the newest unseen such file instead and map tool operations pass `watchdog=True` to dump stacks into `hang.log` after `HANG_TIMEOUT_S`; the next session moves both to `*-previous.log`, reports them and offers "Report a problem…" when they contain plugin frames. Geometries reach the report only through `geometry_block()`, shifted to a local origin, behind an opt-in checkbox in `ReportDialog`.

## Testing

`tests/test_main_functionality.py` is the integration suite — builds in-memory `QgsVectorLayer`s from WKT, runs the pipeline via `processing.run(algOrName=Algorithm(), ...)`, compares output WKT. Extend the existing `@pytest.mark.parametrize` table for new cases rather than adding files. Other `tests/test_*.py` files are module unit tests and don't need a full processing run.

`tests/test_performance.py` is a perf smoke test marked `perf` — skipped by default via `addopts = "-m 'not perf'"` in `pyproject.toml`, run via `make test-perf`. Catches order-of-magnitude regressions in the per-feature pipeline.

## Docs site

https://elfpkck.github.io/parallelizer/ is deployed by `.github/workflows/pages.yaml` from what `tools/stage_site.sh` stages into `build/site` (sources in `site/`, home page from `README.md`), not from the repo root. See `DEVELOPMENT.md` for what is staged and local preview.

## Packaging & Remote Debugging

See `DEVELOPMENT.md` for the plugin-portal zip command (must zip *only* `PolygonsParallelToLine/`, not the repo root) and the PyCharm `pydevd_pycharm.settrace` setup (port `53100`, commented stubs in `src/pptl.py` and `tests/test_main_functionality.py`).
