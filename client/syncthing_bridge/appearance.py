"""Shared styling for the selector and its tray panel."""
from PySide6.QtGui import QPalette


def apply_appearance(surface):
    dark = surface.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        bg, card, field, border, text, muted = '#171b24', '#212836', '#191f2b', '#38445a', '#eef3fc', '#abb9ce'
        hover, selected, accent = '#2b3548', '#294974', '#94c2ff'
        button, button_hover, on_button = '#9bc5ff', '#b6d5ff', '#102746'
    else:
        bg, card, field, border, text, muted = '#edf2f8', '#ffffff', '#f9fbfe', '#cbd6e5', '#16253c', '#4d607a'
        hover, selected, accent = '#edf4fe', '#dceaff', '#1558ae'
        button, button_hover, on_button = '#155dbb', '#114d9b', '#ffffff'

    surface.setStyleSheet(f"""
        QWidget#selectorSurface, QFrame#activitySurface {{ background: {bg}; }}
        QFrame[card='true'] {{ background: {card}; border: 1px solid {border}; border-radius: 12px; }}
        QLabel, QCheckBox {{ color: {text}; }}
        QLabel[role='title'] {{ font-size: 26px; font-weight: 700; }}
        QLabel[role='section'] {{ font-size: 15px; font-weight: 650; }}
        QLabel[role='muted'] {{ color: {muted}; }}
        QLineEdit, QComboBox, QPushButton {{
            background: {field}; color: {text}; border: 1px solid {border};
            border-radius: 7px; padding: 5px 9px; min-height: 21px;
        }}
        QComboBox {{ padding-right: 25px; }}
        QLineEdit:focus, QComboBox:focus, QPushButton:focus, QTreeWidget:focus {{ border-color: {accent}; }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton[primary='true'] {{ background: {button}; color: {on_button}; border-color: {button}; font-weight: 650; }}
        QPushButton[primary='true']:hover {{ background: {button_hover}; }}
        QPushButton:disabled, QCheckBox:disabled {{ color: {muted}; background: {bg}; }}
        QTreeWidget {{ background: {card}; color: {text}; border: 1px solid {border}; border-radius: 8px; }}
        QTreeWidget::item {{ padding: 3px 2px; }}
        QTreeWidget::item:hover {{ background: {hover}; }}
        QTreeWidget::item:selected {{ background: {selected}; color: {text}; }}
        QHeaderView::section {{ background: {field}; color: {muted}; padding: 6px 8px; border: 0; border-bottom: 1px solid {border}; }}
        QProgressBar {{ background: {field}; color: {text}; border: 1px solid {border}; border-radius: 6px; min-height: 15px; text-align: center; }}
        QProgressBar::chunk {{ background: {selected}; border-radius: 5px; }}
        QTabWidget::pane {{ border: 0; margin-top: 5px; }}
        QTabBar::tab {{ background: {field}; color: {muted}; padding: 7px 9px; margin-right: 3px; border-radius: 6px; }}
        QTabBar::tab:selected {{ background: {selected}; color: {text}; font-weight: 650; }}
        QTabBar::tab:hover:!selected {{ background: {hover}; }}
    """)
