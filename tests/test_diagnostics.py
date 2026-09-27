from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from qgis.core import Qgis, QgsApplication, QgsGeometry, QgsPointXY, QgsProcessingException

from PolygonsParallelToLine.src import contact, diagnostics
from PolygonsParallelToLine.src.const import PLUGIN_DIR
from PolygonsParallelToLine.src.settings import MapToolSettings


@pytest.fixture
def emissions():
    received: list[None] = []

    def on_error() -> None:
        received.append(None)

    diagnostics.notifier.error_recorded.connect(on_error)
    yield received
    diagnostics.notifier.error_recorded.disconnect(on_error)


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


def test_scrub_replaces_home_profile_and_plugin_paths():
    home = str(Path.home())
    profile = QgsApplication.qgisSettingsDirPath().rstrip("/")
    text = f'File "{PLUGIN_DIR}/src/map_tool.py"\nFile "{profile}/x.py"\nFile "{home}/data/y.py"'

    scrubbed = diagnostics.scrub(text)

    assert 'File "<plugin>/src/map_tool.py"' in scrubbed
    assert 'File "~/data/y.py"' in scrubbed
    assert str(PLUGIN_DIR) not in scrubbed
    if profile != home:
        assert 'File "<profile>/x.py"' in scrubbed


def test_scrub_handles_windows_separators():
    home_backslash = str(Path.home()).replace("/", "\\")

    assert diagnostics.scrub(f"{home_backslash}\\data\\y.py") == "~\\data\\y.py"


def test_scrub_replaces_coordinates_but_keeps_versions():
    text = "Self-intersection at or near point 523301.2 6720011.8; QGIS 3.40.1, GEOS 3.13.1-CAPI-1.19.2"

    scrubbed = diagnostics.scrub(text)

    assert "523301.2" not in scrubbed
    assert "<coordinates>" in scrubbed
    assert "QGIS 3.40.1, GEOS 3.13.1-CAPI-1.19.2" in scrubbed


def test_capture_errors_records_and_reraises(emissions):
    @diagnostics.capture_errors
    def broken() -> NoReturn:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        broken()

    assert "ValueError: boom" in (diagnostics.last_error() or "")
    assert diagnostics.issue_title() == "Bug: ValueError"
    assert len(emissions) == 1


def test_recorded_error_is_scrubbed_and_titled_by_type(tmp_path):
    diagnostics.setup_logging(tmp_path)

    @diagnostics.capture_errors
    def broken() -> NoReturn:
        msg = f"first line\nnear {PLUGIN_DIR}: 523301.2 6720011.8"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="first line"):
        broken()

    for handler in diagnostics.logger.handlers:
        handler.flush()
    assert diagnostics.issue_title() == "Bug: ValueError"
    for text in (diagnostics.last_error() or "", (tmp_path / diagnostics.LOG_FILE_NAME).read_text(encoding="utf-8")):
        assert str(PLUGIN_DIR) not in text
        assert "523301.2" not in text


def test_capture_errors_records_nested_error_once(emissions):
    @diagnostics.capture_errors
    def inner() -> NoReturn:
        msg = "boom"
        raise RuntimeError(msg)

    @diagnostics.capture_errors
    def outer() -> None:
        inner()

    with pytest.raises(RuntimeError):
        outer()

    assert len(emissions) == 1


def test_capture_errors_passes_user_input_errors_through():
    @diagnostics.capture_errors
    def user_error() -> NoReturn:
        msg = "Target layer is empty"
        raise diagnostics.UserInputError(msg)

    with pytest.raises(QgsProcessingException):
        user_error()

    assert diagnostics.last_error() is None
    assert diagnostics.issue_title() == ""


def test_capture_errors_records_internal_processing_exceptions():
    @diagnostics.capture_errors
    def internal_error() -> NoReturn:
        msg = "Closest vertex not found in the outline of target 7"
        raise QgsProcessingException(msg)

    with pytest.raises(QgsProcessingException):
        internal_error()

    assert "Closest vertex not found" in (diagnostics.last_error() or "")


def test_setup_logging_is_idempotent_and_writes_file(tmp_path):
    diagnostics.setup_logging(tmp_path)
    diagnostics.setup_logging(tmp_path)
    diagnostics.logger.info("hello from test")
    for handler in diagnostics.logger.handlers:
        handler.flush()

    file_handlers = [h for h in diagnostics.logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    log_file = tmp_path / diagnostics.LOG_FILE_NAME
    assert diagnostics.log_file_path() == log_file
    content = log_file.read_text(encoding="utf-8")
    assert "Session start" in content
    assert "hello from test" in content

    diagnostics.teardown_logging()
    assert diagnostics.logger.handlers == []
    assert diagnostics.log_file_path() is None


def test_qgis_log_gets_levels_but_not_session_start(tmp_path, monkeypatch):
    received: list[tuple[str, str, object]] = []

    class FakeMessageLog:
        @staticmethod
        def logMessage(message, tag, level) -> None:  # noqa: N802
            received.append((message, tag, level))

    monkeypatch.setattr(diagnostics, "QgsMessageLog", FakeMessageLog)
    diagnostics.setup_logging(tmp_path)
    diagnostics.logger.info("info line")
    diagnostics.logger.warning("warning line")

    assert received == [
        ("info line", diagnostics.LOG_TAG, Qgis.MessageLevel.Info),
        ("warning line", diagnostics.LOG_TAG, Qgis.MessageLevel.Warning),
    ]


@pytest.mark.usefixtures("isolate_settings")
def test_build_report_contents(tmp_path):
    diagnostics.setup_logging(tmp_path)
    diagnostics.logger.info("marker log line")

    @diagnostics.capture_errors
    def broken() -> NoReturn:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        broken()

    report = diagnostics.build_report(MapToolSettings())

    assert "  QGIS: " in report
    assert "  by_longest: False" in report
    assert "ValueError: boom" in report
    assert "<plugin>" in report
    assert str(PLUGIN_DIR) not in report
    assert report.index(diagnostics.LOG_SECTION) < report.index("marker log line")


def test_build_report_without_settings_or_errors():
    report = diagnostics.build_report()

    assert "Map tool settings" not in report
    assert "Last error in this session\n  none" in report


def test_issue_url_prefills_issue_form():
    url = diagnostics.issue_url("line one\nline two", "Bug: ValueError")

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://github.com/elfpkck/parallelizer/issues/new"
    assert _query(url) == {
        "template": "bug_report.yml",
        "title": "Bug: ValueError",
        "diagnostics": "line one\nline two",
    }


def test_issue_url_drops_oldest_log_lines_to_fit():
    log_lines = [f"  log line {i:04d} " + "x" * 60 for i in range(500)]
    report = "\n".join(["Environment", "  QGIS: 4.2", "", diagnostics.LOG_SECTION, *log_lines])

    url = diagnostics.issue_url(report)

    body = _query(url)["diagnostics"]
    assert len(url) <= 7000
    assert body.startswith("Environment\n  QGIS: 4.2")
    assert diagnostics.TRUNCATED_NOTE in body
    assert "log line 0000" not in body
    assert "log line 0499" in body


def test_issue_url_truncates_report_without_log_section():
    url = diagnostics.issue_url("y" * 20000)

    assert len(url) <= 7000
    assert _query(url)["diagnostics"].endswith(diagnostics.TRUNCATED_NOTE)


def test_logger_does_not_propagate_to_root(tmp_path, caplog):
    diagnostics.setup_logging(tmp_path)
    with caplog.at_level(logging.INFO):
        diagnostics.logger.info("stays in the plugin log")

    assert "stays in the plugin log" not in caplog.text


def test_operation_keeps_failed_and_last_snapshot():
    def failing_operation() -> None:
        with diagnostics.operation("first") as op:
            op.add("Section", ["line"])
            op.add_trace("Math")["delta"] = 12.5
            msg = "boom"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        failing_operation()
    with diagnostics.operation("second"):
        pass

    report = diagnostics.build_report()

    assert "Operation that failed\n  failed: first (failed in" in report
    assert "    delta: 12.5" in report
    assert "Recent operations (newest first)\n  #1: second (finished in" in report
    assert diagnostics.current_operation().name == "untracked"


def test_report_lists_environment_extras():
    report = diagnostics.build_report()

    assert "  Locale: " in report
    assert "  Screen: " in report
    assert "Active plugins" in report


def test_geometry_block_moves_geometries_to_local_origin():
    with diagnostics.operation("rotate") as op:
        op.add_geometry("reference", QgsGeometry.fromWkt("LineString (1000 2000, 1010 2000)"))
        op.add_geometry("target 1", QgsGeometry.fromWkt("Polygon ((1000 2005, 1004 2005, 1004 2009, 1000 2005))"))

    block = diagnostics.geometry_block() or ""

    assert block.startswith(diagnostics.GEOMETRY_SECTION)
    assert "reference: LineString (0 0, 10 0)" in block
    assert "target 1: Polygon ((0 5, 4 5, 4 9, 0 5))" in block
    assert "1000" not in block


def test_geometry_block_omits_huge_geometries_and_is_none_without_geometries():
    assert diagnostics.geometry_block() is None
    huge = QgsGeometry.fromPolylineXY([QgsPointXY(i, 0) for i in range(1500)])
    with diagnostics.operation("rotate") as op:
        op.add_geometry("reference", huge)

    assert "omitted, more than 1000 vertices" in (diagnostics.geometry_block() or "")


def test_keeps_last_operations_drops_idle_and_never_loses_the_failure():
    def failing() -> None:
        with diagnostics.operation("broken"):
            msg = "boom"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        failing()
    for number in range(7):
        with diagnostics.operation(f"op {number}"):
            pass
    with diagnostics.operation("click on nothing", idle=True):
        pass

    report = diagnostics.build_report()

    assert "  failed: broken (failed" in report
    assert "  #1: op 6" in report
    assert "  #5: op 2" in report
    assert "op 1" not in report
    assert "click on nothing" not in report


def test_geometry_block_uses_one_origin_across_operations():
    with diagnostics.operation("set reference") as op:
        op.add_geometry("reference", QgsGeometry.fromWkt("LineString (1000 2000, 1010 2000)"))
    with diagnostics.operation("rotate") as op:
        op.add_geometry("target 1", QgsGeometry.fromWkt("Point (1005 2003)"))

    block = diagnostics.geometry_block() or ""

    assert "  #1: rotate\n    target 1: Point (5 3)" in block
    assert "  #2: set reference\n    reference: LineString (0 0, 10 0)" in block


def _write_dump(log_dir: Path, name: str, frame_file: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text(
        f'Fatal Python error: Segmentation fault\n\nCurrent thread:\n  File "{frame_file}", line 12 in rotate\n',
        encoding="utf-8",
    )


def test_previous_crash_in_plugin_code_is_reported(tmp_path):
    _write_dump(tmp_path, "crash.log", f"{PLUGIN_DIR}/src/map_tool.py")

    diagnostics.setup_logging(tmp_path)
    report = diagnostics.build_report()

    assert (tmp_path / "crash-previous.log").exists()
    assert (tmp_path / "crash.log").read_text() == ""
    assert "Crash in the previous session (Python stacks)" in report
    assert 'File "<plugin>/src/map_tool.py", line 12 in rotate' in report
    assert "closed unexpectedly" in (diagnostics.previous_session_problem() or "")


def test_previous_crash_elsewhere_is_only_noted(tmp_path):
    _write_dump(tmp_path, "hang.log", "/usr/share/qgis/python/other.py")

    diagnostics.setup_logging(tmp_path)
    report = diagnostics.build_report()

    assert "Slow or frozen operation in the previous session\n  it happened outside Parallelizer code" in report
    assert "other.py" not in report
    assert diagnostics.previous_session_problem() is None


class FakeFaulthandler:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.calls: list[tuple] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def enable(self, file, *, all_threads: bool) -> None:
        self.calls.append(("enable", Path(file.name).name, all_threads))

    def disable(self) -> None:
        self.calls.append(("disable",))

    def dump_traceback_later(self, timeout, *, repeat: bool, file) -> None:
        self.calls.append(("arm", timeout, repeat, Path(file.name).name))

    def cancel_dump_traceback_later(self) -> None:
        self.calls.append(("cancel",))


def test_faulthandler_is_enabled_into_crash_log_and_disabled_on_teardown(tmp_path, monkeypatch):
    fake = FakeFaulthandler(enabled=False)
    monkeypatch.setattr(diagnostics, "faulthandler", fake)

    diagnostics.setup_logging(tmp_path)
    diagnostics.teardown_logging()

    assert fake.calls[0] == ("enable", "crash.log", True)
    assert ("disable",) in fake.calls


def test_faulthandler_owned_by_someone_else_is_left_alone(tmp_path, monkeypatch):
    fake = FakeFaulthandler(enabled=True)
    monkeypatch.setattr(diagnostics, "faulthandler", fake)

    diagnostics.setup_logging(tmp_path)
    diagnostics.teardown_logging()

    assert not any(call[0] in {"enable", "disable"} for call in fake.calls)


def test_watchdog_arms_hang_dump_only_when_asked(tmp_path, monkeypatch):
    fake = FakeFaulthandler(enabled=True)
    monkeypatch.setattr(diagnostics, "faulthandler", fake)
    diagnostics.setup_logging(tmp_path)

    with diagnostics.operation("processing run"):
        pass
    assert fake.calls == []

    with diagnostics.operation("map tool", watchdog=True):
        pass
    assert fake.calls == [("arm", diagnostics.HANG_TIMEOUT_S, False, "hang.log"), ("cancel",)]


@pytest.mark.usefixtures("isolate_settings")
def test_previous_qgis_crash_dump_is_reported_once(tmp_path, monkeypatch):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(diagnostics.tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(diagnostics, "faulthandler", FakeFaulthandler(enabled=True))
    _write_dump(temp, "qgis-python-crash-info-4242", f"{PLUGIN_DIR}/src/map_tool.py")
    (temp / "qgis-python-crash-info-4243").touch()  # another instance still running: empty

    diagnostics.setup_logging(tmp_path / "logs")
    first = diagnostics.build_report()
    diagnostics.teardown_logging()
    diagnostics.setup_logging(tmp_path / "logs")

    assert "Crash in the previous session (Python stacks)" in first
    assert 'File "<plugin>/src/map_tool.py", line 12 in rotate' in first
    assert "Crash in the previous session" not in diagnostics.build_report()


def test_mailto_url_prefills_subject_and_body(monkeypatch):
    monkeypatch.setattr(contact, "REPORT_EMAIL", "reports@example.org")
    url = diagnostics.mailto_url("line one\nline two + more", "Bug: ValueError")

    fields = _query(url)
    assert url.startswith("mailto:reports@example.org?")
    assert fields["subject"] == "Parallelizer problem report: Bug: ValueError"
    assert fields["body"].endswith("line one\r\nline two + more")
    assert "+" not in url  # RFC 6068: spaces are %20, a literal plus is %2B


def test_mailto_url_truncates_to_mail_client_limit():
    log_lines = [f"  log line {i:04d} " + "x" * 60 for i in range(200)]
    report = "\n".join(["Environment", "  QGIS: 4.2", "", diagnostics.LOG_SECTION, *log_lines])

    url = diagnostics.mailto_url(report)

    body = unquote(url.partition("&body=")[2])
    assert len(url) <= 1900
    assert diagnostics.TRUNCATED_NOTE in body
    assert "log line 0199" in body
    assert "log line 0000" not in body
