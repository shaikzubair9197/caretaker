"""
Theme tokens and QSS stylesheet generation for the PySide6 UI.

Usage:
    from ui.theme import current_theme, build_stylesheet, DARK, LIGHT
    app.setStyleSheet(build_stylesheet(current_theme()))
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    bg: str
    surface: str
    surface2: str
    border: str
    fg: str
    subtle: str
    accent: str
    accent_hover: str
    success: str
    warn: str
    error: str
    table_hdr: str
    radius: str
    name: str  # "dark" | "light"
    # ── Modern treatment tokens (additive; default-provided for back-compat) ──
    elevated: str = ""        # hovered/raised surface (one step above `surface`)
    border_soft: str = ""     # quieter hairline border
    accent_soft: str = ""     # translucent accent fill (chips, soft buttons)
    accent_line: str = ""     # translucent accent border
    focus_ring: str = ""      # focus highlight
    radius_lg: str = "14px"   # cards / dialogs
    radius_pill: str = "999px"


DARK = Theme(
    bg="#0d1117",
    surface="#161b22",
    surface2="#21262d",
    border="#30363d",
    fg="#e6edf3",
    subtle="#8b949e",
    accent="#58a6ff",
    accent_hover="#79b8ff",
    success="#3fb950",
    warn="#d29922",
    error="#f85149",
    table_hdr="#21262d",
    radius="10px",
    name="dark",
    elevated="#1d2330",
    border_soft="#262c36",
    accent_soft="rgba(88, 166, 255, 0.12)",
    accent_line="rgba(88, 166, 255, 0.35)",
    focus_ring="rgba(88, 166, 255, 0.55)",
    radius_lg="14px",
)

LIGHT = Theme(
    bg="#ffffff",
    surface="#f6f8fa",
    surface2="#eaeef2",
    border="#d0d7de",
    fg="#1f2328",
    subtle="#656d76",
    accent="#0969da",
    accent_hover="#0550ae",
    success="#1a7f37",
    warn="#9a6700",
    error="#cf222e",
    table_hdr="#f6f8fa",
    radius="10px",
    name="light",
    elevated="#ffffff",
    border_soft="#e2e6ea",
    accent_soft="rgba(9, 105, 218, 0.10)",
    accent_line="rgba(9, 105, 218, 0.30)",
    focus_ring="rgba(9, 105, 218, 0.45)",
    radius_lg="14px",
)


def current_theme() -> Theme:
    """Detect system dark/light preference; fall back to DARK."""
    try:
        from PySide6.QtGui import Qt
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            hints = app.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return LIGHT
    except Exception:
        pass
    return DARK


def build_stylesheet(t: Theme) -> str:
    """Generate the global QSS stylesheet from a Theme.

    Modern dark/light treatment: soft elevation, hover feedback, pill chips,
    a clear primary/secondary/ghost button hierarchy, underline tabs, slim
    rounded scrollbars and focus rings — built on the same core palette so any
    inline-styled widgets stay visually consistent.
    """
    # Fall back gracefully if a custom Theme omitted the modern tokens.
    elevated = t.elevated or t.surface2
    border_soft = t.border_soft or t.border
    accent_soft = t.accent_soft or t.surface2
    accent_line = t.accent_line or t.accent
    focus_ring = t.focus_ring or t.accent
    radius_lg = t.radius_lg or t.radius
    radius_pill = t.radius_pill or "999px"
    return f"""
/* ── Base ── */
QWidget {{
    background-color: {t.bg};
    color: {t.fg};
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}
QToolTip {{
    background-color: {elevated};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 5px 8px;
}}

/* ── Dialog / Window ── */
QDialog, QMainWindow {{
    background-color: {t.bg};
}}

/* ── Frames and Panels ── */
QFrame {{
    background-color: transparent;
}}
QFrame[class="card"] {{
    background-color: {t.surface};
    border: 1px solid {border_soft};
    border-radius: {radius_lg};
}}
QFrame[class="card"]:hover {{
    background-color: {elevated};
    border-color: {accent_line};
}}
QFrame[class="card-accent"] {{
    background-color: {t.surface};
    border: 1px solid {accent_line};
    border-radius: {radius_lg};
}}

/* ── Labels ── */
QLabel {{
    background-color: transparent;
    color: {t.fg};
}}
QLabel[class="title"] {{
    font-size: 27px;
    font-weight: 800;
    color: {t.fg};
}}
QLabel[class="countdown"] {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: {t.accent};
}}
QLabel[class="meta"] {{
    font-size: 12px;
    color: {t.subtle};
}}
QLabel[class="section-header"] {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.10em;
    color: {t.subtle};
    text-transform: uppercase;
}}
QLabel[class="tag"], QLabel[class="chip"] {{
    font-size: 11px;
    font-weight: 600;
    color: {t.accent};
    background-color: {accent_soft};
    padding: 2px 10px;
    border: 1px solid {accent_line};
    border-radius: {radius_pill};
}}
QLabel[class="error"] {{
    color: {t.error};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {t.surface2};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {elevated};
    border-color: {t.subtle};
}}
QPushButton:pressed {{
    background-color: {t.bg};
}}
QPushButton:disabled {{
    color: {t.subtle};
    border-color: {border_soft};
}}
QPushButton[class="accent"] {{
    background-color: {t.accent};
    color: {t.bg};
    border-color: {t.accent};
    font-weight: 700;
}}
QPushButton[class="accent"]:hover {{
    background-color: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton[class="accent"]:pressed {{
    background-color: {t.accent};
}}
QPushButton[class="ghost"] {{
    background-color: transparent;
    border-color: transparent;
    color: {t.subtle};
    border-radius: {radius_pill};
    padding: 5px 12px;
    font-weight: 600;
}}
QPushButton[class="ghost"]:hover {{
    background-color: {accent_soft};
    color: {t.accent};
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    background-color: {t.bg};
    border: 1px solid {border_soft};
    border-top: none;
    border-radius: 0 0 {radius_lg} {radius_lg};
}}
QTabBar {{
    background-color: transparent;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {t.subtle};
    padding: 9px 18px;
    border-bottom: 2px solid transparent;
    font-size: 13px;
    font-weight: 600;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {t.fg};
    border-bottom: 2px solid {t.accent};
    font-weight: 700;
}}
QTabBar::tab:hover {{
    color: {t.fg};
    background-color: {t.surface};
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {border_soft};
    width: 1px;
    height: 1px;
}}
QSplitter::handle:hover {{
    background-color: {accent_line};
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.border};
    border-radius: 5px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.subtle};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t.border};
    border-radius: 5px;
    min-width: 36px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t.subtle};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Text Edit / Browser ── */
QTextEdit, QTextBrowser {{
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {border_soft};
    border-radius: {t.radius};
    padding: 14px;
    selection-background-color: {t.accent};
    selection-color: {t.bg};
    font-size: 13px;
    line-height: 1.65;
}}
QTextEdit:focus, QTextBrowser:focus {{
    border: 1px solid {focus_ring};
}}

/* ── Line Edit (search bar) ── */
QLineEdit {{
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    selection-background-color: {t.accent};
}}
QLineEdit:focus {{
    border: 1px solid {focus_ring};
}}
QLineEdit:disabled {{
    color: {t.subtle};
}}

/* ── Combo Box ── */
QComboBox {{
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 12px;
    min-width: 80px;
}}
QComboBox:hover {{
    border-color: {t.subtle};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.subtle};
    width: 0;
    height: 0;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {elevated};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    selection-background-color: {accent_soft};
    selection-color: {t.fg};
    padding: 4px;
}}

/* ── List View ── */
QListView {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListView::item {{
    border-radius: 8px;
    padding: 3px 6px;
}}
QListView::item:hover {{
    background-color: {t.surface};
}}
QListView::item:selected {{
    background-color: {accent_soft};
    color: {t.fg};
}}

/* ── Table View ── */
QTableView {{
    background-color: {t.surface};
    gridline-color: {border_soft};
    border: none;
    alternate-background-color: {t.bg};
    selection-background-color: {t.accent};
    selection-color: {t.bg};
    font-size: 12px;
}}
QTableView QTableCornerButton::section {{
    background-color: {t.table_hdr};
    border: 1px solid {t.border};
}}
QHeaderView::section {{
    background-color: {t.table_hdr};
    color: {t.subtle};
    font-weight: 700;
    font-size: 11px;
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid {t.border};
    border-right: 1px solid {border_soft};
}}
QHeaderView::section:checked {{
    background-color: {t.surface2};
    color: {t.accent};
}}

/* ── Graphics View ── */
QGraphicsView {{
    background-color: {t.surface2};
    border: none;
}}

/* ── Tool bar area ── */
QFrame[class="toolbar"] {{
    background-color: {t.surface};
    border-bottom: 1px solid {border_soft};
    border-radius: 0;
    padding: 6px 10px;
}}

/* ── Status bar / Info bar ── */
QFrame[class="statusbar"] {{
    background-color: {t.surface2};
    border-top: 1px solid {border_soft};
    padding: 4px 10px;
}}

/* ── Loading / spinner area ── */
QFrame[class="loading"] {{
    background-color: {t.bg};
}}
"""
