from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import threading
from typing import TYPE_CHECKING, ClassVar

import pytest
from qgis.PyQt.QtTest import QSignalSpy  # type: ignore[import-not-found]

from PolygonsParallelToLine.src import contact, diagnostics, report_sender
from PolygonsParallelToLine.src.report_sender import ReportSender, build_payload

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Receiver(BaseHTTPRequestHandler):
    status = 201
    body = b'{"id": 42}'
    received: ClassVar[list[tuple[str, dict]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).received.append((self.headers["Content-Type"], json.loads(self.rfile.read(length))))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def receiver(monkeypatch) -> Iterator[type[_Receiver]]:
    _Receiver.status, _Receiver.body, _Receiver.received = 201, b'{"id": 42}', []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(contact, "REPORT_ENDPOINT", f"http://127.0.0.1:{server.server_port}/")
    yield _Receiver
    server.shutdown()
    server.server_close()


def _send(report: str, title: str = "") -> tuple[bool, str]:
    sender = ReportSender()
    spy = QSignalSpy(sender.finished)
    sender.send(report, title)
    assert spy.wait(10_000), "the sender never finished"
    ok, message = spy[0]
    return ok, message


def test_payload_has_schema_versions_and_report():
    payload = json.loads(build_payload("the report", "Bug: ValueError"))

    assert payload["schema"] == 1
    assert payload["title"] == "Bug: ValueError"
    assert payload["report"] == "the report"
    assert payload["qgis_version"]
    assert payload["plugin_version"]


def test_payload_caps_the_report_dropping_oldest_log_lines():
    log_lines = [f"  log line {i:05d} " + "x" * 100 for i in range(2000)]
    report = "\n".join(["Environment", "", diagnostics.LOG_SECTION, *log_lines])

    fitted = json.loads(build_payload(report))["report"]

    assert len(fitted.encode("utf-8")) <= report_sender.MAX_REPORT_BYTES
    assert diagnostics.TRUNCATED_NOTE in fitted
    assert "log line 01999" in fitted
    assert "log line 00000" not in fitted


def test_send_posts_json_and_reports_the_reference(qgis_app, receiver):
    ok, message = _send("the report", "Bug: ValueError")

    assert ok
    assert message == "Sent, thank you. Reference: 42"
    content_type, payload = receiver.received[0]
    assert content_type == "application/json"
    assert payload["report"] == "the report"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "too many reports"), (500, "status 500")],
)
def test_send_failure_statuses(qgis_app, receiver, status, expected):
    receiver.status, receiver.body = status, b'{"error": "nope"}'

    ok, message = _send("the report")

    assert not ok
    assert expected in message


def test_send_unreachable_service(qgis_app, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(contact, "REPORT_ENDPOINT", f"http://127.0.0.1:{port}/")

    ok, message = _send("the report")

    assert not ok
    assert "could not be reached" in message
