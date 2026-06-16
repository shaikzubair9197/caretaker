"""
Panic dump overlay UI.

Fullscreen dark tkinter window that lets the user type a stream-of-consciousness
brain dump. On submit it POSTs to /panic_dump/ and shows the structured results
in a confirmation panel before auto-closing.

Launched by daemon/hotkey.py or directly:
    python ui/panic_dump.py
"""

import json
import os
import tkinter as tk
from tkinter import font as tkfont

import requests

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

# ── Colour palette ────────────────────────────────────────────────────────────
BG = "#0d1117"
FG = "#e6edf3"
ACCENT = "#58a6ff"
SUBTLE = "#8b949e"
SUCCESS = "#3fb950"
WARN = "#d29922"
ERROR = "#f85149"
INPUT_BG = "#161b22"
BORDER = "#30363d"


class PanicDumpUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        self._setup_window()
        self._build_input_screen()

    def _setup_window(self):
        self.root.title("Caretaker — Brain Dump")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def _build_input_screen(self):
        self._clear()

        pad = {"padx": 60, "pady": 0}

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(40, 0))

        tk.Label(
            header,
            text="Brain Dump",
            font=tkfont.Font(family="SF Pro Display", size=28, weight="bold"),
            bg=BG,
            fg=FG,
        ).pack(**pad, anchor="w")

        tk.Label(
            header,
            text="Type everything on your mind. Press Ctrl+Enter to process.",
            font=tkfont.Font(family="SF Pro Text", size=13),
            bg=BG,
            fg=SUBTLE,
        ).pack(**pad, anchor="w", pady=(4, 24))

        # Text input area
        input_frame = tk.Frame(self.root, bg=BORDER)
        input_frame.pack(fill="both", expand=True, padx=60, pady=0)

        self.text_area = tk.Text(
            input_frame,
            font=tkfont.Font(family="Menlo", size=14),
            bg=INPUT_BG,
            fg=FG,
            insertbackground=ACCENT,
            relief="flat",
            wrap="word",
            padx=20,
            pady=20,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.text_area.pack(fill="both", expand=True)
        self.text_area.focus_set()
        self.text_area.bind("<Control-Return>", self._on_submit)

        # Status / button row
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=60, pady=20)

        self.status_label = tk.Label(
            bottom,
            text="Ctrl+Enter to submit  ·  Esc to close",
            font=tkfont.Font(family="SF Pro Text", size=12),
            bg=BG,
            fg=SUBTLE,
        )
        self.status_label.pack(side="left")

        tk.Button(
            bottom,
            text="Submit",
            font=tkfont.Font(family="SF Pro Text", size=13, weight="bold"),
            bg=ACCENT,
            fg=BG,
            activebackground=FG,
            relief="flat",
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._on_submit,
        ).pack(side="right")

    def _on_submit(self, event=None):
        text = self.text_area.get("1.0", "end").strip()
        if not text:
            return

        self._set_status("Processing…", color=SUBTLE)
        self.root.update()

        try:
            resp = requests.post(
                f"{API_BASE}/panic_dump/",
                json={"text": text},
                headers=_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._show_results(text, data)
        except requests.exceptions.ConnectionError:
            self._set_status("Could not reach Caretaker API — is it running?", color=ERROR)
        except Exception as e:
            self._set_status(f"Error: {e}", color=ERROR)

    def _show_results(self, original_text: str, data: dict):
        self._clear()

        pad = {"padx": 60, "pady": 0}

        # Header
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(40, 0))

        tk.Label(
            header,
            text=f"Captured {data['item_count']} items",
            font=tkfont.Font(family="SF Pro Display", size=24, weight="bold"),
            bg=BG,
            fg=SUCCESS,
        ).pack(**pad, anchor="w")

        meta_parts = [f"Sensitivity: {data.get('sensitivity_label', 'PUBLIC')}"]
        if data.get("overload_detected"):
            meta_parts.append("Overload detected")
        if data.get("llm_used"):
            meta_parts.append("LLM classified")

        tk.Label(
            header,
            text="  ·  ".join(meta_parts),
            font=tkfont.Font(family="SF Pro Text", size=12),
            bg=BG,
            fg=SUBTLE,
        ).pack(**pad, anchor="w", pady=(4, 24))

        # Scrollable results frame
        canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        results_frame = tk.Frame(canvas, bg=BG)

        results_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window((0, 0), window=results_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=60)
        scrollbar.pack(side="right", fill="y")

        type_colors = {
            "task": ACCENT,
            "email": WARN,
            "reminder": SUCCESS,
            "follow_up": "#d2a8ff",
        }

        for item in data.get("items", []):
            row = tk.Frame(results_frame, bg=INPUT_BG, pady=12, padx=16)
            row.pack(fill="x", pady=4)

            ctype = item.get("commitment_type", "task")
            color = type_colors.get(ctype, SUBTLE)

            tk.Label(
                row,
                text=f"[{ctype.upper()}]",
                font=tkfont.Font(family="SF Pro Text", size=11, weight="bold"),
                bg=INPUT_BG,
                fg=color,
            ).pack(anchor="w")

            tk.Label(
                row,
                text=item.get("action", ""),
                font=tkfont.Font(family="SF Pro Text", size=13),
                bg=INPUT_BG,
                fg=FG,
                wraplength=900,
                justify="left",
            ).pack(anchor="w", pady=(2, 0))

            meta = []
            if item.get("person"):
                meta.append(f"→ {item['person']}")
            if item.get("due_hint"):
                meta.append(item["due_hint"])
            if item.get("priority") and item["priority"] != "medium":
                meta.append(f"priority: {item['priority']}")

            if meta:
                tk.Label(
                    row,
                    text="  ".join(meta),
                    font=tkfont.Font(family="SF Pro Text", size=11),
                    bg=INPUT_BG,
                    fg=SUBTLE,
                ).pack(anchor="w")

        # Close button
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=60, pady=20)

        tk.Button(
            bottom,
            text="Close",
            font=tkfont.Font(family="SF Pro Text", size=13),
            bg=BORDER,
            fg=FG,
            activebackground=INPUT_BG,
            relief="flat",
            padx=20,
            pady=8,
            cursor="hand2",
            command=self.root.destroy,
        ).pack(side="right")

        tk.Button(
            bottom,
            text="New Dump",
            font=tkfont.Font(family="SF Pro Text", size=13),
            bg=ACCENT,
            fg=BG,
            activebackground=FG,
            relief="flat",
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._build_input_screen,
        ).pack(side="right", padx=(0, 8))

    def _set_status(self, message: str, color: str = SUBTLE):
        if hasattr(self, "status_label"):
            self.status_label.configure(text=message, fg=color)

    def _clear(self):
        for widget in self.root.winfo_children():
            widget.destroy()


def main():
    root = tk.Tk()
    PanicDumpUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
