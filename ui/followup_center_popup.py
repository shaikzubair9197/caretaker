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
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

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
# Execution Queue ordering (status → label). "approved" is the transient state P1 made durable.
EXEC_STATUS_ORDER = [("pending", "Pending"), ("approved", "Approved"),
                     ("executed", "Executed"), ("failed", "Failed"), ("dismissed", "Dismissed")]

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
        self._workers: list[_HttpWorker] = []
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
        self._apply_workspace_geometry(WORKSPACE_RATIO)

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
        bl = QHBoxLayout(bulk)
        bl.setContentsMargins(14, 8, 10, 8)
        self._sel_lbl = QLabel("0 selected")
        self._sel_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        bl.addWidget(self._sel_lbl)
        bl.addStretch(1)
        bl.addWidget(_btn("Approve Selected", C_SUCCESS, self._approve_selected))
        bl.addWidget(_btn("Reject Selected", C_DANGER, self._reject_selected))
        bl.addWidget(_btn("Approve All", C_ACCENT, self._approve_all))
        root.addWidget(bulk)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._scroll = QScrollArea()
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
        w.start()

    def _on_done(self, tag: str, payload: object) -> None:
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
        elif tag == "action":
            # any mutation → reload to reflect new state
            self.refresh()

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
        meetings = data.get("meetings", []) or []
        ungrouped = data.get("ungrouped", []) or []
        total = sum(m.get("counts", {}).get("total", 0) for m in meetings) + len(ungrouped)
        self._count.setText(f"{total} draft(s)")

        if not meetings and not ungrouped:
            self._msg.setText("No follow-up drafts yet.\nGenerate from a meeting to populate the center.")
            self._stack.setCurrentIndex(1)
            return

        for m in meetings:
            self._body_layout.addWidget(self._meeting_section(m))
        if ungrouped:
            self._body_layout.addWidget(self._meeting_section({
                "subject": "Unlinked drafts", "meeting_start": None, "participant_tokens": [],
                "counts": {}, "groups": {"pending_approval": ungrouped},
            }))
        self._body_layout.addStretch(1)
        self._stack.setCurrentIndex(0)
        self._update_sel()

    def _meeting_section(self, m: dict) -> QWidget:
        """A full meeting block: Meeting Information + 7 collapsible workspace
        sections (R5), all re-grouped client-side from the existing GET /drafts
        payload — no extra fetches, masked-only."""
        groups = m.get("groups", {}) or {}
        all_d: list = []
        for g in ("pending_approval", "clarification_needed", "executed", "dismissed", "failed"):
            all_d += groups.get(g) or []

        sec = QFrame()
        sec.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px; }}")
        v = QVBoxLayout(sec)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        # ═══ Meeting Information ═══ (always visible)
        v.addWidget(self._meeting_info(m))

        # ═══ Generated Drafts ═══ (Email / Teams / Calendar / Reminder / Suggestion) — full cards
        sends = [d for d in all_d if d.get("action_type") in _SEND_TYPES]
        v.addWidget(self._collapsible("Generated Drafts", len(sends), self._generated_drafts(sends), open=True))

        # ═══ Action Items ═══
        ai = [d for d in all_d if d.get("action_type") == "teams_message_draft"]
        v.addWidget(self._collapsible("Action Items", len(ai), self._compact_list(ai), open=False))

        # ═══ Commitments ═══
        co = [d for d in all_d if d.get("action_type") in ("email_draft", "reminder_draft")]
        v.addWidget(self._collapsible("Commitments", len(co), self._compact_list(co), open=False))

        # ═══ Knowledge Updates ═══
        ku = [d for d in all_d if d.get("knowledge_key")]
        v.addWidget(self._collapsible("Knowledge Updates", len(ku), self._knowledge_list(ku), open=False))

        # ═══ Clarifications Required ═══ — full cards (open if any)
        cl = [d for d in all_d if d.get("action_type") == "clarification_needed"]
        v.addWidget(self._collapsible("Clarifications Required", len(cl), self._cards_list(cl), open=bool(cl)))

        # ═══ Execution Queue ═══ (by status)
        v.addWidget(self._collapsible("Execution Queue", len(all_d), self._exec_queue(all_d), open=False))

        # ═══ Audit / Timeline ═══
        v.addWidget(self._collapsible("Audit / Timeline", len(all_d), self._audit_list(all_d), open=False))

        return sec

    # ── section builders (read-only projections of the same draft list) ───────
    @staticmethod
    def _vbox() -> tuple:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 4, 2, 2)
        lay.setSpacing(6)
        return w, lay

    @staticmethod
    def _none(lay, w):
        lbl = QLabel("— none —")
        lbl.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        lay.addWidget(lbl)
        return w

    def _meeting_info(self, m: dict) -> QWidget:
        w, lay = self._vbox()
        title = QLabel("▦  " + (m.get("subject") or "Meeting"))
        title.setStyleSheet(f"color:{C_TEXT}; font-size:15px; font-weight:700; border:none;")
        title.setWordWrap(True)
        lay.addWidget(title)
        bits = []
        if m.get("meeting_start"):
            bits.append(str(m["meeting_start"])[:16].replace("T", " "))
        parts = m.get("participant_tokens") or []
        bits.append(f"{len(parts)} participants")
        if parts:
            bits.append("  ".join(str(p) for p in parts[:6]))
        meta = QLabel("  ·  ".join(bits))
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        lay.addWidget(meta)
        c = m.get("counts", {}) or {}
        status = QLabel(
            f"Status:  {c.get('pending', 0)} pending · {c.get('clarification', 0)} clarify · "
            f"{c.get('failed', 0)} failed · {c.get('executed', 0)} executed"
        )
        status.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        lay.addWidget(status)
        return w

    def _collapsible(self, title: str, n, content: QWidget, open: bool = True) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 4, 0, 0)
        wl.setSpacing(0)

        def _label(o: bool) -> str:
            return f"{'▾' if o else '▸'}  {title}" + (f"  ({n})" if n is not None else "")

        hdr = QPushButton(_label(open))
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr.setStyleSheet(
            f"QPushButton {{ text-align:left; background:{C_BG}; color:{C_TEXT}; border:1px solid {C_BORDER};"
            f" border-radius:6px; padding:6px 10px; font-size:11px; font-weight:700; }}"
            f"QPushButton:hover {{ border-color:{C_ACCENT}; }}"
        )
        content.setVisible(open)

        def _toggle():
            vis = not content.isVisible()
            content.setVisible(vis)
            hdr.setText(_label(vis))

        hdr.clicked.connect(_toggle)
        wl.addWidget(hdr)
        wl.addWidget(content)
        return wrap

    def _generated_drafts(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        by_type: dict = {}
        for d in drafts:
            by_type.setdefault(d.get("action_type"), []).append(d)
        for atype in _SEND_TYPES:
            items = by_type.get(atype)
            if not items:
                continue
            gl = QLabel(f"{TYPE_LABEL.get(atype, atype)}  ({len(items)})")
            gl.setStyleSheet(f"color:{C_ACCENT}; font-size:10px; font-weight:700; border:none; margin-top:4px;")
            lay.addWidget(gl)
            for d in items:
                lay.addWidget(self._card(d))
        return w

    def _cards_list(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        for d in drafts:
            lay.addWidget(self._card(d))
        return w

    def _compact_list(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        for d in drafts:
            lay.addWidget(self._compact_row(d))
        return w

    def _compact_row(self, d: dict) -> QWidget:
        atype = d.get("action_type", "")
        status = d.get("status", "")
        st_color = {"executed": C_SUCCESS, "failed": C_DANGER, "dismissed": C_MUTED}.get(status, C_TEXT)
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background:{C_BG}; border:1px solid {C_BORDER}; border-radius:6px; }}")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(8)
        t = QLabel(TYPE_LABEL.get(atype, atype))
        t.setStyleSheet(f"color:{C_ACCENT}; font-size:10px; font-weight:700; border:none;")
        h.addWidget(t)
        title = QLabel(d.get("display_title") or "(untitled)")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:11px; border:none;")
        title.setWordWrap(True)
        h.addWidget(title, 1)
        st = QLabel(status)
        st.setStyleSheet(f"color:{st_color}; font-size:10px; border:none;")
        h.addWidget(st)
        return row

    def _knowledge_list(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        for d in drafts:
            conf = d.get("confidence")
            bits = [f"v{d.get('version', '—')}"]
            if conf is not None:
                bits.append(f"{round(conf * 100)}% confidence")
            if d.get("conflict_flag"):
                bits.append("⚠ conflict")
            row = QFrame()
            row.setStyleSheet(f"QFrame {{ background:{C_BG}; border:1px solid {C_BORDER}; border-radius:6px; }}")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 5, 8, 5)
            h.setSpacing(8)
            ttl = QLabel(d.get("display_title") or "(untitled)")
            ttl.setStyleSheet(f"color:{C_TEXT}; font-size:11px; border:none;")
            ttl.setWordWrap(True)
            meta = QLabel("  ·  ".join(bits))
            meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
            h.addWidget(ttl, 1)
            h.addWidget(meta)
            lay.addWidget(row)
        return w

    def _exec_queue(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        by_status: dict = {}
        for d in drafts:
            by_status.setdefault(d.get("status"), []).append(d)
        for st, label in EXEC_STATUS_ORDER:
            items = by_status.get(st)
            if not items:
                continue
            gl = QLabel(f"{label}  ({len(items)})")
            gl.setStyleSheet(f"color:{C_MUTED}; font-size:10px; font-weight:700; border:none; margin-top:2px;")
            lay.addWidget(gl)
            for d in items:
                lay.addWidget(self._compact_row(d))
        return w

    def _audit_list(self, drafts: list) -> QWidget:
        w, lay = self._vbox()
        if not drafts:
            return self._none(lay, w)
        note = QLabel("Lifecycle overview — full per-draft audit timeline arrives in a later phase.")
        note.setStyleSheet(f"color:{C_MUTED}; font-size:9px; border:none;")
        note.setWordWrap(True)
        lay.addWidget(note)
        for d in sorted(drafts, key=lambda x: x.get("created_at") or ""):
            created = (d.get("created_at") or "")[:16].replace("T", " ")
            line = f"{created}  ·  {TYPE_LABEL.get(d.get('action_type'), d.get('action_type'))}  ·  {d.get('status')}"
            r = QLabel(line)
            r.setStyleSheet(f"color:{C_TEXT}; font-size:10px; border:none;")
            lay.addWidget(r)
        return w

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
            cb.setChecked(aid in self._selected)
            cb.stateChanged.connect(partial(self._toggle_sel, aid))
            top.addWidget(cb)
        type_lbl = QLabel(TYPE_LABEL.get(atype, atype))
        type_lbl.setStyleSheet(f"color:{C_ACCENT}; font-size:10px; font-weight:700; border:none;")
        top.addWidget(type_lbl)
        st = QLabel(status)
        st_color = {"executed": C_SUCCESS, "failed": C_DANGER, "dismissed": C_MUTED}.get(status, C_TEXT)
        st.setStyleSheet(f"color:{st_color}; font-size:10px; border:none;")
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

        title = QLabel(d.get("display_title") or "(untitled)")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:12px; font-weight:600; border:none;")
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

        preview_text = d.get("preview") or (("Clarification: " + d["reason"]) if d.get("reason") else "")
        if preview_text:
            pv = QLabel(preview_text)
            pv.setStyleSheet(f"color:{C_TEXT}; font-size:11px; border:none;")
            pv.setWordWrap(True)
            v.addWidget(pv)

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
        row.addWidget(_btn("Version History", C_MUTED, partial(self._version_history, aid)))
        if is_pending:
            if not is_clar:
                row.addWidget(_btn("Approve", C_SUCCESS, partial(self._approve, aid)))
                row.addWidget(_btn("Edit", C_MUTED, partial(self._edit, d)))
                row.addWidget(_btn("Regenerate", C_MUTED, partial(self._regenerate, aid)))
            row.addWidget(_btn("Reject" if not is_clar else "Dismiss", C_DANGER, partial(self._reject, aid)))
        row.addStretch(1)
        v.addLayout(row)
        return card

    # ── selection ───────────────────────────────────────────────────────────────
    def _toggle_sel(self, aid: int, state: int) -> None:
        if state == Qt.CheckState.Checked.value:
            self._selected.add(aid)
        else:
            self._selected.discard(aid)
        self._update_sel()

    def _update_sel(self) -> None:
        self._sel_lbl.setText(f"{len(self._selected)} selected")

    # ── actions ──────────────────────────────────────────────────────────────────
    def _approve(self, aid: int) -> None:
        self._run("action", "POST", f"/agent/actions/{aid}/approve")

    def _reject(self, aid: int) -> None:
        self._run("action", "POST", f"/agent/actions/{aid}/dismiss")

    def _regenerate(self, aid: int) -> None:
        self._run("action", "POST", f"/drafts/{aid}/regenerate", body={})

    def _version_history(self, aid: int) -> None:
        self._run("versions", "GET", f"/drafts/{aid}/version-history")

    def _approve_selected(self) -> None:
        if self._selected:
            self._run("action", "POST", "/agent/actions/approve-batch", body={"action_ids": list(self._selected)})

    def _reject_selected(self) -> None:
        if self._selected:
            self._run("action", "POST", "/agent/actions/dismiss-batch", body={"action_ids": list(self._selected)})

    def _approve_all(self) -> None:
        ids = self._all_pending()
        if ids:
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
