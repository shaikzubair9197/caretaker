"""
Follow-up Center — global desktop popup (Plan 3).

Reuses the Meeting Prep popup architecture verbatim: a frameless, always-on-top
PySide6 QDialog launched as its own process (by daemon/meeting_scheduler.py),
fetching from the local Caretaker API via a QThread worker and styled with
ui/theme.py. Like Meeting Prep it is a frameless, always-on-top desktop window,
but it opens as a large CENTERED WORKSPACE (≈85% of the screen) — a productivity
surface, not a sidebar — with a custom title bar (minimize / expand / close).
Minimizing collapses it to a compact, always-accessible floating pill (Copilot /
Teams-companion style) that restores the full workspace in one click. A
single-instance PID lock prevents duplicate windows (Pass 2 P2).

It is the production review surface for AI-generated meeting follow-up drafts;
the dashboard component (dashboard/components/followup_center.js) remains only as
an internal debug/admin view.

Masked-only: it renders the tokens the API returns and never decrypts. Decryption
happens server-side at approve/execute time, behind the approved-status gate.

Usage:
    python ui/followup_center_popup.py              # all pending/recent drafts
    python ui/followup_center_popup.py --meeting 5  # one meeting only
"""

import argparse
import atexit
import json
import os
import sys
import tempfile
from functools import partial

from dotenv import load_dotenv
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from ui.theme import build_stylesheet, current_theme
except ModuleNotFoundError:
    # Allow running the script directly (not as a package) by adding project root to sys.path
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from ui.theme import build_stylesheet, current_theme

load_dotenv()

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

PANEL_WIDTH = 470
WORKSPACE_RATIO = 0.85          # centered workspace = 85% of the screen (R4)
_LOCK_NAME = "caretaker_followup_center"

GROUP_ORDER = ["pending_approval", "clarification_needed", "executed", "dismissed", "failed"]
GROUP_LABELS = {
    "pending_approval": "Pending Approval",
    "clarification_needed": "Clarification Required",
    "executed": "Executed",
    "dismissed": "Dismissed",
    "failed": "Failed",
}
TYPE_LABEL = {
    "teams_message_draft": "Teams", "email_draft": "Email", "reminder_draft": "Reminder",
    "calendar_reminder_draft": "Calendar", "followup_suggestion_draft": "Suggestion",
    "clarification_needed": "Clarification",
}

# Sendable draft types shown under "Generated Drafts" (clarifications get their own section).
_SEND_TYPES = ("email_draft", "teams_message_draft", "calendar_reminder_draft",
               "reminder_draft", "followup_suggestion_draft")
# Palette (matches the dark theme used inline across meeting_prep_popup.py)
C_BG = "#0d1117"
C_CARD = "#161b22"
C_BORDER = "#30363d"
C_TEXT = "#c9d1d9"
C_MUTED = "#8b949e"
C_ACCENT = "#6d5efc"
C_DANGER = "#f85149"
C_SUCCESS = "#3fb950"
C_WARN = "#d29922"

# Typography (Phase 5 readability pass) — larger, higher-contrast sizing so the
# Center reads as a modern productivity surface, not a debug list. Centralized
# here and used across the workspace + cards.
FS_HEADER = 16     # workspace / focus context header
FS_COL    = 14     # column header buttons
FS_TITLE  = 14     # card title
FS_BODY   = 13     # card body / preview
FS_LABEL  = 12     # type label, status badge
FS_META   = 11     # metadata, timestamps, secondary lines

# The three fixed workspace columns (Phase 5). Drafts holds every sendable draft +
# credential clarification (approve/reveal/send live here); Commitments and Action
# Items are server-classified lenses on the same follow-up set (followup_category).
WORKSPACE_COLUMNS = (("drafts", "Drafts"), ("commitments", "Commitments"), ("action_items", "Action Items"))


# ── HTTP worker (same QThread+Signal pattern as meeting_prep's _FetchWorker) ──
class _HttpWorker(QThread):
    done = Signal(str, object)  # tag, payload (dict, or {"_error": ...})

    def __init__(self, tag: str, method: str, path: str, body=None) -> None:
        super().__init__()
        self._tag = tag
        self._method = method
        self._path = path
        self._body = body

    def run(self) -> None:
        try:
            import requests
            fn = getattr(requests, self._method.lower())
            kwargs = {"headers": _HEADERS, "timeout": 30}
            if self._body is not None:
                kwargs["json"] = self._body
            resp = fn(f"{API_BASE}{self._path}", **kwargs)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            self.done.emit(self._tag, data if isinstance(data, dict) else {"_list": data})
        except Exception as exc:  # noqa: BLE001
            self.done.emit(self._tag, {"_error": str(exc)})


class _StableScrollArea(QScrollArea):
    """QScrollArea that never auto-scrolls to a focused child.

    Qt's QScrollArea calls ensureWidgetVisible() whenever a child receives focus
    (e.g. clicking a checkbox/button inside a card), which yanks the whole list
    up or down. Overriding it to a no-op keeps the scroll position stable on click;
    we still scroll explicitly via the scrollbar when switching tabs."""

    def ensureWidgetVisible(self, childWidget, xmargin=50, ymargin=50):  # noqa: N803
        return


def _btn(text: str, color: str, on_click) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background:transparent; color:{color}; border:1px solid {color};"
        f" border-radius:6px; padding:4px 10px; font-size:11px; }}"
        f"QPushButton:hover {{ background:{color}22; }}"
    )
    b.clicked.connect(on_click)
    return b


class _CompactBar(QWidget):
    """Minimized state: a compact, always-on-top floating pill (Copilot / Teams
    companion style) showing the draft count; one click restores the workspace."""

    def __init__(self, on_restore) -> None:
        super().__init__()
        self._on_restore = on_restore
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setStyleSheet(f"QWidget {{ background:{C_CARD}; border:1px solid {C_ACCENT}; border-radius:20px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(10)
        self._lbl = QLabel("✦  Follow-ups")
        self._lbl.setStyleSheet(f"color:{C_TEXT}; font-size:12px; font-weight:700; border:none;")
        lay.addWidget(self._lbl)
        lay.addWidget(_btn("Open ▢", C_ACCENT, self._restore))

    def set_count(self, text: str) -> None:
        self._lbl.setText(f"✦  Follow-ups  {text}".rstrip())

    def show_at_corner(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore(self) -> None:
        self._on_restore()

    def mouseDoubleClickEvent(self, e) -> None:   # double-click the pill also restores
        self._on_restore()


class FollowupCenterDialog(QDialog):
    def __init__(self, meeting_id: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._meeting_id = meeting_id
        self._selected: set[int] = set()
        # aid → its checkbox widgets (a draft can appear in several tabs at once;
        # all its checkboxes must reflect the same selection state).
        self._checkboxes: dict[int, list] = {}
        self._workers: list[_HttpWorker] = []
        self._inflight_requests = 0
        self._data: dict = {}
        self._pill: "_CompactBar | None" = None
        self._maximized = False
        self._header_h = 48          # draggable band (frameless window)
        self._drag_off = None
        self._setup_window()
        self._build_shell()
        self.refresh()

    # ── window ───────────────────────────────────────────────────────────────
    def _setup_window(self) -> None:
        self.setWindowTitle("Caretaker — Follow-up Center")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(build_stylesheet(current_theme()))
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)
        # Open full-screen by default (R4 workspace); the ▢ control toggles down to
        # the smaller centered workspace and back.
        self._apply_workspace_geometry(0.98)

    def _apply_workspace_geometry(self, ratio: float) -> None:
        """Centered desktop workspace at `ratio` of the available screen (R4)."""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * ratio)
        h = int(screen.height() * ratio)
        x = screen.left() + (screen.width() - w) // 2
        y = screen.top() + (screen.height() - h) // 2
        self.setGeometry(x, y, w, h)
        self._maximized = ratio >= 0.97

    # ── window controls (custom title bar — frameless, R3) ────────────────────
    def _toggle_maximize(self) -> None:
        self._apply_workspace_geometry(0.98 if not self._maximized else WORKSPACE_RATIO)

    def _minimize_to_pill(self) -> None:
        """Collapse the workspace to a compact, always-on-top floating pill."""
        if self._pill is None:
            self._pill = _CompactBar(on_restore=self._restore_from_pill)
        self._pill.set_count(self._count.text() or "")
        self.hide()
        self._pill.show_at_corner()

    def _restore_from_pill(self) -> None:
        if self._pill is not None:
            self._pill.hide()
        self._apply_workspace_geometry(0.98 if self._maximized else WORKSPACE_RATIO)
        self.show()
        self.raise_()
        self.activateWindow()

    # ── drag (frameless window has no native title bar to grab) ────────────────
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= self._header_h:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_off is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._drag_off = None

    # ── shell (header + bulk bar + scroll body) ──────────────────────────────
    def _build_shell(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(f"background:{C_BG}; border-bottom:1px solid {C_BORDER};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 12, 10, 12)
        title = QLabel("✦  Meeting Follow-up Center")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:14px; font-weight:700;")
        self._count = QLabel("")
        self._count.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(self._count)
        hl.addWidget(_btn("↻", C_MUTED, self.refresh))
        hl.addWidget(_btn("—", C_MUTED, self._minimize_to_pill))   # minimize → compact pill
        hl.addWidget(_btn("▢", C_MUTED, self._toggle_maximize))    # expand / restore workspace
        hl.addWidget(_btn("✕", C_DANGER, self.close))
        header.setFixedHeight(self._header_h)
        root.addWidget(header)

        bulk = QWidget()
        bulk.setStyleSheet(f"background:{C_BG}; border-bottom:1px solid {C_BORDER};")
        self._bulk = bulk
        bl = QHBoxLayout(bulk)
        bl.setContentsMargins(14, 8, 10, 8)
        self._sel_lbl = QLabel("0 selected")
        self._sel_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        bl.addWidget(self._sel_lbl)
        # Transient result line for bulk/per-card actions (success / skip / error),
        # so the buttons visibly report what happened instead of silently refreshing.
        self._action_status = QLabel("")
        self._action_status.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        bl.addSpacing(12)
        bl.addWidget(self._action_status)
        bl.addStretch(1)
        approve_selected_btn = _btn("Approve Selected", C_SUCCESS, self._approve_selected)
        reject_selected_btn = _btn("Reject Selected", C_DANGER, self._reject_selected)
        approve_all_btn = _btn("Approve All", C_ACCENT, self._approve_all)
        for btn in (approve_selected_btn, reject_selected_btn, approve_all_btn):
            btn.setProperty("request_sensitive", True)
        bl.addWidget(approve_selected_btn)
        bl.addWidget(reject_selected_btn)
        bl.addWidget(approve_all_btn)
        root.addWidget(bulk)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._scroll = _StableScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(f"QScrollArea {{ background:{C_BG}; border:none; }}")
        self._body = QWidget()
        self._body.setStyleSheet(f"background:{C_BG};")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(12, 12, 12, 24)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)
        self._stack.addWidget(self._scroll)              # 0: content

        self._msg = QLabel("Loading drafts…")
        self._msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color:{C_MUTED}; font-size:13px;")
        self._stack.addWidget(self._msg)                 # 1: message
        self._stack.setCurrentIndex(1)

    # ── data ──────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._msg.setText("Loading drafts…")
        self._stack.setCurrentIndex(1)
        path = "/drafts" + (f"?meeting_id={self._meeting_id}" if self._meeting_id else "")
        self._run("drafts", "GET", path)

    def _run(self, tag: str, method: str, path: str, body=None) -> None:
        w = _HttpWorker(tag, method, path, body)
        w.done.connect(self._on_done)
        self._workers.append(w)
        self._begin_request()
        try:
            w.start()
        except Exception:
            self._end_request()
            raise

    def _begin_request(self) -> None:
        self._inflight_requests += 1
        if self._inflight_requests == 1:
            self._set_request_controls_enabled(False)

    def _end_request(self) -> None:
        if self._inflight_requests <= 0:
            self._inflight_requests = 0
            self._set_request_controls_enabled(True)
            return
        self._inflight_requests -= 1
        if self._inflight_requests == 0:
            self._set_request_controls_enabled(True)

    def _set_request_controls_enabled(self, enabled: bool) -> None:
        for container in (getattr(self, "_bulk", None), getattr(self, "_body", None)):
            if container is None:
                continue
            for widget in container.findChildren(QWidget):
                if widget.property("request_sensitive"):
                    widget.setEnabled(enabled)

    def _on_done(self, tag: str, payload: object) -> None:
        try:
            if not isinstance(payload, dict):
                payload = {}
            if tag == "drafts":
                if payload.get("_error"):
                    self._msg.setText(f"Could not load drafts:\n{payload['_error']}\n\nIs the API running?")
                    self._stack.setCurrentIndex(1)
                    return
                self._render(payload)
            elif tag == "versions":
                self._show_version_history(payload)
            elif tag == "audit":
                self._show_audit_timeline(payload)
            elif tag == "reveal":
                self._show_reveal(payload)
            elif tag == "action":
                # Surface the outcome (the buttons used to refresh silently, which
                # looked like nothing happened), then reload to reflect new state.
                if payload.get("_error"):
                    self._flash(f"Action failed: {payload['_error']}", error=True)
                else:
                    self._flash(self._summarize_action(payload))
                self.refresh()
        finally:
            self._end_request()

    # ── render ─────────────────────────────────────────────────────────────────
    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _render(self, data: dict) -> None:
        self._data = data
        self._clear_body()
        self._checkboxes = {}   # rebuilt below; old widget refs are now stale
        meetings = data.get("meetings", []) or []
        ungrouped = data.get("ungrouped", []) or []

        # Flatten every draft across all groups + meetings into one list, then split
        # into the three fixed workspace columns (current-transcript-scoped when the
        # Center was launched with --meeting). No extra fetches — masked-only.
        all_d: list = []
        for m in meetings:
            groups = m.get("groups", {}) or {}
            for g in GROUP_ORDER:
                all_d += groups.get(g) or []
        all_d += ungrouped

        self._count.setText(f"{len(all_d)} draft(s)")
        if not all_d:
            self._msg.setText("No follow-up drafts yet.\nGenerate from a meeting to populate the center.")
            self._stack.setCurrentIndex(1)
            return

        # Meeting (transcript) filter options: "All meetings" + one entry per
        # meeting (+ unlinked), each carrying that meeting's own draft list. The tabs
        # below render whichever option is selected — masked-only, no extra fetches.
        self._meeting_options: list[tuple[str, list]] = [("All meetings", all_d)]
        for m in meetings:
            md: list = []
            groups = m.get("groups", {}) or {}
            for g in GROUP_ORDER:
                md += groups.get(g) or []
            subject = m.get("subject_human") or m.get("subject") or "Meeting"
            when = str(m.get("meeting_start") or "")[:16].replace("T", " ")
            self._meeting_options.append((f"{subject}  ·  {when}" if when else subject, md))
        if ungrouped:
            self._meeting_options.append(("Unlinked follow-ups", ungrouped))

        # Preserve the chosen meeting across refreshes; clamp if the set changed.
        if getattr(self, "_active_meeting", 0) >= len(self._meeting_options):
            self._active_meeting = 0
        sel = getattr(self, "_active_meeting", 0)
        self._col_drafts = self._split_cols(self._meeting_options[sel][1])

        self._body_layout.addWidget(self._build_workspace(meetings), 1)
        self._stack.setCurrentIndex(0)
        self._scroll.verticalScrollBar().setValue(0)   # never land mid-list after a render
        self._set_request_controls_enabled(self._inflight_requests == 0)
        self._update_sel()

    # ── tabbed workspace (Phase 5) ────────────────────────────────────────────
    @staticmethod
    def _split_cols(drafts: list) -> dict:
        """Split a flat draft list into the three workspace columns. Drafts holds
        sendable artifacts + credential clarification cards; Commitments / Action
        Items are the server-classified lenses (Phase 3 followup_category)."""
        return {
            "drafts": [d for d in drafts
                       if d.get("action_type") in _SEND_TYPES or d.get("action_type") == "clarification_needed"],
            "commitments": [d for d in drafts if d.get("followup_category") == "commitment"],
            "action_items": [d for d in drafts if d.get("followup_category") == "action_item"],
        }

    def _build_workspace(self, meetings: list) -> QWidget:
        """Tabbed workspace: a meeting (transcript) dropdown on top, then a tab bar
        (Drafts | Commitments | Action Items) over one full-width content area.
        Picking a meeting refilters the tabs; clicking a tab swaps the pre-built
        QStackedWidget page instantly — masked-only, no re-fetch, no scroll jump."""
        wrap = QWidget()
        wrap.setStyleSheet(f"background:{C_BG};")
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # meeting (transcript) filter — drives which transcript the tabs show
        mrow = QWidget()
        mrow.setStyleSheet(f"background:{C_BG};")
        mrl = QHBoxLayout(mrow)
        mrl.setContentsMargins(4, 2, 4, 2)
        mrl.setSpacing(8)
        mlbl = QLabel("Meeting:")
        mlbl.setStyleSheet(f"color:{C_MUTED}; font-size:{FS_META}px; font-weight:700; border:none;")
        mrl.addWidget(mlbl)
        self._meeting_select = QComboBox()
        self._meeting_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self._meeting_select.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Force a QListView popup so the stylesheet's ::item / :hover / :selected
        # rules actually apply (the native combo popup ignores them → a black list).
        self._meeting_select.setView(QListView())
        self._meeting_select.setStyleSheet(self._combo_style())
        for label, _ in getattr(self, "_meeting_options", []):
            self._meeting_select.addItem(label)
        msel = getattr(self, "_active_meeting", 0)
        if msel >= self._meeting_select.count():
            msel = 0
        self._meeting_select.setCurrentIndex(msel)
        self._meeting_select.currentIndexChanged.connect(self._select_meeting)
        mrl.addWidget(self._meeting_select, 1)
        outer.addWidget(mrow)

        # tab bar — text tabs with an accent underline on the active one
        tabbar = QWidget()
        tabbar.setStyleSheet(f"background:{C_BG}; border-bottom:1px solid {C_BORDER};")
        tl = QHBoxLayout(tabbar)
        tl.setContentsMargins(4, 0, 4, 0)
        tl.setSpacing(2)

        self._tab_btns: dict[int, QPushButton] = {}
        self._ws_pages = QStackedWidget()
        for idx, (key, title) in enumerate(WORKSPACE_COLUMNS):
            btn = QPushButton(title)            # text (with count) set by _rebuild_tab_pages
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(partial(self._select_tab, idx))
            tl.addWidget(btn)
            self._tab_btns[idx] = btn
        tl.addStretch(1)

        outer.addWidget(tabbar)
        outer.addWidget(self._ws_pages, 1)

        # Fill page content + tab counts for the current meeting, select active tab.
        self._rebuild_tab_pages()
        return wrap

    def _combo_style(self) -> str:
        return (
            # closed control
            f"QComboBox {{ background:{C_CARD}; color:{C_TEXT}; border:1px solid {C_BORDER};"
            f" border-radius:8px; padding:8px 14px; min-width:260px;"
            f" font-size:{FS_COL}px; font-weight:700; }}"
            f"QComboBox:hover {{ border-color:{C_ACCENT}; }}"
            f"QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:center right;"
            f" border:none; width:26px; }}"
            # open popup list (QListView)
            f"QComboBox QAbstractItemView {{ background:{C_CARD}; color:{C_TEXT};"
            f" border:1px solid {C_BORDER}; border-radius:8px; padding:4px; outline:none; }}"
            f"QComboBox QAbstractItemView::item {{ background:transparent; color:{C_TEXT};"
            f" padding:8px 12px; min-height:22px; border-radius:6px; }}"
            # hover = subtle accent tint; selected/current = solid accent
            f"QComboBox QAbstractItemView::item:hover {{ background:{C_ACCENT}33; color:{C_TEXT}; }}"
            f"QComboBox QAbstractItemView::item:selected {{ background:{C_ACCENT}; color:#ffffff; }}"
        )

    def _select_meeting(self, idx: int) -> None:
        """Refilter the tabs to the chosen transcript (or 'All meetings') and rebuild
        the tab pages — masked-only, reuses the already-fetched payload."""
        opts = getattr(self, "_meeting_options", None)
        if not opts or idx < 0 or idx >= len(opts):
            return
        self._active_meeting = idx
        self._col_drafts = self._split_cols(opts[idx][1])
        self._rebuild_tab_pages()

    def _rebuild_tab_pages(self) -> None:
        """(Re)build the three tab pages + their counts from the current
        _col_drafts, preserving the active tab. Used on first build and whenever the
        meeting filter changes."""
        pages = getattr(self, "_ws_pages", None)
        if pages is None:
            return
        while pages.count():
            w = pages.widget(0)
            pages.removeWidget(w)
            w.deleteLater()
        for idx, (key, title) in enumerate(WORKSPACE_COLUMNS):
            count = len(self._col_drafts.get(key, []))
            if idx in self._tab_btns:
                self._tab_btns[idx].setText(f"{title}  ({count})")
            pages.addWidget(self._build_tab_page(key))
        active = getattr(self, "_active_tab", 0)
        if active >= pages.count():
            active = 0
        self._select_tab(active)

    def _tab_style(self, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background:transparent; color:{C_ACCENT}; border:none;"
                f" border-bottom:2px solid {C_ACCENT}; padding:10px 20px;"
                f" font-size:{FS_COL}px; font-weight:700; }}"
            )
        return (
            f"QPushButton {{ background:transparent; color:{C_MUTED}; border:none;"
            f" border-bottom:2px solid transparent; padding:10px 20px;"
            f" font-size:{FS_COL}px; font-weight:600; }}"
            f"QPushButton:hover {{ color:{C_TEXT}; }}"
        )

    def _select_tab(self, idx: int) -> None:
        """Switch the active tab: swap the stacked page, restyle the tab buttons,
        and reset that page's scroll to the top so it never lands mid-list."""
        pages = getattr(self, "_ws_pages", None)
        if pages is None or idx >= pages.count():
            return
        self._active_tab = idx
        pages.setCurrentIndex(idx)
        for i, btn in self._tab_btns.items():
            btn.setStyleSheet(self._tab_style(i == idx))
        page = pages.widget(idx)
        if isinstance(page, QScrollArea):
            page.verticalScrollBar().setValue(0)

    def _context_header(self, meetings: list) -> QWidget:
        """Slim meeting-context strip above the columns."""
        if len(meetings) == 1:
            m = meetings[0]
            subject = m.get("subject") or "Meeting"
            bits = []
            if m.get("meeting_start"):
                bits.append(str(m["meeting_start"])[:16].replace("T", " "))
            parts = m.get("participant_tokens") or []
            bits.append(f"{len(parts)} participants")
            if parts:
                bits.append("  ".join(str(p) for p in parts[:6]))
            meta = "  ·  ".join(bits)
        elif meetings:
            subject, meta = f"All follow-ups · {len(meetings)} meetings", ""
        else:
            subject, meta = "Unlinked follow-ups", ""

        w = QFrame()
        w.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px; }}")
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(3)
        t = QLabel("▦  " + subject)
        t.setStyleSheet(f"color:{C_TEXT}; font-size:{FS_HEADER}px; font-weight:700; border:none;")
        t.setWordWrap(True)
        v.addWidget(t)
        if meta:
            ml = QLabel(meta)
            ml.setStyleSheet(f"color:{C_MUTED}; font-size:{FS_META}px; border:none;")
            ml.setWordWrap(True)
            v.addWidget(ml)
        return w

    def _build_tab_page(self, key: str) -> QWidget:
        """One tab's content: a single full-width, stable-scrolling list of cards."""
        drafts = self._col_drafts.get(key, [])
        scroll = _StableScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(2, 2, 2, 2)
        il.setSpacing(8)
        self._render_cards_into(il, drafts)
        il.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _render_cards_into(self, layout: QVBoxLayout, drafts: list) -> None:
        if not drafts:
            self._none(layout, None)
            return
        for d in drafts:
            layout.addWidget(self._card(d))

    # ── card helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _none(lay, w):
        lbl = QLabel("— none —")
        lbl.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        lay.addWidget(lbl)
        return w

    # The per-section accordion builders (Generated Drafts / compact lists /
    # Knowledge Updates / Execution Queue / Audit list) were removed in Phase 5 when
    # the accordion was replaced by the 3-pane workspace. Per-card Version/Audit
    # buttons and status badges live on _card; lifecycle detail is reachable via the
    # card's Version History / Audit Timeline dialogs.

    def _card(self, d: dict) -> QWidget:
        aid = d.get("action_id")
        atype = d.get("action_type", "")
        status = d.get("status", "")
        is_pending = status == "pending"
        is_clar = atype == "clarification_needed"

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:{C_BG}; border:1px solid {C_BORDER}; border-radius:8px; }}")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 9, 10, 9)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        if is_pending and not is_clar:
            cb = QCheckBox()
            cb.setProperty("request_sensitive", True)
            cb.setChecked(aid in self._selected)
            cb.stateChanged.connect(partial(self._toggle_sel, aid))
            self._checkboxes.setdefault(aid, []).append(cb)
            top.addWidget(cb)
        type_lbl = QLabel(TYPE_LABEL.get(atype, atype))
        type_lbl.setStyleSheet(f"color:{C_ACCENT}; font-size:{FS_LABEL}px; font-weight:700; border:none;")
        top.addWidget(type_lbl)
        st = QLabel(status)
        st_color = {"executed": C_SUCCESS, "failed": C_DANGER, "dismissed": C_MUTED}.get(status, C_TEXT)
        st.setStyleSheet(f"color:{st_color}; font-size:{FS_LABEL}px; border:none;")
        top.addWidget(st)
        if d.get("conflict_flag"):
            cf = QLabel("⚠ conflict")
            cf.setStyleSheet(f"color:{C_WARN}; font-size:10px; border:none;")
            top.addWidget(cf)
        top.addStretch(1)
        conf = d.get("confidence")
        info = QLabel((f"{round(conf*100)}%  " if conf is not None else "") + f"v{d.get('version', '—')}")
        info.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        top.addWidget(info)
        v.addLayout(top)

        # Render the human-readable (non-secret) text; the masked field is kept for
        # editing. Falls back to the masked value if the server didn't humanize it.
        title = QLabel(d.get("display_title_human") or d.get("display_title") or "(untitled)")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:{FS_TITLE}px; font-weight:700; border:none;")
        title.setWordWrap(True)
        v.addWidget(title)

        # From: — the fixed system sender identity (Pass 2 P1). Always shown.
        if d.get("sender_identity") and not is_clar:
            frm = QLabel(f"From:  {d['sender_identity']}")
            frm.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
            v.addWidget(frm)

        # Reasoning ("why this draft") — rendered only if available (rationale lands in a later phase).
        if d.get("rationale"):
            why = QLabel(f"Why: {d['rationale'].get('generated_because', '')}" if isinstance(d["rationale"], dict) else str(d["rationale"]))
            why.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
            why.setWordWrap(True)
            v.addWidget(why)

        preview_text = (d.get("preview_human") or d.get("preview")
                        or (("Clarification: " + d["reason"]) if d.get("reason") else ""))
        if preview_text:
            pv = QLabel(preview_text)
            pv.setStyleSheet(f"color:{C_TEXT}; font-size:{FS_BODY}px; border:none;")
            pv.setWordWrap(True)
            v.addWidget(pv)

        # Credential delivery (Phase 4): the body carries {{SECURE_REF:n}} inline; show
        # which credential(s) it resolves to — masked label only, never the value.
        secure_refs = d.get("secure_refs") or {}
        if secure_refs:
            labels = ", ".join(sorted({(r.get("masked_label") or "credential") for r in secure_refs.values()}))
            sl = QLabel(f"🔒 Delivers: {labels}")
            sl.setStyleSheet(f"color:{C_WARN}; font-size:{FS_META}px; border:none;")
            sl.setWordWrap(True)
            v.addWidget(sl)

        # Credential clarification (Phase 4): surface the "which credential?" ask
        # right inside the Drafts column — candidate labels / unmatched descriptors.
        candidates = d.get("candidates") or []
        if candidates:
            cand = QLabel("Which one?  " + "   •  ".join(c.get("label") or c.get("credential_key") or "?" for c in candidates))
            cand.setStyleSheet(f"color:{C_TEXT}; font-size:{FS_META}px; border:none;")
            cand.setWordWrap(True)
            v.addWidget(cand)
        unmatched = d.get("unmatched") or []
        if unmatched:
            um = QLabel("No matching credential for: " + ", ".join(str(u) for u in unmatched) + " — sync or add one.")
            um.setStyleSheet(f"color:{C_MUTED}; font-size:{FS_META}px; border:none;")
            um.setWordWrap(True)
            v.addWidget(um)

        meta_bits = []
        if d.get("execution_target"):
            meta_bits.append(f"→ {d['execution_target']}")
        meta_bits.append(f"{len(d.get('citations') or [])} citation(s)")
        if d.get("created_at"):
            meta_bits.append(f"created {d['created_at'][:16].replace('T', ' ')}")
        if d.get("edit_count") and d.get("last_modified"):
            meta_bits.append(f"modified {d['last_modified'][:16].replace('T', ' ')}")
        meta = QLabel("   ".join(meta_bits))
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        v.addWidget(meta)

        row = QHBoxLayout()
        row.setSpacing(6)
        version_btn = _btn("Version History", C_MUTED, partial(self._version_history, aid))
        version_btn.setProperty("request_sensitive", True)
        audit_btn = _btn("Audit Timeline", C_MUTED, partial(self._audit_timeline, aid))
        audit_btn.setProperty("request_sensitive", True)
        row.addWidget(version_btn)
        row.addWidget(audit_btn)
        # Click-to-reveal (Phase 4): server-side, audited unmask of the latest active
        # value(s) for this draft. Available while pending or approved.
        if secure_refs and not is_clar and status in ("pending", "approved"):
            reveal_btn = _btn(f"Reveal ({len(secure_refs)})", C_WARN, partial(self._reveal, aid))
            reveal_btn.setProperty("request_sensitive", True)
            row.addWidget(reveal_btn)
        if is_pending:
            if not is_clar:
                approve_btn = _btn("Approve", C_SUCCESS, partial(self._approve, aid))
                approve_btn.setProperty("request_sensitive", True)
                edit_btn = _btn("Edit", C_MUTED, partial(self._edit, d))
                edit_btn.setProperty("request_sensitive", True)
                regen_btn = _btn("Regenerate", C_MUTED, partial(self._regenerate, aid))
                regen_btn.setProperty("request_sensitive", True)
                row.addWidget(approve_btn)
                row.addWidget(edit_btn)
                row.addWidget(regen_btn)
            reject_btn = _btn("Reject" if not is_clar else "Dismiss", C_DANGER, partial(self._reject, aid))
            reject_btn.setProperty("request_sensitive", True)
            row.addWidget(reject_btn)
        row.addStretch(1)
        v.addLayout(row)
        return card

    # ── selection ───────────────────────────────────────────────────────────────
    def _toggle_sel(self, aid: int, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        if checked:
            self._selected.add(aid)
        else:
            self._selected.discard(aid)
        # A draft can be shown in several tabs at once — keep every copy of its
        # checkbox in sync so the selection is consistent wherever it appears.
        for cb in self._checkboxes.get(aid, []):
            try:
                if cb.isChecked() != checked:
                    cb.blockSignals(True)
                    cb.setChecked(checked)
                    cb.blockSignals(False)
            except RuntimeError:
                continue   # a stale (deleted) checkbox from a prior render
        self._update_sel()

    def _update_sel(self) -> None:
        self._sel_lbl.setText(f"{len(self._selected)} selected")

    def _flash(self, text: str, error: bool = False) -> None:
        """Show a short result/status line in the bulk bar (green ok / red error)."""
        if getattr(self, "_action_status", None) is None:
            return
        self._action_status.setText(text)
        self._action_status.setStyleSheet(
            f"color:{C_DANGER if error else C_SUCCESS}; font-size:11px;"
        )

    @staticmethod
    def _summarize_action(payload: dict) -> str:
        """One-line summary of an approve/dismiss result (batch or single)."""
        if "approved" in payload:
            return f"Approved {payload['approved']} of {payload.get('submitted', 0)}."
        if "dismissed" in payload:
            return f"Rejected {payload['dismissed']} of {payload.get('submitted', 0)}."
        if payload.get("status"):
            return f"Action {payload.get('action_id', '')} → {payload['status']}."
        return "Done."

    # ── actions ──────────────────────────────────────────────────────────────────
    def _approve(self, aid: int) -> None:
        self._run("action", "POST", f"/agent/actions/{aid}/approve")

    def _reject(self, aid: int) -> None:
        self._run("action", "POST", f"/agent/actions/{aid}/dismiss")

    def _regenerate(self, aid: int) -> None:
        self._run("action", "POST", f"/drafts/{aid}/regenerate", body={})

    def _version_history(self, aid: int) -> None:
        self._run("versions", "GET", f"/drafts/{aid}/version-history")

    def _audit_timeline(self, aid: int) -> None:
        self._run("audit", "GET", f"/drafts/{aid}/audit-trail")

    def _reveal(self, aid: int) -> None:
        # Server resolves every {{SECURE_REF:n}} in this draft to its latest active
        # value, audited as CREDENTIAL_REVEAL. The value is shown ephemerally below.
        self._run("reveal", "POST", f"/drafts/{aid}/reveal-credentials")

    def _approve_selected(self) -> None:
        if not self._selected:
            self._flash("Select at least one draft (tick its checkbox) first.", error=True)
            return
        self._run("action", "POST", "/agent/actions/approve-batch", body={"action_ids": list(self._selected)})

    def _reject_selected(self) -> None:
        if not self._selected:
            self._flash("Select at least one draft (tick its checkbox) first.", error=True)
            return
        self._run("action", "POST", "/agent/actions/dismiss-batch", body={"action_ids": list(self._selected)})

    def _approve_all(self) -> None:
        ids = self._all_pending()
        if not ids:
            self._flash("No pending drafts to approve.", error=True)
            return
        self._run("action", "POST", "/agent/actions/approve-batch", body={"action_ids": ids})

    def _all_pending(self) -> list:
        """Every pending, non-clarification draft id currently loaded (Approve All)."""
        ids: list[int] = []
        for m in self._data.get("meetings", []) or []:
            for d in (m.get("groups", {}) or {}).get("pending_approval", []) or []:
                if d.get("action_id") is not None:
                    ids.append(d["action_id"])
        for d in self._data.get("ungrouped", []) or []:
            if d.get("status") == "pending" and d.get("action_type") != "clarification_needed":
                ids.append(d["action_id"])
        return ids

    def _edit(self, d: dict) -> None:
        dlg = _EditDialog(d, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.updates:
            self._run("action", "PATCH", f"/drafts/{d['action_id']}/payload", body=dlg.updates)

    def _show_version_history(self, payload: dict) -> None:
        dlg = _VersionHistoryDialog(payload, self)
        dlg.exec()

    def _show_audit_timeline(self, payload: dict) -> None:
        dlg = _AuditTimelineDialog(payload, self)
        dlg.exec()

    def _show_reveal(self, payload: dict) -> None:
        dlg = _RevealDialog(payload if isinstance(payload, dict) else {}, self)
        dlg.exec()


class _EditDialog(QDialog):
    """Minimal inline editor for a draft's content fields (masked tokens kept)."""

    def __init__(self, d: dict, parent=None) -> None:
        super().__init__(parent)
        self.updates: dict = {}
        self._d = d
        self.setWindowTitle("Edit draft")
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        atype = d.get("action_type", "")
        self._fields: dict[str, QWidget] = {}

        if atype == "email_draft":
            self._add_line(v, "subject", "Subject", d.get("display_title", ""))
            self._add_area(v, "body", "Body", d.get("preview", ""))
        elif atype == "teams_message_draft":
            self._add_area(v, "body", "Message", d.get("preview", ""))
        elif atype == "followup_suggestion_draft":
            self._add_area(v, "suggestion_text", "Suggestion", d.get("preview", ""))
        else:
            self._add_line(v, "title", "Title", d.get("preview", "") or d.get("display_title", ""))

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(_btn("Cancel", C_MUTED, self.reject))
        row.addWidget(_btn("Save", C_SUCCESS, self._save))
        v.addLayout(row)

    def _add_line(self, v, key, label, value):
        v.addWidget(QLabel(label))
        e = QLineEdit(value or "")
        v.addWidget(e)
        self._fields[key] = e

    def _add_area(self, v, key, label, value):
        v.addWidget(QLabel(label))
        e = QTextEdit()
        e.setPlainText(value or "")
        e.setMinimumHeight(120)
        v.addWidget(e)
        self._fields[key] = e

    def _save(self) -> None:
        for key, w in self._fields.items():
            self.updates[key] = w.text() if isinstance(w, QLineEdit) else w.toPlainText()
        self.accept()


class _VersionHistoryDialog(QDialog):
    """Read-only modal dialog for a draft's knowledge version history."""

    def __init__(self, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Version History")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        versions = payload.get("versions") or []
        knowledge_key = payload.get("knowledge_key") or ""
        action_id = payload.get("action_id")
        error = payload.get("_error")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Version History")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:15px; font-weight:700; border:none;")
        root.addWidget(title)

        meta_bits = [f"Action #{action_id}" if action_id is not None else "Draft"]
        if knowledge_key:
            meta_bits.append(f"Key: {knowledge_key}")
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        root.addWidget(meta)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background:{C_BG}; border:none; }}")
        content = QWidget()
        content.setStyleSheet(f"background:{C_BG};")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)

        if error:
            err = QLabel(f"Could not load version history:\n{error}")
            err.setStyleSheet(f"color:{C_DANGER}; font-size:12px; border:none;")
            err.setWordWrap(True)
            content_lay.addWidget(err)
        elif not versions:
            empty = QLabel("No version history available.")
            empty.setStyleSheet(f"color:{C_MUTED}; font-size:12px; border:none;")
            empty.setWordWrap(True)
            content_lay.addWidget(empty)
        else:
            for version in versions:
                content_lay.addWidget(self._version_row(version))

        content_lay.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(_btn("Close", C_ACCENT, self.accept))
        root.addLayout(actions)

    def _version_row(self, version: dict) -> QWidget:
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:8px; }}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        vnum = QLabel(f"v{version.get('version', '—')}")
        vnum.setStyleSheet(f"color:{C_ACCENT}; font-size:11px; font-weight:700; border:none;")
        top.addWidget(vnum)

        status = "active" if version.get("is_active") else "superseded"
        st = QLabel(status)
        st_color = C_SUCCESS if version.get("is_active") else C_MUTED
        st.setStyleSheet(f"color:{st_color}; font-size:10px; border:none;")
        top.addWidget(st)

        top.addStretch(1)
        if version.get("confidence") is not None:
            conf = QLabel(f"{round(float(version['confidence']) * 100)}% confidence")
            conf.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
            top.addWidget(conf)

        lay.addLayout(top)

        title = QLabel(version.get("title_masked") or "(untitled)")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:12px; font-weight:600; border:none;")
        title.setWordWrap(True)
        lay.addWidget(title)

        meta_bits = []
        if version.get("valid_from"):
            meta_bits.append(f"from {version['valid_from'][:16].replace('T', ' ')}")
        if version.get("valid_to"):
            meta_bits.append(f"to {version['valid_to'][:16].replace('T', ' ')}")
        if version.get("resolution_method"):
            meta_bits.append(f"via {version['resolution_method']}")
        if version.get("superseded_by_id") is not None:
            meta_bits.append(f"superseded by #{version['superseded_by_id']}")
        meta = QLabel("  ·  ".join(meta_bits) if meta_bits else " ")
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        lay.addWidget(meta)

        return row


class _AuditTimelineDialog(QDialog):
    """Read-only modal dialog for a draft's audit timeline."""

    def __init__(self, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audit Timeline")
        self.setModal(True)
        self.setMinimumWidth(640)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        events = payload.get("events") or []
        action_id = payload.get("action_id")
        error = payload.get("_error")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Audit Timeline")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:15px; font-weight:700; border:none;")
        root.addWidget(title)

        meta = QLabel(f"Action #{action_id}" if action_id is not None else "Draft")
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        root.addWidget(meta)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background:{C_BG}; border:none; }}")
        content = QWidget()
        content.setStyleSheet(f"background:{C_BG};")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)

        if error:
            err = QLabel(f"Could not load audit timeline:\n{error}")
            err.setStyleSheet(f"color:{C_DANGER}; font-size:12px; border:none;")
            err.setWordWrap(True)
            content_lay.addWidget(err)
        elif not events:
            empty = QLabel("No audit events available.")
            empty.setStyleSheet(f"color:{C_MUTED}; font-size:12px; border:none;")
            empty.setWordWrap(True)
            content_lay.addWidget(empty)
        else:
            for event in events:
                content_lay.addWidget(self._event_row(event))

        content_lay.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(_btn("Close", C_ACCENT, self.accept))
        root.addLayout(actions)

    def _event_row(self, event: dict) -> QWidget:
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:8px; }}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)

        ev_type = QLabel(event.get("event_type") or "(unknown)")
        ev_type.setStyleSheet(f"color:{C_ACCENT}; font-size:11px; font-weight:700; border:none;")
        top.addWidget(ev_type)

        top.addStretch(1)

        outcome = QLabel(event.get("outcome") or "—")
        outcome_color = C_SUCCESS if (event.get("outcome") or "").upper() == "SUCCESS" else C_DANGER if (event.get("outcome") or "").upper() == "ERROR" else C_MUTED
        outcome.setStyleSheet(f"color:{outcome_color}; font-size:10px; border:none;")
        top.addWidget(outcome)

        lay.addLayout(top)

        meta_bits = []
        if event.get("created_at"):
            meta_bits.append(f"at {event['created_at'][:16].replace('T', ' ')}")
        if event.get("actor"):
            meta_bits.append(f"actor {event['actor']}")
        meta = QLabel("  ·  ".join(meta_bits) if meta_bits else " ")
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        lay.addWidget(meta)

        data = QTextEdit()
        data.setReadOnly(True)
        data.setMinimumHeight(90)
        data.setStyleSheet(
            f"QTextEdit {{ background:{C_BG}; color:{C_TEXT}; border:1px solid {C_BORDER};"
            f" border-radius:6px; font-family:monospace; font-size:10px; }}"
        )
        data.setPlainText(json.dumps(event.get("event_data") or {}, indent=2, sort_keys=True, ensure_ascii=False))
        lay.addWidget(data)

        return row


class _RevealDialog(QDialog):
    """Ephemeral display of revealed credential value(s) (Phase 4 click-to-reveal).

    Values are shown for THIS dialog only and are never persisted by the UI. Each
    reveal was already resolved server-side to the LATEST ACTIVE version and audited
    as CREDENTIAL_REVEAL. Closing the dialog discards the values from view."""

    def __init__(self, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revealed credentials")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        revealed = payload.get("revealed") or {}
        errors = payload.get("errors") or {}
        api_error = payload.get("_error")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Revealed credentials")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:{FS_HEADER}px; font-weight:700; border:none;")
        root.addWidget(title)

        warn = QLabel("Shown once for this view only — not stored by the app. Treat as sensitive.")
        warn.setStyleSheet(f"color:{C_WARN}; font-size:{FS_META}px; border:none;")
        warn.setWordWrap(True)
        root.addWidget(warn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background:{C_BG}; border:none; }}")
        content = QWidget()
        content.setStyleSheet(f"background:{C_BG};")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)

        if api_error:
            err = QLabel(f"Could not reveal credentials:\n{api_error}")
            err.setStyleSheet(f"color:{C_DANGER}; font-size:{FS_BODY}px; border:none;")
            err.setWordWrap(True)
            content_lay.addWidget(err)
        elif not revealed and not errors:
            empty = QLabel("No credentials to reveal for this draft.")
            empty.setStyleSheet(f"color:{C_MUTED}; font-size:{FS_BODY}px; border:none;")
            empty.setWordWrap(True)
            content_lay.addWidget(empty)
        else:
            for n in sorted(revealed.keys(), key=lambda k: int(k) if str(k).isdigit() else 0):
                content_lay.addWidget(self._value_row(n, revealed[n]))
            for n, reason in errors.items():
                er = QLabel(f"Reference {n}: could not resolve ({reason})")
                er.setStyleSheet(f"color:{C_DANGER}; font-size:{FS_META}px; border:none;")
                er.setWordWrap(True)
                content_lay.addWidget(er)

        content_lay.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(_btn("Close", C_ACCENT, self.accept))
        root.addLayout(actions)

    def _value_row(self, n, info: dict) -> QWidget:
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:8px; }}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(4)

        label = QLabel(f"{{{{SECURE_REF:{n}}}}}  ·  {info.get('masked_label') or 'credential'}")
        label.setStyleSheet(f"color:{C_ACCENT}; font-size:{FS_LABEL}px; font-weight:700; border:none;")
        label.setWordWrap(True)
        lay.addWidget(label)

        # Read-only field so the value can be copied but not edited; selectable text.
        value = QLineEdit(info.get("value") or "")
        value.setReadOnly(True)
        value.setCursorPosition(0)
        value.setStyleSheet(
            f"QLineEdit {{ background:{C_BG}; color:{C_TEXT}; border:1px solid {C_BORDER};"
            f" border-radius:6px; padding:6px 8px; font-family:monospace; font-size:{FS_BODY}px; }}"
        )
        lay.addWidget(value)
        return row


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class _SingleInstance:
    """PID-lockfile single-instance guard (Pass 2 P2): prevents the daemon (or a
    manual launch) from stacking multiple Follow-up Center windows. Ownership of
    this guard moves to the FollowUpCenterManager in P6; for now it lives with the
    popup, exactly as planned in R2. Fails open — a lockfile quirk never blocks UI."""

    def __init__(self, name: str = _LOCK_NAME) -> None:
        self.path = os.path.join(tempfile.gettempdir(), f"{name}.pid")
        self.acquired = False

    def acquire(self) -> bool:
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    old = int((f.read().strip() or "0") or 0)
                if old and old != os.getpid() and _pid_alive(old):
                    return False
            with open(self.path, "w") as f:
                f.write(str(os.getpid()))
            self.acquired = True
            atexit.register(self.release)
            return True
        except Exception:            # noqa: BLE001 — never block the popup on a lockfile error
            return True

    def release(self) -> None:
        try:
            if self.acquired and os.path.exists(self.path):
                with open(self.path) as f:
                    if (f.read().strip() or "") == str(os.getpid()):
                        os.remove(self.path)
        except Exception:            # noqa: BLE001
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", type=int, default=None, help="restrict to one meeting_transcript_id")
    args = parser.parse_args()

    instance = _SingleInstance()
    if not instance.acquire():
        print("Follow-up Center is already open — not launching a duplicate window.")
        return

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = FollowupCenterDialog(meeting_id=args.meeting)
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
