"""One-click sending of a reviewed report to the receiver in ``contact.REPORT_ENDPOINT`` (see tools/report_worker)."""

from __future__ import annotations

import functools
import json
from typing import TYPE_CHECKING

from qgis.core import Qgis, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QByteArray, QObject, QUrl, pyqtSignal  # type: ignore[import-not-found]
from qgis.PyQt.QtNetwork import QNetworkRequest  # type: ignore[import-not-found]

from . import contact
from .diagnostics import fit_report, logger, plugin_metadata

if TYPE_CHECKING:
    from qgis.PyQt.QtNetwork import QNetworkReply  # type: ignore[import-not-found]

SCHEMA_VERSION = 1
# The receiver rejects bodies over 64 KB; the rest of the payload is small.
MAX_REPORT_BYTES = 60_000
TIMEOUT_MS = 15_000
_CREATED = 201
_TOO_MANY_REQUESTS = 429


def build_payload(report: str, title: str = "") -> bytes:
    fitted = fit_report(report, lambda text: len(text.encode("utf-8")) <= MAX_REPORT_BYTES, MAX_REPORT_BYTES)
    payload = {
        "schema": SCHEMA_VERSION,
        "title": title,
        "plugin_version": plugin_metadata("version") or "unknown",
        "qgis_version": Qgis.version(),
        "report": fitted,
    }
    return json.dumps(payload).encode("utf-8")


class ReportSender(QObject):
    """Posts one report; ``finished(ok, message)`` carries a message ready to show to the user."""

    finished = pyqtSignal(bool, str)

    def send(self, report: str, title: str = "") -> None:
        request = QNetworkRequest(QUrl(contact.REPORT_ENDPOINT))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(TIMEOUT_MS)
        # QGIS's manager applies the user's proxy and SSL settings.
        reply = QgsNetworkAccessManager.instance().post(request, QByteArray(build_payload(report, title)))
        reply.finished.connect(functools.partial(self._on_finished, reply))

    def _on_finished(self, reply: QNetworkReply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll())
        error = reply.errorString()
        reply.deleteLater()

        if status == _CREATED:
            reference = _reference(body)
            logger.info("Report sent (reference %s)", reference)
            self.finished.emit(True, f"Sent, thank you. Reference: {reference}")  # noqa: FBT003
            return
        if status == _TOO_MANY_REQUESTS:
            reason = "too many reports were sent from your network recently; please try again later"
        elif status:
            reason = f"the report service answered with status {status}"
        else:
            reason = f"the report service could not be reached ({error})"
        logger.warning("Report sending failed: %s", reason)
        self.finished.emit(False, f"Not sent: {reason}.")  # noqa: FBT003


def _reference(body: bytes) -> str:
    try:
        return str(json.loads(body)["id"])
    except (ValueError, KeyError, TypeError):
        return "unknown"
