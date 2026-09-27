"""
Local log and a problem report the user reviews and shares themselves; nothing here sends data anywhere.

Privacy rule for every log call in the plugin: no layer names, file paths, attribute values or coordinates.
"""

from __future__ import annotations

import collections
import configparser
import contextlib
import dataclasses
import faulthandler
import functools
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import tempfile
import threading
import time
import traceback
from typing import IO, TYPE_CHECKING, Any, Callable, TypeVar, cast
from urllib.parse import quote, urlencode

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMessageLog,
    QgsProcessingException,
    QgsProject,
    QgsProjUtils,
)
from qgis.PyQt.QtCore import (  # type: ignore[import-not-found]
    PYQT_VERSION_STR,
    QObject,
    QSettings,
    pyqtSignal,
    qVersion,
)
import qgis.utils  # type: ignore[import-not-found]

try:
    from osgeo import gdal  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - GDAL ships with every QGIS build
    gdal = None

from . import contact
from .const import PLUGIN_DIR

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .settings import MapToolSettings

LOG_TAG = "Parallelizer"
LOG_FILE_NAME = "parallelizer.log"
# Python stack dumps written by faulthandler, kept in the log folder; moved to "<kind>-previous.log" next session.
_DUMPS = {
    "crash": ("crash.log", "Crash in the previous session"),
    "hang": ("hang.log", "Slow or frozen operation in the previous session"),
}
HANG_TIMEOUT_S = 30.0
_KEY_LAST_QGIS_CRASH = "PolygonsParallelToLine/diagnostics/last_qgis_crash_mtime"
ISSUE_TEMPLATE = "bug_report.yml"
LOG_SECTION = "Recent log lines (oldest first)"
GEOMETRY_SECTION = "Geometries (moved so the first reference vertex is at 0 0; real location removed)"
TRUNCATED_NOTE = "(truncated; the full report is on your clipboard)"

_LOG_MAX_BYTES = 256 * 1024
_REPORT_LOG_LINES = 100
# GitHub rejects issue URLs somewhere above 8 KB.
_MAX_URL_LENGTH = 7000
# Mail clients accept much shorter links: on Windows a mailto: link handed to the default mail app can be cut
# at about 2,000 characters.
_MAX_MAILTO_LENGTH = 1900
_MAIL_PROMPT = "Please describe what you did, what you expected and what happened instead:\n\n\n"
_RECORDED_ATTR = "_parallelizer_recorded"
_MAX_OPERATION_GEOMETRIES = 12
MAX_TRACED_FEATURES = 5
MAX_KEPT_OPERATIONS = 5
MAX_WKT_VERTICES = 1000
# Two decimal numbers in a row, e.g. "523301.2 6720011.8" in a GEOS message or a WKT fragment.
_COORDINATE_PAIR = re.compile(r"-?\d+\.\d+(?:[ ,]+-?\d+\.\d+)+")

logger = logging.getLogger("parallelizer")

F = TypeVar("F", bound=Callable[..., Any])


class UserInputError(QgsProcessingException):
    """A problem with the user's input (e.g. an empty layer), not a plugin bug: shown, but not recorded."""


class _ErrorNotifier(QObject):
    error_recorded = pyqtSignal()


# Created on import, in the main thread, so an emit from the Processing worker thread reaches
# queued connections in the main thread.
notifier = _ErrorNotifier()


@dataclasses.dataclass
class _State:
    handlers: list[logging.Handler] = dataclasses.field(default_factory=list)
    file_handler: RotatingFileHandler | None = None
    last_error: str | None = None
    last_error_type: str | None = None
    # Newest first. A failed operation is also kept on its own, so later actions never push it out.
    operations: collections.deque[Operation] = dataclasses.field(
        default_factory=lambda: collections.deque(maxlen=MAX_KEPT_OPERATIONS)
    )
    failed_operation: Operation | None = None
    # Guards operations and failed_operation: a Processing run finishes in its worker thread.
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    dump_files: dict[str, IO[str]] = dataclasses.field(default_factory=dict)
    owns_faulthandler: bool = False
    # Dumps found at startup, i.e. written by the previous session: kind -> text.
    previous_dumps: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Operation:
    """What one map tool action or Processing run worked on, kept for the report."""

    name: str
    # A dict section is rendered when the report is built, so values filled in before a failure still show.
    sections: list[tuple[str, list[str] | dict[str, Any]]] = dataclasses.field(default_factory=list)
    # None stands for a geometry too large for the report; it is not kept in memory.
    geometries: list[tuple[str, QgsGeometry | None]] = dataclasses.field(default_factory=list)
    # All geometries are kept in the CRS of the first one recorded with a CRS (layers can differ).
    crs: QgsCoordinateReferenceSystem | None = None
    traced_features: int = 0
    duration_s: float | None = None
    failed: bool = False
    # An idle operation (e.g. a click on nothing) is not kept unless it fails.
    idle: bool = False

    def describe_as(self, name: str) -> None:
        self.name = name
        self.idle = False

    def add(self, title: str, lines: list[str] | dict[str, Any]) -> None:
        self.sections.append((title, lines))

    def add_trace(self, title: str) -> dict[str, Any]:
        trace: dict[str, Any] = {}
        self.add(title, trace)
        return trace

    def add_geometry(self, role: str, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem | None = None) -> None:
        if len(self.geometries) >= _MAX_OPERATION_GEOMETRIES or geom.isNull():
            return
        if geom.constGet().nCoordinates() > MAX_WKT_VERTICES:
            self.geometries.append((role, None))
            return
        kept = QgsGeometry(geom)
        if crs is not None and crs.isValid():
            if self.crs is None:
                self.crs = crs
            else:
                _transform(kept, crs, self.crs)
        self.geometries.append((role, kept))

    def report_lines(self, label: str) -> list[str]:
        status = "failed" if self.failed else "finished"
        duration = f" in {self.duration_s:.3f} s" if self.duration_s is not None else ""
        lines = [f"  {label}: {self.name} ({status}{duration})"]
        for title, section in self.sections:
            lines.append(f"  {title}")
            if isinstance(section, dict):
                section = [f"{k}: {v:.6g}" if isinstance(v, float) else f"{k}: {v}" for k, v in section.items()]  # noqa: PLW2901
            lines.extend(f"    {line}" for line in section or ["(nothing recorded)"])
        return lines


_state = _State()
# Per thread, because a Processing run can overlap with map tool use in the main thread.
_current = threading.local()


class _QgsMessageLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            level = Qgis.MessageLevel.Critical
        elif record.levelno >= logging.WARNING:
            level = Qgis.MessageLevel.Warning
        else:
            level = Qgis.MessageLevel.Info
        try:
            QgsMessageLog.logMessage(self.format(record), LOG_TAG, level)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def default_log_dir() -> Path:
    return Path(QgsApplication.qgisSettingsDirPath()) / "parallelizer"


def _add_handler(handler: logging.Handler, fmt: str) -> None:
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    _state.handlers.append(handler)


def setup_logging(log_dir: Path | None = None) -> None:
    if _state.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_file = (log_dir or default_log_dir()) / LOG_FILE_NAME
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=_LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
    except OSError:
        pass
    else:
        _add_handler(file_handler, "%(asctime)s %(levelname)s %(message)s")
        _state.file_handler = file_handler
        # Logged before the QGIS handler is attached, so it only marks sessions in the file.
        logger.info("Session start: Parallelizer %s, QGIS %s", plugin_metadata("version"), Qgis.version())
        _setup_dumps(log_file.parent)

    _add_handler(_QgsMessageLogHandler(), "%(message)s")


def _setup_dumps(log_dir: Path) -> None:
    """
    Collect the previous session's crash/hang dumps, then arm faulthandler for this session. A hard crash
    (e.g. a segfault in Qt) kills Python before any except clause runs; faulthandler still writes the
    Python stacks. QGIS usually owns faulthandler already (its crash dialog shows those stacks), so it is
    left alone and QGIS's own dump file is read in the next session instead.
    """
    with contextlib.suppress(OSError):
        qgis_crash = _unseen_qgis_crash_dump()
        if qgis_crash:
            _state.previous_dumps["crash"] = qgis_crash
    try:
        for kind, (name, _) in _DUMPS.items():
            path = log_dir / name
            if path.exists() and path.stat().st_size:
                _state.previous_dumps[kind] = path.read_text(encoding="utf-8", errors="replace")
                path.replace(log_dir / f"{kind}-previous.log")
            _state.dump_files[kind] = path.open("w", encoding="utf-8")
        # Someone else (QGIS, another plugin, pytest) already owns it: don't redirect their dumps.
        if not faulthandler.is_enabled():
            faulthandler.enable(_state.dump_files["crash"], all_threads=True)
            _state.owns_faulthandler = True
    except OSError:
        _close_dumps()


def _unseen_qgis_crash_dump() -> str | None:
    """
    The newest non-empty Python crash dump QGIS left in the temp folder
    (``QgsCrashHandler::sPythonCrashLogFile``) since the last check; each crash is reported once.
    """
    settings = QSettings()
    last_seen = float(settings.value(_KEY_LAST_QGIS_CRASH, 0.0, type=float))
    own = f"qgis-python-crash-info-{os.getpid()}"
    dumps = [
        (stat.st_mtime, path)
        for path in Path(tempfile.gettempdir()).glob("qgis-python-crash-info-*")
        if path.name != own and (stat := path.stat()).st_size
    ]
    if not dumps:
        return None
    mtime, newest = max(dumps)
    settings.setValue(_KEY_LAST_QGIS_CRASH, mtime)
    return newest.read_text(encoding="utf-8", errors="replace") if mtime > last_seen else None


def _close_dumps() -> None:
    if _state.owns_faulthandler:
        faulthandler.disable()
        _state.owns_faulthandler = False
    faulthandler.cancel_dump_traceback_later()
    for file in _state.dump_files.values():
        file.close()
    _state.dump_files.clear()


def teardown_logging() -> None:
    for handler in _state.handlers:
        logger.removeHandler(handler)
        handler.close()
    _state.handlers.clear()
    _state.file_handler = None
    _close_dumps()
    _state.previous_dumps.clear()
    _state.last_error = None
    _state.last_error_type = None
    with _state.lock:
        _state.operations.clear()
        _state.failed_operation = None


def log_file_path() -> Path | None:
    return Path(_state.file_handler.baseFilename) if _state.file_handler else None


def record_error(exc: BaseException) -> None:
    # An error raised through nested decorated calls (e.g. keyPressEvent -> deactivate) is recorded once.
    if getattr(exc, _RECORDED_ATTR, False):
        return
    with contextlib.suppress(Exception):
        setattr(exc, _RECORDED_ATTR, True)
    # Diagnostics must never mask the error being reported.
    with contextlib.suppress(Exception):
        # Scrubbed before logging: a traceback carries file paths, and its message may carry coordinates.
        _state.last_error = scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
        _state.last_error_type = type(exc).__name__
        logger.error("Unexpected error\n%s", _state.last_error)
        notifier.error_recorded.emit()


def last_error() -> str | None:
    return _state.last_error


@contextlib.contextmanager
def operation(name: str, *, idle: bool = False, watchdog: bool = False) -> Iterator[Operation]:
    """
    ``watchdog`` dumps the Python stacks to hang.log if the operation runs longer than HANG_TIMEOUT_S. Only
    for map tool actions: Processing runs can legitimately take long and have their own cancel button.
    """
    op = Operation(name, idle=idle)
    _current.operation = op
    hang_file = _state.dump_files.get("hang") if watchdog else None
    if hang_file is not None:
        faulthandler.dump_traceback_later(HANG_TIMEOUT_S, repeat=False, file=hang_file)
    start = time.perf_counter()
    try:
        yield op
    except Exception:
        op.failed = True
        with _state.lock:
            _state.failed_operation = op
        raise
    finally:
        if hang_file is not None:
            faulthandler.cancel_dump_traceback_later()
        op.duration_s = time.perf_counter() - start
        _current.operation = None
        if op.failed or not op.idle:
            with _state.lock:
                _state.operations.appendleft(op)


def current_operation() -> Operation:
    """The operation running in this thread, or a throwaway one so callers need no checks."""
    return getattr(_current, "operation", None) or Operation("untracked")


def capture_errors(func: F) -> F:
    """Record unexpected exceptions for the report, then re-raise so QGIS still shows its own error."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        try:
            return func(*args, **kwargs)
        except UserInputError:
            raise
        except Exception as exc:
            record_error(exc)
            raise

    return cast("F", wrapper)


@functools.cache
def _metadata() -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return dict(parser["general"]) if parser.has_section("general") else {}


def plugin_metadata(key: str) -> str:
    return _metadata().get(key, "")


def scrub(text: str) -> str:
    """Best-effort removal of personal paths and coordinates; the user still reviews the result."""
    replacements = {
        str(PLUGIN_DIR): "<plugin>",
        QgsApplication.qgisSettingsDirPath(): "<profile>",
        str(Path.home()): "~",
    }
    # Longest first: the plugin dir usually sits inside the profile dir, which sits inside home.
    for raw_path, placeholder in sorted(replacements.items(), key=lambda item: -len(item[0])):
        path = raw_path.rstrip("/\\")
        if len(path) < 3:  # noqa: PLR2004 - "/" or "C:" would replace every separator
            continue
        for variant in {path, path.replace("\\", "/"), path.replace("/", "\\")}:
            text = re.sub(re.escape(variant), placeholder, text, flags=re.IGNORECASE)
    return _COORDINATE_PAIR.sub("<coordinates>", text)


def environment() -> dict[str, str]:
    return {
        "Plugin": plugin_metadata("version") or "unknown",
        "QGIS": Qgis.version(),
        "Qt": qVersion(),
        "PyQt": PYQT_VERSION_STR,
        "Python": platform.python_version(),
        "GEOS": Qgis.geosVersion(),
        "PROJ": f"{QgsProjUtils.projVersionMajor()}.{QgsProjUtils.projVersionMinor()}",
        "GDAL": gdal.__version__ if gdal is not None else "unknown",
        "OS": " ".join(part for part in (platform.system(), platform.release(), platform.machine()) if part),
        "Locale": QgsApplication.locale(),
        "Screen": _screen_description(),
    }


def _screen_description() -> str:
    screen = QgsApplication.primaryScreen() if QgsApplication.instance() is not None else None
    if screen is None:
        return "unknown"
    return f"scale {screen.devicePixelRatio():g}, {screen.logicalDotsPerInch():g} dpi"


def active_plugins() -> list[str]:
    return [f"{name} {qgis.utils.pluginMetadata(name, 'version')}" for name in sorted(qgis.utils.active_plugins)]


def _recent_log_lines() -> list[str]:
    log_file = log_file_path()
    if log_file is None:
        return []
    try:
        with log_file.open(encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in collections.deque(f, maxlen=_REPORT_LOG_LINES)]
    except OSError:
        return []


def build_report(settings: MapToolSettings | None = None) -> str:
    lines = ["Parallelizer diagnostics report", "", "Environment"]
    lines.extend(f"  {name}: {value}" for name, value in environment().items())

    if settings is not None:
        lines.extend(
            [
                "",
                "Map tool settings",
                f"  by_longest: {settings.by_longest}",
                f"  pick_reference_segment: {settings.pick_reference_segment}",
                f"  pick_target_segment: {settings.pick_target_segment}",
            ]
        )

    lines.extend(["", "Active plugins", *_indented("\n".join(active_plugins()))])
    lines.extend(["", "Last error in this session", *_indented(_state.last_error)])
    failed = _state.failed_operation
    if failed is not None:
        lines.extend(["", "Operation that failed", *scrub("\n".join(failed.report_lines("failed"))).splitlines()])
    recent = [line for label, op in _listed_operations() if op is not failed for line in op.report_lines(label)]
    if recent:
        lines.extend(["", "Recent operations (newest first)", *scrub("\n".join(recent)).splitlines()])
    for kind, text in _state.previous_dumps.items():
        title = _DUMPS[kind][1]
        if _mentions_plugin(text):
            lines.extend(["", f"{title} (Python stacks)", *_indented(text)])
        else:
            lines.extend(["", title, "  it happened outside Parallelizer code"])
    lines.extend(["", LOG_SECTION, *_indented("\n".join(_recent_log_lines()))])
    return "\n".join(lines)


def _mentions_plugin(dump: str) -> bool:
    return str(PLUGIN_DIR) in dump


def previous_session_problem() -> str | None:
    """A message-bar text when the previous session crashed or froze inside Parallelizer code."""
    if _mentions_plugin(_state.previous_dumps.get("crash", "")):
        return "QGIS closed unexpectedly last time while Parallelizer was working."
    if _mentions_plugin(_state.previous_dumps.get("hang", "")):
        return f"Parallelizer was busy for over {HANG_TIMEOUT_S:g} s last time (QGIS may have frozen)."
    return None


def _listed_operations() -> list[tuple[str, Operation]]:
    """The failed operation (label "failed") first, then the other kept ones, newest first, labeled #1, #2..."""
    with _state.lock:
        failed = _state.failed_operation
        operations = list(_state.operations)
    listed = [("failed", failed)] if failed is not None else []
    recent = [op for op in operations if op is not failed]
    listed.extend((f"#{number}", op) for number, op in enumerate(recent, start=1))
    return listed


def has_geometries() -> bool:
    return any(op.geometries for _, op in _listed_operations())


def geometry_block() -> str | None:
    """
    The geometries of the listed operations as WKT, all moved by one offset so the first geometry (the
    reference) sits at 0 0: shapes and relative positions stay, also across operations; the location does not.
    """
    listed = [(label, op) for label, op in _listed_operations() if op.geometries]
    if not listed:
        return None
    kept = [(role, geom, op.crs) for _, op in listed for role, geom in op.geometries if geom is not None]
    first = next((item for item in kept if item[0].startswith("reference")), kept[0] if kept else None)
    origin = next(first[1].vertices(), None) if first is not None else None
    origin_crs = first[2] if first is not None else None
    lines = [GEOMETRY_SECTION]
    for label, op in listed:
        lines.append(f"  {label}: {op.name}")
        for role, geom in op.geometries:
            if geom is None or origin is None:
                lines.append(f"    {role}: omitted, more than {MAX_WKT_VERTICES} vertices; attach sample data instead")
                continue
            shifted = QgsGeometry(geom)
            if op.crs is not None and origin_crs is not None:
                _transform(shifted, op.crs, origin_crs)
            shifted.translate(-origin.x(), -origin.y())
            lines.append(f"    {role}: {shifted.asWkt(6)}")
    return "\n".join(lines)


def _transform(geom: QgsGeometry, source: QgsCoordinateReferenceSystem, dest: QgsCoordinateReferenceSystem) -> None:
    if source != dest:
        geom.transform(QgsCoordinateTransform(source, dest, QgsProject.instance()))


def _indented(text: str | None) -> list[str]:
    if not text:
        return ["  none"]
    return [f"  {line}" for line in scrub(text).splitlines()]


def issue_title() -> str:
    return f"Bug: {_state.last_error_type}" if _state.last_error_type else ""


def _longest_fitting(n: int, fits: Callable[[int], bool]) -> int:
    """Largest k in [0, n] with fits(k), assuming fits is monotonic and fits(0) holds."""
    low, high = 0, n
    while low < high:
        mid = (low + high + 1) // 2
        if fits(mid):
            low = mid
        else:
            high = mid - 1
    return low


def fit_report(report: str, fits: Callable[[str], bool], max_chars: int) -> str:
    """
    ``report``, or with the oldest log lines dropped (then the end cut) until ``fits`` accepts it. Every probe
    measures the whole candidate, so the final cut starts from at most ``max_chars`` characters (``fits`` never
    accepts more than that anyway).
    """
    if fits(report):
        return report

    lines = report.splitlines()
    start = lines.index(LOG_SECTION) + 1 if LOG_SECTION in lines else len(lines)
    head, logs = lines[:start], lines[start:]

    def with_logs(keep: int) -> str:
        return "\n".join([*head, f"  {TRUNCATED_NOTE}", *logs[len(logs) - keep :]])

    keep = _longest_fitting(len(logs), lambda k: fits(with_logs(k)))
    if fits(text := with_logs(keep)):
        return text

    joined = "\n".join(head)[:max_chars]
    chars = _longest_fitting(len(joined), lambda n: fits(f"{joined[:n]}\n{TRUNCATED_NOTE}"))
    return f"{joined[:chars]}\n{TRUNCATED_NOTE}"


def _fitted_url(report: str, build: Callable[[str], str], limit: int) -> str:
    return build(fit_report(report, lambda body: len(build(body)) <= limit, limit))


def issue_url(report: str, title: str = "") -> str:
    """GitHub new-issue URL with the report pre-filled; oldest log lines are dropped to fit the URL limit."""
    base = plugin_metadata("tracker").rstrip("/") + "/new"

    def build(body: str) -> str:
        return f"{base}?{urlencode({'template': ISSUE_TEMPLATE, 'title': title, 'diagnostics': body})}"

    return _fitted_url(report, build, _MAX_URL_LENGTH)


def mailto_url(report: str, title: str = "") -> str:
    """A mailto: link to ``contact.REPORT_EMAIL`` with the report pre-filled, cut to what mail clients accept."""
    subject = f"Parallelizer problem report: {title}" if title else "Parallelizer problem report"

    def build(body: str) -> str:
        # RFC 6068: percent-encode everything (a "+" is not a space here) and use CRLF line breaks.
        encoded = quote(f"{_MAIL_PROMPT}{body}".replace("\n", "\r\n"), safe="")
        return f"mailto:{contact.REPORT_EMAIL}?subject={quote(subject, safe='')}&body={encoded}"

    return _fitted_url(report, build, _MAX_MAILTO_LENGTH)
