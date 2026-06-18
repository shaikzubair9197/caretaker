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
    radius="8px",
    name="dark",
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
    radius="8px",
    name="light",
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
    """Generate the global QSS stylesheet from a Theme."""
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
    border: 1px solid {t.border};
    border-radius: {t.radius};
}}
QFrame[class="card-accent"] {{
    background-color: {t.surface};
    border: 1px solid {t.accent};
    border-radius: {t.radius};
}}

/* ── Labels ── */
QLabel {{
    background-color: transparent;
    color: {t.fg};
}}
QLabel[class="title"] {{
    font-size: 26px;
    font-weight: 700;
    color: {t.fg};
}}
QLabel[class="countdown"] {{
    font-size: 14px;
    color: {t.accent};
}}
QLabel[class="meta"] {{
    font-size: 12px;
    color: {t.subtle};
}}
QLabel[class="section-header"] {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: {t.subtle};
    text-transform: uppercase;
}}
QLabel[class="tag"] {{
    font-size: 11px;
    color: {t.accent};
    background-color: transparent;
    padding: 1px 6px;
    border: 1px solid {t.accent};
    border-radius: 4px;
}}
QLabel[class="error"] {{
    color: {t.error};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {t.surface2};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t.surface};
    border-color: {t.subtle};
}}
QPushButton:pressed {{
    background-color: {t.bg};
}}
QPushButton:disabled {{
    color: {t.subtle};
    border-color: {t.border};
}}
QPushButton[class="accent"] {{
    background-color: {t.accent};
    color: {t.bg};
    border-color: {t.accent};
    font-weight: 600;
}}
QPushButton[class="accent"]:hover {{
    background-color: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton[class="accent"]:pressed {{
    background-color: {t.accent};
    opacity: 0.85;
}}
QPushButton[class="ghost"] {{
    background-color: transparent;
    border-color: transparent;
    color: {t.subtle};
    padding: 4px 10px;
}}
QPushButton[class="ghost"]:hover {{
    background-color: {t.surface};
    color: {t.fg};
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    background-color: {t.bg};
    border: 1px solid {t.border};
    border-top: none;
    border-radius: 0 0 {t.radius} {t.radius};
}}
QTabBar {{
    background-color: transparent;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {t.subtle};
    padding: 8px 18px;
    border-bottom: 2px solid transparent;
    font-size: 13px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {t.fg};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {t.fg};
    background-color: {t.surface};
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {t.border};
    width: 1px;
    height: 1px;
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t.border};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.subtle};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {t.border};
    border-radius: 4px;
    min-width: 32px;
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
    border: none;
    border-radius: 0;
    padding: 12px;
    selection-background-color: {t.accent};
    selection-color: {t.bg};
    font-size: 13px;
    line-height: 1.6;
}}
QTextEdit:focus, QTextBrowser:focus {{
    border: 1px solid {t.accent};
}}

/* ── Line Edit (search bar) ── */
QLineEdit {{
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 13px;
    selection-background-color: {t.accent};
}}
QLineEdit:focus {{
    border-color: {t.accent};
}}
QLineEdit:disabled {{
    color: {t.subtle};
}}

/* ── Combo Box ── */
QComboBox {{
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 4px 8px;
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
    background-color: {t.surface};
    color: {t.fg};
    border: 1px solid {t.border};
    selection-background-color: {t.accent};
    selection-color: {t.bg};
    padding: 2px;
}}

/* ── List View ── */
QListView {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListView::item {{
    border-radius: 6px;
    padding: 2px 4px;
}}
QListView::item:hover {{
    background-color: {t.surface};
}}
QListView::item:selected {{
    background-color: {t.surface2};
    color: {t.fg};
}}

/* ── Table View ── */
QTableView {{
    background-color: {t.surface};
    gridline-color: {t.border};
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
    font-weight: 600;
    font-size: 11px;
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid {t.border};
    border-right: 1px solid {t.border};
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
    border-bottom: 1px solid {t.border};
    border-radius: 0;
    padding: 4px 8px;
}}

/* ── Status bar / Info bar ── */
QFrame[class="statusbar"] {{
    background-color: {t.surface2};
    border-top: 1px solid {t.border};
    padding: 2px 8px;
}}

/* ── Loading / spinner area ── */
QFrame[class="loading"] {{
    background-color: {t.bg};
}}
"""
