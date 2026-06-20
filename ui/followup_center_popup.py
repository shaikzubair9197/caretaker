"""
Follow-up Center — global desktop popup (Plan 3).

Reuses the Meeting Prep popup architecture verbatim: a frameless, always-on-top
PySide6 QDialog launched as its own process (by daemon/meeting_scheduler.py),
fetching from the local Caretaker API via a QThread worker and styled with
ui/theme.py. Unlike Meeting Prep (fullscreen), this docks to the RIGHT edge like
Copilot / ChatGPT Desktop — an assistant panel that floats over any work.

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
import os
import sys
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


class FollowupCenterDialog(QDialog):
    def __init__(self, meeting_id: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._meeting_id = meeting_id
        self._selected: set[int] = set()
        self._workers: list[_HttpWorker] = []
        self._data: dict = {}
        self._setup_window()
        self._build_shell()
        self.refresh()

    # ── window ───────────────────────────────────────────────────────────────
    def _setup_window(self) -> None:
        self.setWindowTitle("Caretaker — Follow-up Center")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(build_stylesheet(current_theme()))
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.setFixedWidth(PANEL_WIDTH)
        self.setGeometry(screen.right() - PANEL_WIDTH + 1, screen.top(), PANEL_WIDTH, screen.height())

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
        hl.addWidget(_btn("✕", C_MUTED, self.close))
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
        sec = QFrame()
        sec.setStyleSheet(f"QFrame {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px; }}")
        v = QVBoxLayout(sec)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        title = QLabel(m.get("subject") or "Meeting")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:13px; font-weight:700; border:none;")
        title.setWordWrap(True)
        v.addWidget(title)

        c = m.get("counts", {}) or {}
        meta_bits = []
        if m.get("meeting_start"):
            meta_bits.append(str(m["meeting_start"])[:16].replace("T", " "))
        meta_bits.append(f"{len(m.get('participant_tokens') or [])} participants")
        if c:
            meta_bits.append(
                f"{c.get('pending', 0)} pending · {c.get('clarification', 0)} clarify · "
                f"{c.get('failed', 0)} failed · {c.get('executed', 0)} executed"
            )
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        meta.setWordWrap(True)
        v.addWidget(meta)

        groups = m.get("groups", {}) or {}
        for g in GROUP_ORDER:
            items = groups.get(g) or []
            if not items:
                continue
            gl = QLabel(f"{GROUP_LABELS[g]}  ({len(items)})")
            gl.setStyleSheet(f"color:{C_MUTED}; font-size:10px; font-weight:700; border:none; margin-top:4px;")
            v.addWidget(gl)
            for d in items:
                v.addWidget(self._card(d))
        return sec

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
        meta = QLabel("   ".join(meta_bits))
        meta.setStyleSheet(f"color:{C_MUTED}; font-size:10px; border:none;")
        v.addWidget(meta)

        if is_pending:
            row = QHBoxLayout()
            row.setSpacing(6)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", type=int, default=None, help="restrict to one meeting_transcript_id")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = FollowupCenterDialog(meeting_id=args.meeting)
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
