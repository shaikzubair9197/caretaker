"""
Meeting prep overlay UI — PySide6 replacement for the Tkinter version.

Fullscreen Fluent/Windows 11-style QDialog that surfaces precomputed meeting
context ~15 minutes before a meeting.  All data comes from the deterministic
/meeting/prep API — no LLM, no masking.

Entry point preserved (identical to the previous Tkinter version):
    python ui/meeting_prep_popup.py              # next meeting in the 15-min window
    python ui/meeting_prep_popup.py --force      # soonest meeting (demo, any time)
    python ui/meeting_prep_popup.py --event <id> # a specific event
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QEasingCurve,
    QEvent,
    QModelIndex,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from dotenv import load_dotenv

# ── Path setup ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui.document_cache import DocumentCache
from ui.document_controller import DocumentController
from ui.document_session import DocumentSession
from ui.theme import build_stylesheet, current_theme

load_dotenv()

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}


# ── Fetch worker ───────────────────────────────────────────────────────────────

class _FetchWorker(QThread):
    result = Signal(dict)

    def __init__(self, force: bool, event_id: str | None) -> None:
        super().__init__()
        self._force = force
        self._event_id = event_id

    def run(self) -> None:
        try:
            import requests
            if self._event_id:
                url = f"{API_BASE}/meeting/prep/{self._event_id}"
                params: dict = {}
            else:
                url = f"{API_BASE}/meeting/prep/next"
                params = {"force": "true"} if self._force else {}
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            self.result.emit(resp.json())
        except Exception as exc:
            self.result.emit({"_error": str(exc)})


# ── Attendee list model ────────────────────────────────────────────────────────

class AttendeeListModel(QAbstractListModel):
    def __init__(self, attendees: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._data = attendees

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._data)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._data):
            return None
        att = self._data[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return att.get("name") or att.get("address", "")
        if role == Qt.ItemDataRole.UserRole:
            return att  # full dict for delegate
        return None

    def rowData(self, row: int) -> dict:
        return self._data[row] if row < len(self._data) else {}


class AttendeeDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: ARG002
        painter.save()
        att = index.data(Qt.ItemDataRole.UserRole) or {}
        name = att.get("name") or att.get("address", "")
        response = att.get("response", "none").lower()

        r = option.rect
        is_selected = bool(option.state & QStyle.State_Selected)
        bg = "#21262d" if is_selected else "transparent"
        painter.fillRect(r, QColor(bg))

        # Name
        painter.setPen(QColor("#e6edf3"))
        painter.setFont(QFont("Inter", 12))
        name_rect = QRect(r.left() + 12, r.top(), r.width() - 80, r.height())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)

        # RSVP badge
        badge_colors = {
            "accepted": ("#3fb950", "#0d1117"),
            "declined": ("#f85149", "#0d1117"),
            "tentativelyaccepted": ("#d29922", "#0d1117"),
        }
        badge_label = {
            "accepted": "✓",
            "declined": "✗",
            "tentativelyaccepted": "?",
        }.get(response, "")
        if badge_label:
            bg_c, fg_c = badge_colors.get(response, ("#30363d", "#8b949e"))
            bw, bh = 20, 20
            bx = r.right() - bw - 8
            by = r.top() + (r.height() - bh) // 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(bg_c)))
            painter.drawRoundedRect(QRect(bx, by, bw, bh), 10, 10)
            painter.setPen(QColor(fg_c))
            painter.setFont(QFont("Inter", 9, QFont.Weight.Bold))
            painter.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, badge_label)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: ARG002
        return QSize(option.rect.width(), 36)


# ── Document list model ────────────────────────────────────────────────────────

class DocumentListModel(QAbstractListModel):
    def __init__(self, items: list[dict], confidence: str, parent=None) -> None:
        super().__init__(parent)
        self._items = items
        self._confidence = confidence
        self._thumbnails: dict[int, QPixmap] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.get("label", "")
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.UserRole + 1:
            return self._confidence
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbnails.get(index.row())
        return None

    def set_thumbnail(self, row: int, pixmap: QPixmap) -> None:
        self._thumbnails[row] = pixmap
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class DocumentCardDelegate(QStyledItemDelegate):
    open_requested = Signal(str, str)  # url, label

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover_row = -1

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        item = index.data(Qt.ItemDataRole.UserRole) or {}
        confidence = index.data(Qt.ItemDataRole.UserRole + 1) or ""
        label = item.get("label", "")
        reason = item.get("reason", "")
        is_high = confidence == "HIGH"

        r = option.rect
        is_hover = index.row() == self._hover_row

        bg = "#21262d" if is_hover else "#161b22"
        painter.fillRect(r.adjusted(2, 2, -2, -2), QColor(bg))
        border_col = "#58a6ff" if is_high else "#30363d"
        pen = QPen(QColor(border_col), 1)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(r.adjusted(2, 2, -2, -2)), 6, 6)

        # File icon
        painter.setPen(QColor("#58a6ff"))
        painter.setFont(QFont("Inter", 13))
        painter.drawText(QRect(r.left() + 10, r.top(), 24, r.height()), Qt.AlignmentFlag.AlignVCenter, "📄")

        # Label
        lbl_font = QFont("Inter", 12)
        if is_high:
            lbl_font.setBold(True)
        painter.setFont(lbl_font)
        painter.setPen(QColor("#e6edf3"))
        lbl_rect = QRect(r.left() + 38, r.top(), r.width() - 120, r.height() // 2 + 2)
        painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignVCenter, label)

        # Reason tag
        if reason:
            painter.setFont(QFont("Inter", 10))
            painter.setPen(QColor("#58a6ff"))
            reason_rect = QRect(r.left() + 38, r.top() + r.height() // 2 - 2, r.width() - 120, r.height() // 2)
            painter.drawText(reason_rect, Qt.AlignmentFlag.AlignVCenter, reason)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: ARG002
        return QSize(option.rect.width(), 60)

    def editorEvent(self, event, model, option, index):  # noqa: ARG002
        if event.type() == QEvent.Type.MouseButtonRelease:
            item = index.data(Qt.ItemDataRole.UserRole) or {}
            url = item.get("url", "")
            label = item.get("label", "")
            if url:
                self.open_requested.emit(url, label)
                return True
        return False


# ── Email card widget ──────────────────────────────────────────────────────────

class _EmailCard(QFrame):
    preview_requested = Signal(dict)

    def __init__(self, email: dict, parent=None) -> None:
        super().__init__(parent)
        self._email = email
        self.setProperty("class", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        subject = QLabel(email.get("subject", "(no subject)"))
        subject.setWordWrap(True)
        subject.setStyleSheet("QLabel { font-size: 13px; font-weight: 600; color: #e6edf3; }")
        layout.addWidget(subject)

        meta_parts = [p for p in (email.get("from"), email.get("received")) if p]
        if meta_parts:
            meta = QLabel("  ·  ".join(meta_parts))
            meta.setStyleSheet("QLabel { font-size: 11px; color: #8b949e; }")
            layout.addWidget(meta)

        if email.get("reason"):
            reason = QLabel(email["reason"])
            reason.setStyleSheet(
                "QLabel { font-size: 11px; color: #58a6ff; border: 1px solid #30363d;"
                " border-radius: 4px; padding: 1px 6px; }"
            )
            layout.addWidget(reason, alignment=Qt.AlignmentFlag.AlignLeft)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        preview_btn = QPushButton("Preview")
        preview_btn.setProperty("class", "ghost")
        preview_btn.clicked.connect(lambda: self.preview_requested.emit(self._email))
        btn_row.addWidget(preview_btn)
        layout.addLayout(btn_row)

    def mousePressEvent(self, _event) -> None:
        self.preview_requested.emit(self._email)

    def enterEvent(self, _event) -> None:
        self.setStyleSheet(
            "QFrame { background-color: #21262d; border: 1px solid #30363d; border-radius: 8px; }"
        )

    def leaveEvent(self, _event) -> None:
        self.setStyleSheet(
            "QFrame { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; }"
        )


# ── Email preview dialog ───────────────────────────────────────────────────────

class _EmailPreviewDialog(QDialog):
    def __init__(self, email: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(email.get("subject", "Email preview"))
        self.resize(760, 520)
        from ui.theme import build_stylesheet, current_theme
        self.setStyleSheet(build_stylesheet(current_theme()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        subject_label = QLabel(email.get("subject", "(no subject)"))
        subject_label.setWordWrap(True)
        subject_label.setStyleSheet(
            "QLabel { font-size: 18px; font-weight: 700; color: #e6edf3; }"
        )
        layout.addWidget(subject_label)

        meta_parts = [p for p in (email.get("from"), email.get("received")) if p]
        if meta_parts:
            meta = QLabel("  ·  ".join(meta_parts))
            meta.setStyleSheet("QLabel { font-size: 12px; color: #8b949e; }")
            layout.addWidget(meta)

        if email.get("reason"):
            reason = QLabel(email["reason"])
            reason.setStyleSheet(
                "QLabel { font-size: 11px; color: #58a6ff; border: 1px solid #30363d;"
                " border-radius: 4px; padding: 2px 8px; }"
            )
            layout.addWidget(reason, alignment=Qt.AlignmentFlag.AlignLeft)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(email.get("body_preview") or "No preview available.")
        body.setStyleSheet(
            "QTextEdit { background-color: #161b22; color: #e6edf3;"
            " border: none; padding: 12px; font-size: 12px; }"
        )
        layout.addWidget(body)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)


# ── Inline viewer area ─────────────────────────────────────────────────────────

class _InlineViewerArea(QWidget):
    """
    Expands from 0 to target height with an animation when a document is opened.
    Shows loading or the actual viewer.
    """

    def __init__(self, target_height: int = 420, parent=None) -> None:
        super().__init__(parent)
        self._target_h = target_height
        self._current_viewer = None
        self._current_url = ""
        self._current_label = ""
        self._current_page = 0
        self._total_pages = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 8)
        toolbar_layout.setSpacing(6)

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.setProperty("class", "ghost")
        self._prev_btn.setFixedWidth(110)
        self._prev_btn.clicked.connect(self._page_prev)
        toolbar_layout.addWidget(self._prev_btn)

        self._page_label = QLabel("0 / 0")
        self._page_label.setStyleSheet("QLabel { color: #8b949e; font-size: 12px; font-weight: 600; }")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setFixedWidth(70)
        toolbar_layout.addWidget(self._page_label)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setProperty("class", "ghost")
        self._next_btn.setFixedWidth(110)
        self._next_btn.clicked.connect(self._page_next)
        toolbar_layout.addWidget(self._next_btn)

        toolbar_layout.addStretch()

        self._open_btn = QPushButton("Open full view")
        self._open_btn.setProperty("class", "ghost")
        self._open_btn.clicked.connect(self._open_full_view)
        toolbar_layout.addWidget(self._open_btn)

        layout.addWidget(toolbar)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Placeholder / loading
        self._loading = QLabel("Click a document to preview it here")
        self._loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading.setStyleSheet("QLabel { color: #8b949e; font-size: 13px; }")
        self._stack.addWidget(self._loading)   # index 0

        self._anim = QPropertyAnimation(self, b"maximumHeight")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setMaximumHeight(40)
        self._set_navigation_visible(False)

    def set_source(self, url: str, label: str) -> None:
        self._current_url = url
        self._current_label = label

    def show_loading(self, hint: str = "") -> None:
        self._disconnect_viewer()
        if self._stack.count() > 1:
            old = self._stack.widget(1)
            self._stack.removeWidget(old)
            old.deleteLater()
        self._loading.setText(f"Loading {hint}…" if hint else "Loading…")
        self._stack.setCurrentIndex(0)
        self._set_navigation_visible(False)
        self._expand()

    def show_viewer(self, viewer: QWidget) -> None:
        self._disconnect_viewer()
        self._current_viewer = viewer
        self._current_page = 0
        self._total_pages = 0
        if self._stack.count() > 1:
            old = self._stack.widget(1)
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.insertWidget(1, viewer)
        self._stack.setCurrentIndex(1)
        self._set_navigation_visible(hasattr(viewer, "go_to_page"))
        if hasattr(viewer, "page_changed"):
            viewer.page_changed.connect(self._on_page_changed)
        self._page_label.setText("1 / 1")
        self._expand()

    def _disconnect_viewer(self) -> None:
        if self._current_viewer is None:
            return
        if hasattr(self._current_viewer, "page_changed"):
            try:
                self._current_viewer.page_changed.disconnect(self._on_page_changed)
            except Exception:
                pass
        self._current_viewer = None

    def _set_navigation_visible(self, visible: bool) -> None:
        self._prev_btn.setVisible(visible)
        self._next_btn.setVisible(visible)
        self._page_label.setVisible(visible)
        self._open_btn.setVisible(bool(self._current_url))

    def _on_page_changed(self, current: int, total: int) -> None:
        self._current_page = current
        self._total_pages = total
        self._page_label.setText(f"{current + 1} / {total}")

    def _page_prev(self) -> None:
        if self._current_viewer is not None and hasattr(self._current_viewer, "go_to_page"):
            self._current_viewer.go_to_page(max(0, self._current_page - 1))

    def _page_next(self) -> None:
        if self._current_viewer is not None and hasattr(self._current_viewer, "go_to_page"):
            self._current_viewer.go_to_page(min(self._total_pages - 1, self._current_page + 1))

    def _open_full_view(self) -> None:
        if not self._current_url:
            return
        from ui.document_viewer_popup import DocumentViewerPopup
        dlg = DocumentViewerPopup(self, self._current_url, _HEADERS, self._current_label)
        dlg.exec()

    def _expand(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(self._target_h)
        self._anim.start()


# ── Header panel ───────────────────────────────────────────────────────────────

class _HeaderPanel(QFrame):
    def __init__(self, snapshot: dict, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 16)
        layout.setSpacing(4)

        mins = snapshot.get("minutes_until")
        countdown_text = (
            f"Meeting starts in {mins} minutes" if mins is not None else "Upcoming meeting"
        )
        countdown = QLabel(countdown_text)
        countdown.setProperty("class", "countdown")
        layout.addWidget(countdown)

        title = QLabel(snapshot.get("title", "(no subject)"))
        title.setProperty("class", "title")
        title.setWordWrap(True)
        layout.addWidget(title)

        meta_parts = []
        if snapshot.get("start_display"):
            meta_parts.append(snapshot["start_display"])
        org = (snapshot.get("organizer") or {}).get("name")
        if org:
            meta_parts.append(f"Organized by {org}")
        if meta_parts:
            meta = QLabel("   ·   ".join(meta_parts))
            meta.setProperty("class", "meta")
            layout.addWidget(meta)


# ── Action bar ────────────────────────────────────────────────────────────────

class _ActionBar(QFrame):
    join_clicked = Signal(str)
    dismiss_clicked = Signal()

    def __init__(self, join_url: str | None, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(48, 12, 48, 20)
        layout.addStretch()

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(self.dismiss_clicked)
        layout.addWidget(dismiss_btn)

        if join_url:
            join_btn = QPushButton("Join Meeting")
            join_btn.setProperty("class", "accent")
            join_btn.clicked.connect(lambda: self.join_clicked.emit(join_url))
            layout.addWidget(join_btn)
        else:
            no_link = QLabel("No online meeting link found")
            no_link.setProperty("class", "meta")
            layout.addWidget(no_link)


# ── Documents panel ────────────────────────────────────────────────────────────

class _DocumentsPanel(QWidget):
    document_open_requested = Signal(str, str)  # url, label

    def __init__(
        self,
        docs: dict,
        controller: DocumentController,
        cache: DocumentCache,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._cache = cache
        self._items = docs.get("items") or []
        self._confidence = docs.get("confidence", "NONE")
        self._url_to_row: dict[str, int] = {
            item.get("url", ""): i for i, item in enumerate(self._items)
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        if self._confidence == "HIGH":
            heading = QLabel("CONFIRMED DOCUMENT")
        elif self._confidence == "LOW":
            heading = QLabel("RELATED DOCUMENTS")
        else:
            heading = QLabel("DOCUMENTS")
        heading.setProperty("class", "section-header")
        layout.addWidget(heading)

        if not self._items:
            no_docs = QLabel("No documents or attachments found for this meeting.")
            no_docs.setProperty("class", "meta")
            no_docs.setWordWrap(True)
            layout.addWidget(no_docs)
        else:
            self._model = DocumentListModel(self._items, self._confidence)
            self._doc_delegate = DocumentCardDelegate()
            self._doc_delegate.open_requested.connect(self._on_open_requested)

            self._list_view = QListView()
            self._list_view.setModel(self._model)
            self._list_view.setItemDelegate(self._doc_delegate)
            self._list_view.setSpacing(4)
            self._list_view.setFixedHeight(min(len(self._items) * 68, 220))
            layout.addWidget(self._list_view)

        # Inline viewer area
        self._viewer_area = _InlineViewerArea(target_height=420)
        layout.addWidget(self._viewer_area)
        layout.addStretch()

        # Connect controller signals
        self._controller.document_ready.connect(self._on_document_ready)
        self._controller.thumbnail_ready.connect(self._on_thumbnail_ready)

    def _on_open_requested(self, url: str, label: str) -> None:
        full_url = url if not url.startswith("/") else API_BASE.rstrip("/") + url
        self._viewer_area.set_source(full_url, label)
        self._viewer_area.show_loading(label)
        self._controller.open(full_url, _HEADERS, label)
        self.document_open_requested.emit(full_url, label)

    def _on_document_ready(self, _url: str, viewer, _session: DocumentSession) -> None:
        self._viewer_area.show_viewer(viewer)

    def _on_thumbnail_ready(self, url: str, image) -> None:
        if hasattr(self, "_model"):
            pixmap = QPixmap.fromImage(image) if isinstance(image, QImage) else image
            rel = url.replace(API_BASE.rstrip("/"), "") if url.startswith(API_BASE) else url
            row = self._url_to_row.get(url) or self._url_to_row.get(rel)
            if row is not None:
                self._model.set_thumbnail(row, pixmap)


# ── Main dialog ────────────────────────────────────────────────────────────────

class MeetingPrepDialog(QDialog):
    def __init__(
        self,
        force: bool = False,
        event_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._force = force
        self._event_id = event_id
        self._snapshot: dict | None = None
        self._theme = current_theme()

        # Shared document cache & controller (lives for dialog lifetime)
        self._doc_cache = DocumentCache()
        self._doc_controller = DocumentController(cache=self._doc_cache, parent=self)

        self._setup_window()
        self._build_loading_ui()
        self._start_fetch()

    # ── Window setup ──────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowTitle("Caretaker — Meeting Prep")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(build_stylesheet(self._theme))
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_fade_started", False):
            self._fade_started = True
            # Fade in after the window is fully shown
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity")
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
            self._fade_anim = anim  # keep reference

    # ── Loading state ─────────────────────────────────────────────────────────

    def _build_loading_ui(self) -> None:
        self._root_stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._root_stack)

        loading = QWidget()
        vl = QVBoxLayout(loading)
        vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("Loading meeting details…")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("QLabel { color: #8b949e; font-size: 16px; }")
        vl.addWidget(lbl)
        hint = QLabel("Press Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("QLabel { color: #30363d; font-size: 12px; }")
        vl.addWidget(hint)
        self._root_stack.addWidget(loading)  # index 0: loading

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _start_fetch(self) -> None:
        self._worker = _FetchWorker(self._force, self._event_id)
        self._worker.result.connect(self._on_fetch_result)
        self._worker.start()

    def _on_fetch_result(self, payload: dict) -> None:
        error = payload.get("_error")
        if error:
            self._show_error(error)
            return
        snapshot = payload.get("event")
        if not snapshot:
            self._show_error("No meeting needs prep right now.")
            return
        self._snapshot = snapshot
        self._build_content_ui(snapshot)
        # Prefetch high-confidence documents in background
        self._prefetch_docs(snapshot)

    def _show_error(self, msg: str) -> None:
        err = QWidget()
        vl = QVBoxLayout(err)
        vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(msg)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("QLabel { color: #f85149; font-size: 14px; }")
        vl.addWidget(lbl)
        hint = QLabel("Press Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("QLabel { color: #8b949e; font-size: 12px; margin-top: 8px; }")
        vl.addWidget(hint)
        self._root_stack.addWidget(err)
        self._root_stack.setCurrentIndex(self._root_stack.count() - 1)

    # ── Content UI ────────────────────────────────────────────────────────────

    def _build_content_ui(self, s: dict) -> None:
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = _HeaderPanel(s)
        outer.addWidget(header)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame { color: #30363d; }")
        outer.addWidget(sep)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #30363d; }")

        # LEFT pane: tabs
        left = QTabWidget()
        left.setMinimumWidth(380)
        left.tabBar().setExpanding(False)

        # Agenda tab
        agenda_text = (s.get("agenda") or "").strip()
        agenda_widget = QTextEdit()
        agenda_widget.setReadOnly(True)
        agenda_widget.setHtml(
            f"<div style='font-size:13px; color:#e6edf3; line-height:1.6; padding:4px;'>"
            + (agenda_text.replace("\n", "<br>") if agenda_text else
               "<span style='color:#8b949e'>No agenda provided.</span>")
            + "</div>"
        )
        agenda_widget.setStyleSheet(
            "QTextEdit { background-color: #161b22; border: none; padding: 16px; }"
        )
        left.addTab(agenda_widget, "Agenda")

        # Attendees tab
        attendees = s.get("attendees") or []
        att_view = QListView()
        att_model = AttendeeListModel(attendees)
        att_view.setModel(att_model)
        att_view.setItemDelegate(AttendeeDelegate())
        att_view.setStyleSheet("QListView { background-color: #161b22; border: none; }")
        att_tab = QWidget()
        att_tab_layout = QVBoxLayout(att_tab)
        att_tab_layout.setContentsMargins(0, 0, 0, 0)
        count_label = QLabel(f"Attendees ({len(attendees)})")
        count_label.setProperty("class", "section-header")
        count_label.setContentsMargins(12, 8, 0, 4)
        att_tab_layout.addWidget(count_label)
        att_tab_layout.addWidget(att_view)
        left.addTab(att_tab, f"Attendees ({len(attendees)})")

        # Emails tab
        emails = s.get("related_emails") or []
        email_scroll = QScrollArea()
        email_scroll.setWidgetResizable(True)
        email_scroll.setStyleSheet(
            "QScrollArea { background-color: #161b22; border: none; }"
        )
        email_container = QWidget()
        email_layout = QVBoxLayout(email_container)
        email_layout.setContentsMargins(8, 8, 8, 8)
        email_layout.setSpacing(8)
        for em in emails:
            card = _EmailCard(em)
            card.preview_requested.connect(self._on_email_preview)
            email_layout.addWidget(card)
        if not emails:
            no_email = QLabel("No related emails found.")
            no_email.setProperty("class", "meta")
            no_email.setAlignment(Qt.AlignmentFlag.AlignCenter)
            email_layout.addWidget(no_email)
        email_layout.addStretch()
        email_scroll.setWidget(email_container)
        left.addTab(email_scroll, f"Emails ({len(emails)})")

        splitter.addWidget(left)

        # RIGHT pane: documents + inline viewer
        docs = s.get("documents") or {}
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setStyleSheet(
            "QScrollArea { background-color: #0d1117; border: none; }"
        )
        right_scroll.setMinimumWidth(420)

        self._docs_panel = _DocumentsPanel(
            docs, self._doc_controller, self._doc_cache  # noqa: SIM117
        )
        right_scroll.setWidget(self._docs_panel)
        splitter.addWidget(right_scroll)

        splitter.setSizes([480, 560])
        outer.addWidget(splitter, stretch=1)

        # Action bar
        action_bar = _ActionBar(s.get("join_url"))
        action_bar.join_clicked.connect(self._join_meeting)
        action_bar.dismiss_clicked.connect(self.close)
        outer.addWidget(action_bar)

        self._root_stack.addWidget(content)
        self._root_stack.setCurrentIndex(self._root_stack.count() - 1)

    # ── Prefetch ──────────────────────────────────────────────────────────────

    def _prefetch_docs(self, snapshot: dict) -> None:
        docs = snapshot.get("documents") or {}
        items = docs.get("items") or []
        confidence = docs.get("confidence", "NONE")
        for item in items:
            url = item.get("url", "")
            if not url:
                continue
            full_url = url if not url.startswith("/") else API_BASE.rstrip("/") + url
            label = item.get("label", "")
            self._doc_controller.prefetch(full_url, _HEADERS, label)

    # ── Interactions ──────────────────────────────────────────────────────────

    def _on_email_preview(self, email: dict) -> None:
        dlg = _EmailPreviewDialog(email, self)
        dlg.exec()

    def _join_meeting(self, url: str) -> None:
        self.close()
        _open_url(url)


# ── URL opener (platform-aware) ────────────────────────────────────────────────

def _open_url(url: str) -> None:
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        webbrowser.open(url)
    except Exception:
        webbrowser.open(url)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Soonest meeting, ignore window.")
    parser.add_argument("--event", default=None, help="Specific calendar event external_id.")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    theme = current_theme()
    app.setStyleSheet(build_stylesheet(theme))

    dlg = MeetingPrepDialog(force=args.force, event_id=args.event)
    dlg.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
