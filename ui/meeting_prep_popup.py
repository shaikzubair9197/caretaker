"""
Meeting prep overlay UI.


Fullscreen dark tkinter window that surfaces precomputed meeting context
(title, time, organizer, join link, agenda) ~15 minutes before a meeting.
All data comes from the deterministic /meeting/prep API — no LLM, no masking.


Launched by daemon/meeting_scheduler.py or directly:
    python ui/meeting_prep_popup.py              # next meeting in the 15-min window
    python ui/meeting_prep_popup.py --force      # soonest meeting (demo, any time)
    python ui/meeting_prep_popup.py --event <id> # a specific event
"""


import argparse
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import font as tkfont


import requests


API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}


# ── Colour palette (matches ui/panic_dump.py) ──────────────────────────────────
BG = "#0d1117"
FG = "#e6edf3"
ACCENT = "#58a6ff"
SUBTLE = "#8b949e"
SUCCESS = "#3fb950"
WARN = "#d29922"
ERROR = "#f85149"
INPUT_BG = "#161b22"
BORDER = "#30363d"



def _open_url(url: str) -> None:
    """Open a URL reliably on Linux/Mac/Windows without silent failure."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", url])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            webbrowser.open(url)
    except Exception:
        webbrowser.open(url)



def _fetch(force: bool, event_id: str | None) -> dict:
    """Return the prep API JSON, or {'_error': msg}."""
    try:
        if event_id:
            url = f"{API_BASE}/meeting/prep/{event_id}"
            params = {}
        else:
            url = f"{API_BASE}/meeting/prep/next"
            params = {"force": "true"} if force else {}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"_error": "Could not reach Caretaker API — is it running?"}
    except Exception as e:  # noqa: BLE001
        return {"_error": f"Error: {e}"}



class MeetingPrepUI:


    def __init__(self, root: tk.Tk, snapshot: dict | None, error: str | None):
        self.root = root
        self.snapshot = snapshot
        self.error = error
        self._setup_window()
        self._build()


    def _setup_window(self):
        self.root.title("Caretaker — Meeting Prep")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", lambda e: self.root.destroy())


    def _build(self):
        for w in self.root.winfo_children():
            w.destroy()


        if self.error:
            self._centered(self.error, ERROR)
            return
        if not self.snapshot:
            self._centered("No meeting needs prep right now.", SUBTLE)
            return


        s = self.snapshot
        pad = {"padx": 60}


        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(40, 0))


        mins = s.get("minutes_until")
        countdown = (
            f"Meeting starts in {mins} minutes" if mins is not None else "Upcoming meeting"
        )
        tk.Label(
            header, text=countdown,
            font=tkfont.Font(family="SF Pro Text", size=14),
            bg=BG, fg=ACCENT,
        ).pack(**pad, anchor="w")


        tk.Label(
            header, text=s.get("title", "(no subject)"),
            font=tkfont.Font(family="SF Pro Display", size=28, weight="bold"),
            bg=BG, fg=FG, wraplength=1100, justify="left",
        ).pack(**pad, anchor="w", pady=(6, 0))


        meta = []
        if s.get("start_display"):
            meta.append(s["start_display"])
        org = (s.get("organizer") or {}).get("name")
        if org:
            meta.append(f"Organized by {org}")
        if meta:
            tk.Label(
                header, text="   ·   ".join(meta),
                font=tkfont.Font(family="SF Pro Text", size=13),
                bg=BG, fg=SUBTLE,
            ).pack(**pad, anchor="w", pady=(6, 24))


        # Body — scrollable context
        canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=BG)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=60)
        scrollbar.pack(side="right", fill="y")


        self._render_context(body, s)


        # Action bar
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=60, pady=20)


        tk.Button(
            bottom, text="Dismiss",
            font=tkfont.Font(family="SF Pro Text", size=13),
            bg=BORDER, fg=FG, activebackground=INPUT_BG,
            relief="flat", padx=20, pady=8, cursor="hand2",
            command=self.root.destroy,
        ).pack(side="right")


        join_url = s.get("join_url")
        if join_url:
            tk.Button(
                bottom, text="Join Meeting",
                font=tkfont.Font(family="SF Pro Text", size=13, weight="bold"),
                bg=ACCENT, fg=BG, activebackground=FG,
                relief="flat", padx=20, pady=8, cursor="hand2",
                command=lambda u=join_url: self._join_meeting(u),
            ).pack(side="right", padx=(0, 8))
        else:
            tk.Label(
                bottom, text="No online meeting link found",
                font=tkfont.Font(family="SF Pro Text", size=12),
                bg=BG, fg=SUBTLE,
            ).pack(side="right", padx=(0, 12))


    def _render_context(self, parent: tk.Frame, s: dict):
        # Agenda
        agenda = (s.get("agenda") or "").strip()
        if agenda:
            self._section_title(parent, "Agenda")
            block = tk.Frame(parent, bg=INPUT_BG, padx=16, pady=12)
            block.pack(fill="x", pady=(0, 16))
            tk.Label(
                block, text=agenda,
                font=tkfont.Font(family="SF Pro Text", size=13),
                bg=INPUT_BG, fg=FG, wraplength=980, justify="left",
            ).pack(anchor="w")


        # Attendees
        attendees = s.get("attendees") or []
        if attendees:
            names = ", ".join(a.get("name") or a.get("address") or "" for a in attendees)
            self._section_title(parent, f"Attendees ({len(attendees)})")
            tk.Label(
                parent, text=names,
                font=tkfont.Font(family="SF Pro Text", size=12),
                bg=BG, fg=SUBTLE, wraplength=980, justify="left",
            ).pack(anchor="w", pady=(0, 16))


        # Related emails (Phase 2)
        emails = s.get("related_emails") or []
        if emails:
            self._section_title(parent, "Related emails")
            for em in emails:
                row = tk.Frame(parent, bg=INPUT_BG, padx=16, pady=10)
                row.pack(fill="x", pady=4)
                row.configure(cursor="hand2")
                row.bind("<Button-1>", lambda e, email=em: self._show_related_email(email))

                subject_label = tk.Label(
                    row, text=em.get("subject", ""),
                    font=tkfont.Font(family="SF Pro Text", size=13),
                    bg=INPUT_BG, fg=FG, wraplength=940, justify="left",
                )
                subject_label.pack(anchor="w")
                subject_label.bind("<Button-1>", lambda e, email=em: self._show_related_email(email))

                sub = "  ·  ".join(p for p in (em.get("from"), em.get("received")) if p)
                if sub:
                    meta_label = tk.Label(
                        row, text=sub,
                        font=tkfont.Font(family="SF Pro Text", size=11),
                        bg=INPUT_BG, fg=SUBTLE,
                    )
                    meta_label.pack(anchor="w", pady=(2, 0))
                    meta_label.bind("<Button-1>", lambda e, email=em: self._show_related_email(email))

                if em.get("reason"):
                    reason_label = tk.Label(
                        row, text=em["reason"],
                        font=tkfont.Font(family="SF Pro Text", size=10),
                        bg=INPUT_BG, fg=ACCENT,
                    )
                    reason_label.pack(anchor="w", pady=(2, 0))
                    reason_label.bind("<Button-1>", lambda e, email=em: self._show_related_email(email))

                tk.Button(
                    row, text="Preview",
                    font=tkfont.Font(family="SF Pro Text", size=11),
                    bg=BORDER, fg=FG, activebackground=ACCENT,
                    relief="flat", padx=12, pady=4, cursor="hand2",
                    command=lambda email=em: self._show_related_email(email),
                ).pack(side="right")
            # spacer
            tk.Frame(parent, bg=BG, height=12).pack(fill="x")


        # Documents
        docs = s.get("documents") or {}
        doc_items = docs.get("items") or []
        if not doc_items:
            self._section_title(parent, "Documents")
            tk.Label(
                parent, text="No documents or attachments found for this meeting.",
                font=tkfont.Font(family="SF Pro Text", size=12),
                bg=BG, fg=SUBTLE,
            ).pack(anchor="w", pady=(0, 16))
        elif doc_items:
            high = docs.get("confidence") == "HIGH"
            self._section_title(parent, "Document" if high else "Possibly relevant documents")
            for d in doc_items:
                # HIGH-confidence exact doc gets an accent border; LOW is plain.
                row = tk.Frame(
                    parent, bg=INPUT_BG, padx=16, pady=10,
                    highlightthickness=(1 if high else 0), highlightbackground=ACCENT,
                )
                row.pack(fill="x", pady=4)
                tk.Label(
                    row, text="📄  " + d.get("label", ""),
                    font=tkfont.Font(family="SF Pro Text", size=13,
                                     weight=("bold" if high else "normal")),
                    bg=INPUT_BG, fg=FG, wraplength=900, justify="left",
                ).pack(side="left", anchor="w")
                if d.get("url"):
                    url = d["url"]
                    tk.Button(
                        row, text="Open",
                        font=tkfont.Font(family="SF Pro Text", size=11),
                        bg=BORDER, fg=FG, activebackground=ACCENT,
                        relief="flat", padx=12, pady=4, cursor="hand2",
                        command=lambda u=url: _open_url(u),
                    ).pack(side="right")
                if d.get("reason"):
                    tk.Label(
                        row, text=d["reason"],
                        font=tkfont.Font(family="SF Pro Text", size=10),
                        bg=INPUT_BG, fg=ACCENT,
                    ).pack(side="left", anchor="w", padx=(10, 0))


    def _section_title(self, parent: tk.Frame, text: str):
        tk.Label(
            parent, text=text,
            font=tkfont.Font(family="SF Pro Text", size=12, weight="bold"),
            bg=BG, fg=ACCENT,
        ).pack(anchor="w", pady=(0, 6))

    def _show_related_email(self, email: dict):
        preview = email.get("body_preview") or "No preview available for this email."
        window = tk.Toplevel(self.root)
        window.title(email.get("subject", "Related email"))
        window.configure(bg=BG)
        window.attributes("-topmost", True)

        header = tk.Frame(window, bg=BG)
        header.pack(fill="x", padx=24, pady=20)
        tk.Label(
            header, text=email.get("subject", "(no subject)"),
            font=tkfont.Font(family="SF Pro Display", size=18, weight="bold"),
            bg=BG, fg=FG, wraplength=920, justify="left",
        ).pack(anchor="w")
        sub = "  ·  ".join(p for p in (email.get("from"), email.get("received")) if p)
        if sub:
            tk.Label(
                header, text=sub,
                font=tkfont.Font(family="SF Pro Text", size=12),
                bg=BG, fg=SUBTLE,
            ).pack(anchor="w", pady=(8, 0))
        if email.get("reason"):
            tk.Label(
                header, text=email["reason"],
                font=tkfont.Font(family="SF Pro Text", size=11),
                bg=BG, fg=ACCENT,
            ).pack(anchor="w", pady=(8, 0))

        body_frame = tk.Frame(window, bg=BG)
        body_frame.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        text_widget = tk.Text(
            body_frame, wrap="word", bg=INPUT_BG, fg=FG,
            relief="flat", padx=12, pady=12, font=tkfont.Font(family="SF Pro Text", size=12),
        )
        text_widget.insert("1.0", preview)
        text_widget.configure(state="disabled")
        text_widget.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(body_frame, command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        action_bar = tk.Frame(window, bg=BG)
        action_bar.pack(fill="x", padx=24, pady=(0, 24))
        tk.Button(
            action_bar, text="Close",
            font=tkfont.Font(family="SF Pro Text", size=12),
            bg=BORDER, fg=FG, activebackground=INPUT_BG,
            relief="flat", padx=16, pady=8, cursor="hand2",
            command=window.destroy,
        ).pack(side="right")


    def _centered(self, message: str, color: str):
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(expand=True)
        tk.Label(
            frame, text=message,
            font=tkfont.Font(family="SF Pro Text", size=16),
            bg=BG, fg=color,
        ).pack()
        tk.Label(
            frame, text="Press Esc to close",
            font=tkfont.Font(family="SF Pro Text", size=12),
            bg=BG, fg=SUBTLE,
        ).pack(pady=(12, 0))


    def _join_meeting(self, url: str):
        """Close the fullscreen, topmost window before opening the meeting URL."""
        self.root.destroy()
        _open_url(url)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Soonest meeting, ignore window.")
    parser.add_argument("--event", default=None, help="Specific calendar event external_id.")
    args = parser.parse_args()


    payload = _fetch(args.force, args.event)
    error = payload.get("_error")
    snapshot = None if error else payload.get("event")


    root = tk.Tk()
    MeetingPrepUI(root, snapshot, error)
    root.mainloop()



if __name__ == "__main__":
    main()