from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from qgis.PyQt.QtCore import QUrl  # type: ignore[import-not-found]
from qgis.PyQt.QtGui import QDesktopServices, QFontDatabase  # type: ignore[import-not-found]
from qgis.PyQt.QtWidgets import (  # type: ignore[import-not-found]
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from . import contact
from .diagnostics import LOG_SECTION, issue_url, mailto_url, plugin_metadata
from .report_sender import ReportSender

if TYPE_CHECKING:
    from qgis.PyQt.QtWidgets import QWidget  # type: ignore[import-not-found]

_INTRO = (
    "This report helps the developer find and fix the problem. It contains software versions, active plugins, "
    "the plugin's settings, the last error, a description of the layers, project and features the last few "
    "operations worked on (types, coordinate systems, counts, validity; no names or coordinates), recent "
    "Parallelizer log lines and, if QGIS crashed or froze last time, the Python stacks it left. Paths to your "
    "home and QGIS profile folders have been replaced automatically. "
    "Please check the text and edit or delete anything you don't want to share."
)


def intro_text() -> str:
    """What the report holds, then one sentence per available way to share it."""
    email, endpoint = contact.REPORT_EMAIL, contact.REPORT_ENDPOINT
    sentences = ["Nothing is sent automatically; every button below first copies the full report to the clipboard."]
    if endpoint:
        sentences.append(
            '"Send report" sends the text above to the developer\'s report service (a Cloudflare Worker that files '
            "it privately on GitHub); your IP address is used only for rate limiting and is not stored. "
            f"Privacy note: {privacy_url()}."
        )
    sentences.append(
        '"Open GitHub issue" opens a pre-filled issue in your browser; it is only created if you submit it, and '
        "GitHub issues are public."
    )
    if email:
        sentences.append(f'"Send by email" opens a pre-filled message to {email} in your mail app.')
    sentences.append("If a pre-filled text ends with a truncation note, paste the full report from the clipboard.")
    where = f"an issue at {plugin_metadata('tracker')}" + (f" or an email to {email}" if email else "")
    sentences.append(f"You can also paste the copied report into {where}.")
    return f"{_INTRO}\n\n{' '.join(sentences)}"


def privacy_url() -> str:
    return f"{plugin_metadata('homepage').rstrip('/')}/privacy.html"


class ReportDialog(QDialog):
    def __init__(
        self,
        report: str,
        title: str = "",
        parent: QWidget | None = None,
        *,
        geometries: Callable[[], str | None] | None = None,
    ) -> None:
        """``geometries`` builds the opt-in geometry block; it is only called if the user ticks the box."""
        super().__init__(parent)
        self._issue_title = title
        self._build_geometries = geometries
        self._geometry_block: str | None = None
        self.setWindowTitle("Parallelizer: Report a problem")

        intro = QLabel(intro_text())
        intro.setWordWrap(True)

        self.text_edit = QPlainTextEdit(report)
        self.text_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.geometry_checkbox = QCheckBox(
            "Include the geometries involved, moved so the first reference vertex is at 0 0 "
            "(shapes and relative positions are kept, the real location is not)"
        )
        self.geometry_checkbox.setEnabled(geometries is not None)
        self.geometry_checkbox.toggled.connect(self._toggle_geometries)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.send_button = button_box.addButton("Send report", QDialogButtonBox.ButtonRole.ActionRole)
        self.send_button.setVisible(bool(contact.REPORT_ENDPOINT))
        self.send_button.setDefault(bool(contact.REPORT_ENDPOINT))
        self.send_button.clicked.connect(self.send_report)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self._sender = ReportSender(self)
        self._sender.finished.connect(self._on_sent)
        copy_button = button_box.addButton("Copy to clipboard", QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(self.copy_to_clipboard)
        issue_button = button_box.addButton("Open GitHub issue…", QDialogButtonBox.ButtonRole.ActionRole)
        issue_button.clicked.connect(self.open_issue)
        if contact.REPORT_EMAIL:
            email_button = button_box.addButton("Send by email…", QDialogButtonBox.ButtonRole.ActionRole)
            email_button.clicked.connect(self.send_email)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addWidget(self.geometry_checkbox)
        layout.addWidget(self.text_edit)
        layout.addWidget(self.status_label)
        layout.addWidget(button_box)
        self.setLayout(layout)
        self.resize(760, 560)

    def _toggle_geometries(self, checked: bool) -> None:  # noqa: FBT001
        if self._geometry_block is None and self._build_geometries is not None:
            self._geometry_block = self._build_geometries()
        if self._geometry_block is None:
            return
        text = self.report_text()
        block = f"\n{self._geometry_block}\n"
        if checked and block not in text:
            # Before the log lines, so they are what gets dropped first when a link is too long.
            index = text.find(f"\n{LOG_SECTION}\n")
            index = len(text) if index == -1 else index
            text = f"{text[:index]}{block}{text[index:]}"
        elif not checked:
            text = text.replace(block, "")
        self.text_edit.setPlainText(text)

    def report_text(self) -> str:
        return str(self.text_edit.toPlainText())

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.report_text())

    def open_issue(self) -> None:
        self._open_with_full_copy(issue_url(self.report_text(), self._issue_title))

    def send_report(self) -> None:
        self.copy_to_clipboard()
        self.send_button.setEnabled(False)
        self._show_status("Sending…")
        self._sender.send(self.report_text(), self._issue_title)

    def _on_sent(self, ok: bool, message: str) -> None:  # noqa: FBT001
        if ok:
            self._show_status(message)
            return
        self._show_status(f"{message} The full report is on your clipboard; you can also use the other buttons.")
        self.send_button.setEnabled(True)

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.show()

    def send_email(self) -> None:
        self._open_with_full_copy(mailto_url(self.report_text(), self._issue_title))

    def _open_with_full_copy(self, url: str) -> None:
        # Copied first, in case the link had to be truncated to fit its length limit.
        self.copy_to_clipboard()
        QDesktopServices.openUrl(QUrl.fromEncoded(url.encode("ascii")))
