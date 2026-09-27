from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from qgis.PyQt.QtWidgets import QApplication, QLabel, QPushButton  # type: ignore[import-not-found]

from PolygonsParallelToLine.src import contact, report_dialog
from PolygonsParallelToLine.src.diagnostics import GEOMETRY_SECTION, LOG_SECTION
from PolygonsParallelToLine.src.report_dialog import ReportDialog


@pytest.fixture
def opened_urls(monkeypatch) -> list[str]:
    """URLs the dialog asks the desktop to open, fully encoded; nothing is really opened."""
    opened: list[str] = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(url) -> bool:  # noqa: N802
            opened.append(bytes(url.toEncoded()).decode())
            return True

    monkeypatch.setattr(report_dialog, "QDesktopServices", FakeDesktopServices)
    return opened


def test_copy_uses_edited_text(qgis_app):
    dialog = ReportDialog("original report", "Bug: ValueError")
    dialog.text_edit.setPlainText("edited report")

    dialog.copy_to_clipboard()

    assert QApplication.clipboard().text() == "edited report"


def test_open_issue_builds_url_from_edited_text(qgis_app, opened_urls):
    dialog = ReportDialog("original report", "Bug: ValueError")
    dialog.text_edit.setPlainText("edited report")

    dialog.open_issue()

    assert len(opened_urls) == 1
    query = parse_qs(urlsplit(opened_urls[0]).query)
    assert query["diagnostics"] == ["edited report"]
    assert query["title"] == ["Bug: ValueError"]
    assert QApplication.clipboard().text() == "edited report"


def test_geometry_checkbox_inserts_block_before_log_lines_and_removes_it(qgis_app):
    report = f"Environment\n  QGIS: 4\n\n{LOG_SECTION}\n  log line"
    dialog = ReportDialog(report, geometries=lambda: f"{GEOMETRY_SECTION}\n  reference: LineString (0 0, 10 0)")

    dialog.geometry_checkbox.setChecked(True)
    with_geometries = dialog.report_text()
    dialog.geometry_checkbox.setChecked(False)

    assert with_geometries.index(GEOMETRY_SECTION) < with_geometries.index(LOG_SECTION)
    assert dialog.report_text() == report


def test_geometry_checkbox_disabled_without_geometries(qgis_app):
    dialog = ReportDialog("report")

    assert not dialog.geometry_checkbox.isEnabled()
    assert not dialog.geometry_checkbox.isChecked()


EMAIL = "reports@example.org"


def test_send_email_opens_mailto_from_edited_text_and_copies_full_report(qgis_app, monkeypatch, opened_urls):
    monkeypatch.setattr(contact, "REPORT_EMAIL", EMAIL)
    dialog = ReportDialog("original report", "Bug: ValueError")
    dialog.text_edit.setPlainText("edited report")

    dialog.send_email()

    assert len(opened_urls) == 1
    assert opened_urls[0].startswith(f"mailto:{EMAIL}?subject=")
    assert "edited%20report" in opened_urls[0]
    assert QApplication.clipboard().text() == "edited report"


def _buttons_and_intro(dialog: ReportDialog) -> tuple[list[str], str]:
    buttons = [button.text() for button in dialog.findChildren(QPushButton) if not button.isHidden()]
    return buttons, dialog.findChildren(QLabel)[0].text()


def test_dialog_offers_email_and_says_where_to_send_when_address_is_set(qgis_app, monkeypatch):
    monkeypatch.setattr(contact, "REPORT_EMAIL", EMAIL)

    buttons, intro = _buttons_and_intro(ReportDialog("report"))

    assert "Send by email…" in buttons
    assert EMAIL in intro
    assert "https://github.com/elfpkck/parallelizer/issues" in intro


def test_dialog_hides_email_when_address_is_not_set(qgis_app, monkeypatch):
    monkeypatch.setattr(contact, "REPORT_EMAIL", "")

    buttons, intro = _buttons_and_intro(ReportDialog("report"))

    assert "Send by email…" not in buttons
    assert "email" not in intro
    assert "https://github.com/elfpkck/parallelizer/issues" in intro


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, report: str, title: str = "") -> None:
        self.sent.append((report, title))


def test_send_report_button_only_with_an_endpoint(qgis_app, monkeypatch):
    monkeypatch.setattr(contact, "REPORT_ENDPOINT", "")
    buttons, intro = _buttons_and_intro(ReportDialog("report"))
    assert "Send report" not in buttons
    assert "Cloudflare" not in intro

    monkeypatch.setattr(contact, "REPORT_ENDPOINT", "https://reports.example.org/")
    buttons, intro = _buttons_and_intro(ReportDialog("report"))
    assert "Send report" in buttons
    assert "not stored" in intro
    assert "https://elfpkck.github.io/parallelizer/privacy.html" in intro


def test_send_report_sends_edited_text_and_stays_disabled_after_success(qgis_app, monkeypatch):
    monkeypatch.setattr(contact, "REPORT_ENDPOINT", "https://reports.example.org/")
    dialog = ReportDialog("original report", "Bug: ValueError")
    fake = FakeSender()
    dialog._sender = fake  # noqa: SLF001
    dialog.text_edit.setPlainText("edited report")

    dialog.send_report()

    assert fake.sent == [("edited report", "Bug: ValueError")]
    assert not dialog.send_button.isEnabled()
    assert QApplication.clipboard().text() == "edited report"
    dialog._on_sent(True, "Sent, thank you. Reference: 7")  # noqa: SLF001, FBT003
    assert not dialog.send_button.isEnabled()
    assert dialog.status_label.text() == "Sent, thank you. Reference: 7"


def test_failed_send_explains_fallbacks_and_allows_retry(qgis_app, monkeypatch):
    monkeypatch.setattr(contact, "REPORT_ENDPOINT", "https://reports.example.org/")
    dialog = ReportDialog("report")
    dialog._sender = FakeSender()  # noqa: SLF001

    dialog.send_report()
    dialog._on_sent(False, "Not sent: the report service could not be reached (timeout).")  # noqa: SLF001, FBT003

    assert dialog.send_button.isEnabled()
    assert "on your clipboard" in dialog.status_label.text()
