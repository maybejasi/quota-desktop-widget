from __future__ import annotations

import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QFileSystemWatcher,
    QPoint,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from agy_bridge import install_bridge, is_bridge_installed, uninstall_bridge
from config import AppConfig, get_agy_cache_path, get_default_t3_caches_dir, save_config
from providers import (
    ProviderQuota,
    QuotaBucket,
    fetch_agy_quota,
    fetch_opencode_quota,
    make_demo_quotas,
)
from win_desktop import (
    IS_WINDOWS,
    apply_window_layer,
    bring_window_to_front,
    is_autostart_enabled,
    send_to_bottom,
    set_autostart,
)

FONT_FAMILY = "Microsoft YaHei UI"


def _quota_color(remaining_pct: float) -> tuple[QColor, QColor]:
    """Return (start_color, end_color) for remaining percentage."""
    if remaining_pct >= 50.0:
        return QColor("#10B981"), QColor("#34D399")  # Emerald
    if remaining_pct >= 20.0:
        return QColor("#F59E0B"), QColor("#FBBF24")  # Amber
    return QColor("#EF4444"), QColor("#F87171")  # Coral Red


class VectorIconButton(QPushButton):
    """Crisp monochrome vector icon button (no colorful OS emoji mismatch)."""

    def __init__(self, kind: str, tooltip: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind  # "refresh" | "lock" | "unlock" | "settings"
        self.dark = True
        self._hovered = False
        self.setToolTip(tooltip)
        self.setFixedSize(26, 26)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("background: transparent; border: none;")

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._hovered:
            bg = QColor(255, 255, 255, 26) if self.dark else QColor(15, 23, 42, 20)
            bp = QPainterPath()
            bp.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 7, 7)
            p.fillPath(bp, bg)

        if self.dark:
            fg = QColor("#FFFFFF") if self._hovered else QColor(203, 213, 225, 195)
        else:
            fg = QColor("#0F172A") if self._hovered else QColor(71, 85, 105, 205)

        pen = QPen(fg, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)

        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self.kind == "refresh":
            r = 5.5
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 35 * 16, 285 * 16)
            # Arrowhead at top-right
            p.drawLine(int(cx + 2), int(cy - 5), int(cx + 5), int(cy - 5))
            p.drawLine(int(cx + 5), int(cy - 5), int(cx + 5), int(cy - 2))
        elif self.kind in ("lock", "unlock"):
            # Lock body
            body = QRectF(cx - 4.8, cy - 0.5, 9.6, 6.8)
            p.drawRoundedRect(body, 2.0, 2.0)
            # Shackle
            if self.kind == "lock":
                p.drawArc(QRectF(cx - 3.0, cy - 6.0, 6.0, 6.5), 0, 180 * 16)
                p.drawLine(int(cx - 3), int(cy - 3), int(cx - 3), int(cy - 1))
                p.drawLine(int(cx + 3), int(cy - 3), int(cx + 3), int(cy - 1))
            else:
                p.drawArc(QRectF(cx - 3.0, cy - 6.8, 6.0, 6.5), 25 * 16, 155 * 16)
                p.drawLine(int(cx - 3), int(cy - 4), int(cx - 3), int(cy - 1))
        elif self.kind == "settings":
            # Clean sliders / gear icon: inner ring + 6 short spokes
            p.drawEllipse(QRectF(cx - 2.6, cy - 2.6, 5.2, 5.2))
            import math

            for deg in range(0, 360, 60):
                rad = math.radians(deg)
                x1 = cx + math.cos(rad) * 4.5
                y1 = cy + math.sin(rad) * 4.5
                x2 = cx + math.cos(rad) * 6.3
                y2 = cy + math.sin(rad) * 6.3
                p.drawLine(QRectF(x1, y1, x2 - x1, y2 - y1).topLeft(), QRectF(x1, y1, x2 - x1, y2 - y1).bottomRight())


class BrandIcon(QWidget):
    """Crisp macOS-style rounded-square gradient app badge."""

    def __init__(self, provider: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.setFixedSize(26, 26)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)

        grad = QLinearGradient(0, 0, self.width(), self.height())
        if self.provider == "agy":
            grad.setColorAt(0.0, QColor("#6366F1"))
            grad.setColorAt(1.0, QColor("#3B82F6"))
            glyph = "A"
        else:
            grad.setColorAt(0.0, QColor("#10B981"))
            grad.setColorAt(1.0, QColor("#06B6D4"))
            glyph = "Go"

        p.fillPath(path, grad)

        # Subtle top gloss
        p.setPen(QPen(QColor(255, 255, 255, 65), 1.0))
        p.drawPath(path)

        # Glyph
        p.setPen(QColor("#FFFFFF"))
        font = QFont(FONT_FAMILY, 9 if len(glyph) > 1 else 10, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)


class RingGauge(QWidget):
    """iOS/macOS style circular remaining-quota ring gauge."""

    def __init__(self, dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.dark = dark
        self.value: Optional[float] = None
        self.setFixedSize(40, 40)

    def set_value(self, value: Optional[float], dark: bool) -> None:
        self.value = value
        self.dark = dark
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin = 4.0
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        track_color = QColor(255, 255, 255, 30) if self.dark else QColor(15, 23, 42, 24)
        pen_track = QPen(track_color, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_track)
        p.drawArc(rect, 0, 360 * 16)

        if self.value is not None:
            pct = max(0.0, min(100.0, float(self.value)))
            c1, c2 = _quota_color(pct)
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            pen_val = QPen(grad, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_val)
            span = int(-round(pct / 100.0 * 360.0 * 16.0))
            if span != 0:
                p.drawArc(rect, 90 * 16, span)

            text_color = QColor("#F8FAFC") if self.dark else QColor("#0F172A")
            p.setPen(text_color)
            p.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Bold))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{pct:.0f}%")
        else:
            muted = QColor(255, 255, 255, 95) if self.dark else QColor(15, 23, 42, 95)
            p.setPen(muted)
            p.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Medium))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "--")


class SmoothProgressBar(QWidget):
    """Sleek rounded pill bar with smooth gradient fill."""

    def __init__(self, remaining_pct: float = 0.0, dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.remaining_pct = remaining_pct
        self.dark = dark
        self.setFixedHeight(7)

    def set_state(self, remaining_pct: float, dark: bool) -> None:
        self.remaining_pct = max(0.0, min(100.0, float(remaining_pct)))
        self.dark = dark
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        radius = h / 2.0

        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        track_color = QColor(255, 255, 255, 24) if self.dark else QColor(15, 23, 42, 20)
        p.fillPath(track_path, track_color)

        fill_w = w * (self.remaining_pct / 100.0)
        if fill_w > 0.5:
            fill_w = max(fill_w, h)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(QRectF(0, 0, min(w, fill_w), h), radius, radius)
            c1, c2 = _quota_color(self.remaining_pct)
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.fillPath(fill_path, grad)


class ProviderCard(QFrame):
    """Frosted inner card for a single subscription provider (AGY or OpenCode Go)."""

    action_clicked = Signal(str)  # provider id

    def __init__(self, provider: str, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.dark = True
        self._quota: Optional[ProviderQuota] = None

        self.setObjectName("providerCard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(13, 11, 13, 12)
        outer.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(9)

        self.icon = BrandIcon(provider, self)
        header.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(1)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(QFont(FONT_FAMILY, 10, QFont.Weight.DemiBold))
        title_row.addWidget(self.title_lbl)

        self.badge_lbl = QLabel("加载中")
        self.badge_lbl.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Medium))
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(self.badge_lbl)
        title_row.addStretch(1)

        self.subtitle_lbl = QLabel("")
        self.subtitle_lbl.setFont(QFont(FONT_FAMILY, 8))
        self.subtitle_lbl.setWordWrap(True)
        title_col.addLayout(title_row)
        title_col.addWidget(self.subtitle_lbl)

        header.addLayout(title_col, 1)

        self.ring = RingGauge(dark=True, parent=self)
        header.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(header)

        # Body container (dynamically rebuilt per update)
        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 2, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addLayout(self.body_layout)

        self.apply_theme(True)

    def apply_theme(self, dark: bool) -> None:
        self.dark = dark
        if dark:
            self.setStyleSheet(
                """
                QFrame#providerCard {
                    background-color: rgba(255, 255, 255, 16);
                    border: 1px solid rgba(255, 255, 255, 26);
                    border-radius: 15px;
                }
                """
            )
            self.title_lbl.setStyleSheet("color: #F8FAFC; background: transparent;")
            self.subtitle_lbl.setStyleSheet("color: rgba(226, 232, 240, 155); background: transparent;")
        else:
            self.setStyleSheet(
                """
                QFrame#providerCard {
                    background-color: rgba(255, 255, 255, 178);
                    border: 1px solid rgba(15, 23, 42, 20);
                    border-radius: 15px;
                }
                """
            )
            self.title_lbl.setStyleSheet("color: #0F172A; background: transparent;")
            self.subtitle_lbl.setStyleSheet("color: rgba(51, 65, 85, 175); background: transparent;")

        if self._quota is not None:
            self.set_quota(self._quota)

    def _clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_sublayout(item.layout())

    def _clear_sublayout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_sublayout(item.layout())

    def _style_badge(self, status: str) -> None:
        if status == "ok":
            bg = "rgba(16, 185, 129, 38)" if self.dark else "rgba(16, 185, 129, 30)"
            fg = "#34D399" if self.dark else "#047857"
        elif status in ("setup", "stale", "loading"):
            bg = "rgba(245, 158, 11, 36)" if self.dark else "rgba(245, 158, 11, 28)"
            fg = "#FBBF24" if self.dark else "#B45309"
        else:
            bg = "rgba(239, 68, 68, 38)" if self.dark else "rgba(239, 68, 68, 28)"
            fg = "#F87171" if self.dark else "#B91C1C"

        self.badge_lbl.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 7px; padding: 1px 7px;"
        )

    def set_quota(self, q: ProviderQuota) -> None:
        self._quota = q
        self.title_lbl.setText(q.title)
        self.badge_lbl.setText(q.badge or "状态")
        self._style_badge(q.status)

        if q.subtitle:
            self.subtitle_lbl.setText(q.subtitle)
            self.subtitle_lbl.setVisible(True)
        else:
            self.subtitle_lbl.setVisible(False)

        self._clear_body()

        if q.buckets:
            # Show lowest remaining percentage in the header ring for quick glance
            min_rem = min(b.remaining_pct for b in q.buckets)
            self.ring.set_value(min_rem, self.dark)
            self.ring.setVisible(True)

            for bucket in q.buckets:
                self.body_layout.addWidget(self._build_bucket_row(bucket, q.updated_at))
        else:
            self.ring.set_value(None, self.dark)
            self.ring.setVisible(q.status == "loading")

            msg = q.error or ("正在获取额度..." if q.status == "loading" else "暂无数据")
            lbl = QLabel(msg)
            lbl.setWordWrap(True)
            lbl.setFont(QFont(FONT_FAMILY, 8.5))
            color = "rgba(226, 232, 240, 195)" if self.dark else "rgba(51, 65, 85, 215)"
            lbl.setStyleSheet(f"color: {color}; background: transparent;")
            self.body_layout.addWidget(lbl)

            if q.needs_setup or q.status in ("error", "stale"):
                btn_text = (
                    "一键接入 / 配置 AGY"
                    if self.provider == "agy" and q.needs_setup
                    else ("配置 OpenCode API Key" if self.provider == "opencode" and q.needs_setup else "打开设置")
                )
                btn = QPushButton(btn_text)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.setFont(QFont(FONT_FAMILY, 8.5, QFont.Weight.Medium))
                if self.dark:
                    btn.setStyleSheet(
                        """
                        QPushButton {
                            background-color: rgba(99, 102, 241, 60);
                            color: #E0E7FF;
                            border: 1px solid rgba(129, 140, 248, 95);
                            border-radius: 9px;
                            padding: 5px 10px;
                        }
                        QPushButton:hover {
                            background-color: rgba(99, 102, 241, 95);
                        }
                        """
                    )
                else:
                    btn.setStyleSheet(
                        """
                        QPushButton {
                            background-color: rgba(79, 70, 229, 26);
                            color: #3730A3;
                            border: 1px solid rgba(79, 70, 229, 65);
                            border-radius: 9px;
                            padding: 5px 10px;
                        }
                        QPushButton:hover {
                            background-color: rgba(79, 70, 229, 44);
                        }
                        """
                    )
                btn.clicked.connect(lambda: self.action_clicked.emit(self.provider))
                self.body_layout.addWidget(btn)

    def _build_bucket_row(self, b: QuotaBucket, synced_at_iso: Optional[str]) -> QWidget:
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(row_widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        lbl_name = QLabel(b.label)
        lbl_name.setFont(QFont(FONT_FAMILY, 8.8, QFont.Weight.Medium))
        name_color = "#E2E8F0" if self.dark else "#1E293B"
        lbl_name.setStyleSheet(f"color: {name_color}; background: transparent;")
        top.addWidget(lbl_name)

        reset_txt = b.live_reset_text(synced_at_iso)
        if reset_txt:
            lbl_reset = QLabel(reset_txt)
            lbl_reset.setFont(QFont(FONT_FAMILY, 7.8))
            reset_color = "rgba(148, 163, 184, 210)" if self.dark else "rgba(100, 116, 139, 220)"
            lbl_reset.setStyleSheet(f"color: {reset_color}; background: transparent;")
            top.addWidget(lbl_reset)

        top.addStretch(1)

        c1, c2 = _quota_color(b.remaining_pct)
        pct_color = c2.name() if self.dark else c1.darker(120).name()
        lbl_pct = QLabel(f"剩 {b.remaining_pct:.0f}%")
        lbl_pct.setFont(QFont(FONT_FAMILY, 8.8, QFont.Weight.Bold))
        lbl_pct.setStyleSheet(f"color: {pct_color}; background: transparent;")
        if b.detail:
            row_widget.setToolTip(f"{b.label}：{b.detail}（剩余 {b.remaining_pct:.1f}%）")
        top.addWidget(lbl_pct)

        vbox.addLayout(top)

        bar = SmoothProgressBar(b.remaining_pct, dark=self.dark)
        vbox.addWidget(bar)
        return row_widget


class RefreshWorker(QThread):
    finished_data = Signal(object, object)  # (ProviderQuota agy, ProviderQuota opencode)

    def __init__(self, cfg: AppConfig, demo_mode: bool = False) -> None:
        super().__init__()
        self.cfg = cfg
        self.demo_mode = demo_mode

    def run(self) -> None:
        if self.demo_mode:
            agy, oc = make_demo_quotas()
        else:
            agy = fetch_agy_quota(self.cfg)
            oc = fetch_opencode_quota(self.cfg)
        self.finished_data.emit(agy, oc)


class SingleTestWorker(QThread):
    test_done = Signal(object)  # ProviderQuota

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        res = fetch_opencode_quota(self.cfg)
        self.test_done.emit(res)


class SettingsDialog(QDialog):
    """Clean Chinese settings dialog for OpenCode Go, AGY bridge, and desktop widget options."""

    settings_saved = Signal(object)  # AppConfig

    def __init__(self, cfg: AppConfig, initial_tab: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cfg = AppConfig(**cfg.__dict__)
        self._test_worker: Optional[SingleTestWorker] = None

        self.setWindowTitle("订阅额度桌面小组件 · 设置")
        self.setMinimumWidth(470)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._apply_dialog_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_opencode_tab(), "OpenCode Go")
        self.tabs.addTab(self._build_agy_tab(), "Antigravity (AGY)")
        self.tabs.addTab(self._build_appearance_tab(), "外观与桌面")
        self.tabs.setCurrentIndex(max(0, min(2, initial_tab)))
        root.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存并应用")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: #161922;
                color: #F1F5F9;
                font-family: "{FONT_FAMILY}";
                font-size: 13px;
            }}
            QTabWidget::pane {{
                border: 1px solid rgba(255,255,255,24);
                border-radius: 10px;
                background-color: #1D212D;
                padding: 10px;
            }}
            QTabBar::tab {{
                background: #1B1F2B;
                color: #94A3B8;
                padding: 8px 14px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
            QTabBar::tab:selected {{
                background: #282E40;
                color: #FFFFFF;
                font-weight: 600;
            }}
            QLabel {{ color: #E2E8F0; }}
            QLineEdit, QComboBox, QSpinBox {{
                background-color: #11141C;
                color: #F8FAFC;
                border: 1px solid rgba(255,255,255,34);
                border-radius: 7px;
                padding: 6px 8px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border: 1px solid #6366F1;
            }}
            QPushButton {{
                background-color: #272D3D;
                color: #F1F5F9;
                border: 1px solid rgba(255,255,255,28);
                border-radius: 7px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: #323A50; }}
            QPushButton#primaryBtn {{
                background-color: #4F46E5;
                border: 1px solid #6366F1;
                color: #FFFFFF;
                font-weight: 600;
            }}
            QPushButton#primaryBtn:hover {{ background-color: #6366F1; }}
            QCheckBox {{ color: #E2E8F0; spacing: 8px; }}
            """
        )

    def _build_opencode_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        hint = QLabel(
            "默认直接读取 T3 Code 本地缓存（~/.t3/caches/opencode.json），无需额外配置 API Key；"
            "若脱离 T3 Code 使用，也可切换为直连 OpenCode Go API。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 12px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self.oc_mode_combo = QComboBox()
        self.oc_mode_combo.addItem("直连 T3 Code 本地缓存（推荐，免配 Key）", "t3_cache")
        self.oc_mode_combo.addItem("直连 OpenCode Go API（需 API Key）", "api")
        idx = self.oc_mode_combo.findData(self.cfg.opencode_mode)
        self.oc_mode_combo.setCurrentIndex(max(0, idx))
        self.oc_mode_combo.currentIndexChanged.connect(self._sync_opencode_mode_fields)
        form.addRow("数据来源：", self.oc_mode_combo)

        t3_oc_exists = self.cfg.t3_opencode_cache_path().exists()
        self.oc_t3_status = QLabel(
            f"已检测到缓存：{self.cfg.t3_opencode_cache_path()}"
            if t3_oc_exists
            else f"未检测到：{self.cfg.t3_opencode_cache_path()}"
        )
        self.oc_t3_status.setWordWrap(True)
        self.oc_t3_status.setStyleSheet(
            "color: #34D399; font-size: 12px;" if t3_oc_exists else "color: #FBBF24; font-size: 12px;"
        )
        form.addRow("T3 缓存：", self.oc_t3_status)

        key_row = QHBoxLayout()
        self.oc_key_edit = QLineEdit(self.cfg.opencode_api_key)
        self.oc_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.oc_key_edit.setPlaceholderText("粘贴你的 OpenCode API Key（API 模式使用）")
        self.oc_toggle_btn = QPushButton("显示")
        self.oc_toggle_btn.setFixedWidth(56)

        def _toggle_key() -> None:
            if self.oc_key_edit.echoMode() == QLineEdit.EchoMode.Password:
                self.oc_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
                self.oc_toggle_btn.setText("隐藏")
            else:
                self.oc_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
                self.oc_toggle_btn.setText("显示")

        self.oc_toggle_btn.clicked.connect(_toggle_key)
        key_row.addWidget(self.oc_key_edit, 1)
        key_row.addWidget(self.oc_toggle_btn)
        form.addRow("API Key：", key_row)

        self.oc_endpoint_edit = QLineEdit(self.cfg.opencode_endpoint)
        form.addRow("用量接口：", self.oc_endpoint_edit)
        layout.addLayout(form)

        test_row = QHBoxLayout()
        self.oc_test_btn = QPushButton("测试连接 / 读取")
        self.oc_test_btn.clicked.connect(self._on_test_opencode)
        self.oc_test_lbl = QLabel("")
        self.oc_test_lbl.setWordWrap(True)
        self.oc_test_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        test_row.addWidget(self.oc_test_btn)
        test_row.addWidget(self.oc_test_lbl, 1)
        layout.addLayout(test_row)
        layout.addStretch(1)

        self._sync_opencode_mode_fields()
        return w

    def _sync_opencode_mode_fields(self) -> None:
        is_api = self.oc_mode_combo.currentData() == "api"
        self.oc_key_edit.setEnabled(is_api)
        self.oc_toggle_btn.setEnabled(is_api)
        self.oc_endpoint_edit.setEnabled(is_api)

    def _build_agy_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        hint = QLabel(
            "默认直接读取 T3 Code 本地缓存（~/.t3/caches/antigravity.json），实时同步 Gemini 与 Claude/GPT 5h 及周额度，无需额外登录。"
            "也支持官方 AGY CLI statusLine 桥接或自定义命令。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 12px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self.agy_mode_combo = QComboBox()
        self.agy_mode_combo.addItem("直连 T3 Code 本地缓存（推荐，免配置）", "t3_cache")
        self.agy_mode_combo.addItem("官方 AGY CLI StatusLine 自动同步", "statusline")
        self.agy_mode_combo.addItem("自定义命令行输出 JSON", "command")
        self.agy_mode_combo.addItem("读取自定义 JSON 文件", "json_file")
        idx = self.agy_mode_combo.findData(self.cfg.agy_mode)
        self.agy_mode_combo.setCurrentIndex(max(0, idx))
        self.agy_mode_combo.currentIndexChanged.connect(self._sync_agy_mode_fields)
        form.addRow("数据来源：", self.agy_mode_combo)

        t3_agy_exists = self.cfg.t3_agy_cache_path().exists()
        self.agy_t3_status = QLabel(
            f"已检测到缓存：{self.cfg.t3_agy_cache_path()}"
            if t3_agy_exists
            else f"未检测到：{self.cfg.t3_agy_cache_path()}"
        )
        self.agy_t3_status.setWordWrap(True)
        self.agy_t3_status.setStyleSheet(
            "color: #34D399; font-size: 12px;" if t3_agy_exists else "color: #FBBF24; font-size: 12px;"
        )
        form.addRow("T3 缓存：", self.agy_t3_status)

        self.agy_bridge_status = QLabel()
        form.addRow("CLI 桥接：", self.agy_bridge_status)

        bridge_btns = QHBoxLayout()
        self.install_bridge_btn = QPushButton("安装 AGY 桥接")
        self.install_bridge_btn.clicked.connect(self._on_install_bridge)

        self.uninstall_bridge_btn = QPushButton("卸载桥接")
        self.uninstall_bridge_btn.clicked.connect(self._on_uninstall_bridge)

        self.open_agy_btn = QPushButton("打开 agy 终端")
        self.open_agy_btn.clicked.connect(self._on_open_agy_terminal)

        bridge_btns.addWidget(self.install_bridge_btn)
        bridge_btns.addWidget(self.uninstall_bridge_btn)
        bridge_btns.addWidget(self.open_agy_btn)
        form.addRow("", bridge_btns)

        self.agy_cmd_edit = QLineEdit(self.cfg.agy_command)
        self.agy_cmd_edit.setPlaceholderText("例如：my-agy-quota-script --json")
        form.addRow("自定义命令：", self.agy_cmd_edit)

        json_row = QHBoxLayout()
        self.agy_json_edit = QLineEdit(self.cfg.agy_json_path)
        self.agy_json_edit.setPlaceholderText(str(get_agy_cache_path()))
        browse_btn = QPushButton("浏览...")
        browse_btn.setFixedWidth(68)
        browse_btn.clicked.connect(self._on_browse_json)
        json_row.addWidget(self.agy_json_edit, 1)
        json_row.addWidget(browse_btn)
        form.addRow("JSON 路径：", json_row)

        layout.addLayout(form)
        layout.addStretch(1)

        self._refresh_bridge_status()
        self._sync_agy_mode_fields()
        return w

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        form.setSpacing(10)

        self.layer_combo = QComboBox()
        self.layer_combo.addItem("贴附桌面底层（仿 Mac 小组件，不遮挡应用）", "desktop")
        self.layer_combo.addItem("始终置顶悬浮（浮在其他窗口上方）", "floating")
        self.layer_combo.addItem("普通窗口层级", "normal")
        idx = self.layer_combo.findData(self.cfg.layer_mode)
        self.layer_combo.setCurrentIndex(max(0, idx))
        form.addRow("桌面层级：", self.layer_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色毛玻璃（Dark）", "dark")
        self.theme_combo.addItem("浅色毛玻璃（Light）", "light")
        t_idx = self.theme_combo.findData(self.cfg.theme)
        self.theme_combo.setCurrentIndex(max(0, t_idx))
        form.addRow("主题风格：", self.theme_combo)

        op_row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(60, 100)
        self.opacity_slider.setValue(int(round(self.cfg.opacity * 100)))
        self.opacity_lbl = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_lbl.setFixedWidth(40)
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_lbl.setText(f"{v}%"))
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_lbl)
        form.addRow("卡片不透明度：", op_row)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(30, 3600)
        self.interval_spin.setSingleStep(30)
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setValue(self.cfg.refresh_interval_sec)
        form.addRow("自动刷新间隔：", self.interval_spin)

        self.locked_cb = QCheckBox("锁定桌面位置（防止误拖动）")
        self.locked_cb.setChecked(self.cfg.locked)
        form.addRow("", self.locked_cb)

        self.autostart_cb = QCheckBox("Windows 开机自动启动（静默后台运行）")
        self.autostart_cb.setChecked(is_autostart_enabled())
        form.addRow("", self.autostart_cb)

        layout.addLayout(form)
        layout.addStretch(1)
        return w

    def _refresh_bridge_status(self) -> None:
        installed = is_bridge_installed()
        cache_exists = get_agy_cache_path().exists()
        if installed and cache_exists:
            self.agy_bridge_status.setText("已安装且已有缓存数据")
            self.agy_bridge_status.setStyleSheet("color: #34D399; font-weight: 600;")
        elif installed:
            self.agy_bridge_status.setText("已安装（打开一次 agy 终端登录即可同步）")
            self.agy_bridge_status.setStyleSheet("color: #FBBF24; font-weight: 600;")
        else:
            self.agy_bridge_status.setText("尚未安装")
            self.agy_bridge_status.setStyleSheet("color: #F87171;")

    def _sync_agy_mode_fields(self) -> None:
        mode = self.agy_mode_combo.currentData()
        self.agy_cmd_edit.setEnabled(mode == "command")
        self.agy_json_edit.setEnabled(mode == "json_file")

    def _on_install_bridge(self) -> None:
        ok, msg = install_bridge()
        self._refresh_bridge_status()
        if ok:
            QMessageBox.information(self, "AGY 桥接安装成功", f"{msg}\n\n接下来点击「打开 agy 终端登录/刷新」，登录或输入 /usage 后桌面小组件就会自动显示额度。")
        else:
            QMessageBox.warning(self, "安装失败", msg)

    def _on_uninstall_bridge(self) -> None:
        ok, msg = uninstall_bridge()
        self._refresh_bridge_status()
        QMessageBox.information(self, "AGY 桥接", msg if ok else f"失败：{msg}")

    def _on_open_agy_terminal(self) -> None:
        if IS_WINDOWS:
            try:
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "agy"])
            except Exception as exc:
                QMessageBox.warning(self, "无法打开终端", str(exc))

    def _on_browse_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 AGY 额度 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)")
        if path:
            self.agy_json_edit.setText(path)

    def _on_test_opencode(self) -> None:
        tmp_cfg = AppConfig(**self.cfg.__dict__)
        tmp_cfg.opencode_mode = str(self.oc_mode_combo.currentData())
        tmp_cfg.opencode_api_key = self.oc_key_edit.text().strip()
        tmp_cfg.opencode_endpoint = self.oc_endpoint_edit.text().strip() or "https://opencode.ai/zen/go/v1/usage"
        self.oc_test_btn.setEnabled(False)
        self.oc_test_lbl.setText("正在读取测试...")
        self.oc_test_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")

        self._test_worker = SingleTestWorker(tmp_cfg)
        self._test_worker.test_done.connect(self._on_test_opencode_done)
        self._test_worker.start()

    def _on_test_opencode_done(self, res: ProviderQuota) -> None:
        self.oc_test_btn.setEnabled(True)
        if res.status == "ok" and res.buckets:
            summary = " · ".join(f"{b.label} 剩{b.remaining_pct:.0f}%" for b in res.buckets)
            self.oc_test_lbl.setText(f"读取成功：{summary}")
            self.oc_test_lbl.setStyleSheet("color: #34D399; font-size: 12px;")
        else:
            self.oc_test_lbl.setText(res.error or "读取失败")
            self.oc_test_lbl.setStyleSheet("color: #F87171; font-size: 12px;")

    def _on_save(self) -> None:
        self.cfg.opencode_mode = str(self.oc_mode_combo.currentData())
        self.cfg.opencode_api_key = self.oc_key_edit.text().strip()
        self.cfg.opencode_endpoint = self.oc_endpoint_edit.text().strip() or "https://opencode.ai/zen/go/v1/usage"
        self.cfg.agy_mode = str(self.agy_mode_combo.currentData())
        self.cfg.agy_command = self.agy_cmd_edit.text().strip()
        self.cfg.agy_json_path = self.agy_json_edit.text().strip()
        self.cfg.layer_mode = str(self.layer_combo.currentData())
        self.cfg.theme = str(self.theme_combo.currentData())
        self.cfg.opacity = self.opacity_slider.value() / 100.0
        self.cfg.refresh_interval_sec = self.interval_spin.value()
        self.cfg.locked = self.locked_cb.isChecked()

        save_config(self.cfg)
        set_autostart(self.autostart_cb.isChecked())
        self.settings_saved.emit(self.cfg)
        self.accept()


class QuotaWidgetWindow(QWidget):
    """Frameless frosted-glass desktop widget displaying AGY & OpenCode Go quotas."""

    quit_requested = Signal()

    SHADOW_MARGIN = 10
    WIDGET_WIDTH = 344

    def __init__(self, cfg: AppConfig, demo_mode: bool = False) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.demo_mode = demo_mode
        self._dragging = False
        self._drag_offset = QPoint()
        self._worker: Optional[RefreshWorker] = None
        self._last_agy: Optional[ProviderQuota] = None
        self._last_oc: Optional[ProviderQuota] = None
        self._settings_dlg: Optional[SettingsDialog] = None
        self._menu_open = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._apply_qt_flags()
        self.setFixedWidth(self.WIDGET_WIDTH)

        root = QVBoxLayout(self)
        m = self.SHADOW_MARGIN
        root.setContentsMargins(m + 14, m + 12, m + 14, m + 12)
        root.setSpacing(10)

        # Header bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 0)
        top_bar.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont(FONT_FAMILY, 8))
        self.status_dot.setStyleSheet("color: #34D399; background: transparent;")
        top_bar.addWidget(self.status_dot)

        self.header_title = QLabel("订阅额度")
        self.header_title.setFont(QFont(FONT_FAMILY, 9.5, QFont.Weight.DemiBold))
        top_bar.addWidget(self.header_title)

        top_bar.addStretch(1)

        self.lock_btn = VectorIconButton("unlock", "锁定/解锁拖动", self)
        self.lock_btn.clicked.connect(self.toggle_lock)

        top_bar.addWidget(self.lock_btn)
        root.addLayout(top_bar)

        # Two cards: AGY + OpenCode Go
        self.agy_card = ProviderCard("agy", "Antigravity (AGY)", self)
        self.agy_card.action_clicked.connect(lambda _: self.open_settings(1))
        root.addWidget(self.agy_card)

        self.oc_card = ProviderCard("opencode", "OpenCode Go", self)
        self.oc_card.action_clicked.connect(lambda _: self.open_settings(0))
        root.addWidget(self.oc_card)

        # Footer
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        self.footer_left = QLabel("正在初始化...")
        self.footer_left.setFont(QFont(FONT_FAMILY, 7.8))
        self.footer_right = QLabel("")
        self.footer_right.setFont(QFont(FONT_FAMILY, 7.8))
        footer.addWidget(self.footer_left)
        footer.addStretch(1)
        footer.addWidget(self.footer_right)
        root.addLayout(footer)

        # Timers
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.trigger_refresh)

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(30_000)
        self.countdown_timer.timeout.connect(self._re_render_cached)
        self.countdown_timer.start()

        # Debounced file watcher for instant T3 Code / AGY cache updates
        self._watch_debounce = QTimer(self)
        self._watch_debounce.setSingleShot(True)
        self._watch_debounce.setInterval(450)
        self._watch_debounce.timeout.connect(self.trigger_refresh)

        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.fileChanged.connect(self._on_agy_file_changed)
        self.file_watcher.directoryChanged.connect(self._on_agy_file_changed)
        self._watch_agy_cache()

        self.apply_config(self.cfg, reposition=True)
        self.trigger_refresh()

    def _apply_qt_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.layer_mode == "floating":
            flags |= Qt.WindowType.WindowStaysOnTopHint
        elif self.cfg.layer_mode == "desktop":
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        self.setWindowFlags(flags)

    def _watch_agy_cache(self) -> None:
        try:
            paths_to_watch: list[Path] = [
                self.cfg.effective_agy_json_path(),
                self.cfg.t3_agy_cache_path(),
                self.cfg.t3_opencode_cache_path(),
            ]
            for cache_path in paths_to_watch:
                parent = cache_path.parent
                if parent.exists() and str(parent) not in self.file_watcher.directories():
                    self.file_watcher.addPath(str(parent))
                if cache_path.exists() and str(cache_path) not in self.file_watcher.files():
                    self.file_watcher.addPath(str(cache_path))
        except Exception:
            pass

    def _on_agy_file_changed(self, _path: str) -> None:
        self._watch_agy_cache()
        if not self.demo_mode:
            self._watch_debounce.start()

    def apply_config(self, cfg: AppConfig, reposition: bool = False) -> None:
        prev_layer = self.cfg.layer_mode
        self.cfg = cfg
        dark = cfg.theme == "dark"

        if prev_layer != cfg.layer_mode:
            was_visible = self.isVisible()
            self._apply_qt_flags()
            if was_visible:
                self.show()

        self.agy_card.apply_theme(dark)
        self.oc_card.apply_theme(dark)

        if dark:
            self.header_title.setStyleSheet("color: rgba(248, 250, 252, 235); background: transparent;")
            self.footer_left.setStyleSheet("color: rgba(148, 163, 184, 185); background: transparent;")
            self.footer_right.setStyleSheet("color: rgba(148, 163, 184, 185); background: transparent;")
        else:
            self.header_title.setStyleSheet("color: rgba(15, 23, 42, 235); background: transparent;")
            self.footer_left.setStyleSheet("color: rgba(71, 85, 105, 195); background: transparent;")
            self.footer_right.setStyleSheet("color: rgba(71, 85, 105, 195); background: transparent;")

        self.lock_btn.set_dark(dark)

        self.lock_btn.set_kind("lock" if cfg.locked else "unlock")
        self.lock_btn.setToolTip("已锁定位置（点击解锁）" if cfg.locked else "未锁定位置（可拖拽摆放）")

        layer_names = {"desktop": "贴附桌面", "floating": "置顶悬浮", "normal": "普通层级"}
        self.footer_right.setText(layer_names.get(cfg.layer_mode, "贴附桌面"))

        self.poll_timer.stop()
        self.poll_timer.start(max(30, cfg.refresh_interval_sec) * 1000)

        self._watch_agy_cache()
        self.adjustSize()

        if reposition:
            self._place_initial_position()

        self.apply_native_layer()
        self.update()

    def apply_native_layer(self) -> None:
        if IS_WINDOWS and self.isVisible():
            apply_window_layer(int(self.winId()), self.cfg.layer_mode)

    def _find_target_screen(self, x: int, y: int):
        screens = QGuiApplication.screens()
        if not screens:
            return self.screen()

        # Prefer the screen containing the saved card center (handles edge/shadow overhang cleanly)
        cx = x + self.width() // 2
        cy = y + max(80, self.height()) // 2
        center_pt = QPoint(cx, cy)
        for s in screens:
            if s.geometry().contains(center_pt):
                return s

        # Fallback: screen containing the saved top-left (expanded by shadow margin)
        top_left = QPoint(x, y)
        m = self.SHADOW_MARGIN
        for s in screens:
            if s.geometry().adjusted(-m, -m, m, m).contains(top_left):
                return s

        # If previously saved monitor is disconnected, recover onto primary screen
        return QGuiApplication.primaryScreen() or self.screen() or screens[0]

    def _place_initial_position(self) -> None:
        if self.cfg.x is not None and self.cfg.y is not None:
            screen = self._find_target_screen(self.cfg.x, self.cfg.y)
            if screen is None:
                return
            geo = screen.availableGeometry()
            m = self.SHADOW_MARGIN
            x = max(geo.left() - m, min(geo.right() - 80, self.cfg.x))
            y = max(geo.top() - m, min(geo.bottom() - 80, self.cfg.y))
            self.move(x, y)
        else:
            screen = QGuiApplication.primaryScreen() or self.screen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            x = geo.right() - self.width() - 18
            y = geo.top() + 24
            self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(40, self.apply_native_layer)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        m = self.SHADOW_MARGIN
        card_rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        radius = 22.0

        # Soft layered drop shadow
        for i, alpha in ((3, 14), (2, 22), (1, 30)):
            sr = card_rect.adjusted(-i * 1.5, -i * 0.8 + 2.0, i * 1.5, i * 1.8 + 2.0)
            sp = QPainterPath()
            sp.addRoundedRect(sr, radius + i, radius + i)
            p.fillPath(sp, QColor(0, 0, 0, alpha))

        # Main frosted glass shell
        path = QPainterPath()
        path.addRoundedRect(card_rect, radius, radius)

        alpha_top = int(round(240 * self.cfg.opacity))
        alpha_bot = int(round(228 * self.cfg.opacity))
        grad = QLinearGradient(card_rect.topLeft(), card_rect.bottomLeft())

        if self.cfg.theme == "dark":
            grad.setColorAt(0.0, QColor(24, 28, 39, alpha_top))
            grad.setColorAt(1.0, QColor(15, 18, 26, alpha_bot))
            border_color = QColor(255, 255, 255, 42)
        else:
            grad.setColorAt(0.0, QColor(250, 252, 255, alpha_top))
            grad.setColorAt(1.0, QColor(238, 242, 249, alpha_bot))
            border_color = QColor(15, 23, 42, 28)

        p.fillPath(path, grad)
        p.setPen(QPen(border_color, 1.0))
        p.drawPath(path)

    def _sink_to_bottom(self) -> None:
        if IS_WINDOWS and self.cfg.layer_mode == "desktop" and not self._menu_open and self.isVisible():
            send_to_bottom(int(self.winId()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.cfg.locked:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(target)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            pos = self.pos()
            self.cfg.x = pos.x()
            self.cfg.y = pos.y()
            save_config(self.cfg)
            QTimer.singleShot(0, self._sink_to_bottom)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        # All management controls live in the background system tray menu
        event.ignore()

    def create_tray_menu(self) -> QMenu:
        """Build ONE persistent QMenu for the system tray (never clear/recreate actions)."""
        menu = QMenu(None)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: #1B1F2B;
                color: #F1F5F9;
                border: 1px solid rgba(255,255,255,32);
                border-radius: 10px;
                padding: 6px;
                font-family: "{FONT_FAMILY}";
                font-size: 12.5px;
            }}
            QMenu::item {{
                padding: 6px 22px 6px 14px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: rgba(99, 102, 241, 120);
            }}
            QMenu::separator {{
                height: 1px;
                background: rgba(255,255,255,22);
                margin: 5px 8px;
            }}
            """
        )

        act_refresh = QAction("立即刷新额度", menu)
        act_refresh.triggered.connect(lambda _checked=False: self.trigger_refresh())
        menu.addAction(act_refresh)

        act_settings = QAction("打开设置...", menu)
        act_settings.triggered.connect(lambda _checked=False: QTimer.singleShot(0, lambda: self.open_settings(0)))
        menu.addAction(act_settings)

        menu.addSeparator()

        self._tray_act_lock = QAction("锁定桌面位置", menu)
        self._tray_act_lock.setCheckable(True)
        self._tray_act_lock.triggered.connect(lambda _checked=False: self.toggle_lock())
        menu.addAction(self._tray_act_lock)

        layer_menu = menu.addMenu("桌面层级")
        layer_group = QActionGroup(layer_menu)
        self._tray_layer_actions: dict[str, QAction] = {}
        for label, mode in (
            ("贴附桌面底层（推荐）", "desktop"),
            ("始终置顶悬浮", "floating"),
            ("普通窗口", "normal"),
        ):
            a = QAction(label, layer_menu)
            a.setCheckable(True)
            a.triggered.connect(lambda _checked=False, m=mode: self.set_layer_mode(m))
            layer_group.addAction(a)
            layer_menu.addAction(a)
            self._tray_layer_actions[mode] = a

        theme_menu = menu.addMenu("外观主题")
        theme_group = QActionGroup(theme_menu)
        self._tray_theme_actions: dict[str, QAction] = {}
        for label, theme in (("深色毛玻璃", "dark"), ("浅色毛玻璃", "light")):
            a = QAction(label, theme_menu)
            a.setCheckable(True)
            a.triggered.connect(lambda _checked=False, t=theme: self.set_theme(t))
            theme_group.addAction(a)
            theme_menu.addAction(a)
            self._tray_theme_actions[theme] = a

        self._tray_act_auto = QAction("开机自动启动", menu)
        self._tray_act_auto.setCheckable(True)
        self._tray_act_auto.triggered.connect(lambda checked=False: self.toggle_autostart(bool(checked)))
        menu.addAction(self._tray_act_auto)

        menu.addSeparator()

        act_quit = QAction("退出小组件", menu)
        act_quit.triggered.connect(lambda _checked=False: self.quit_requested.emit())
        menu.addAction(act_quit)

        menu.aboutToShow.connect(self.sync_tray_menu_state)
        self.sync_tray_menu_state()
        return menu

    def sync_tray_menu_state(self) -> None:
        if hasattr(self, "_tray_act_lock"):
            self._tray_act_lock.setChecked(self.cfg.locked)
        if hasattr(self, "_tray_layer_actions"):
            for m, act in self._tray_layer_actions.items():
                act.setChecked(self.cfg.layer_mode == m)
        if hasattr(self, "_tray_theme_actions"):
            for t, act in self._tray_theme_actions.items():
                act.setChecked(self.cfg.theme == t)
        if hasattr(self, "_tray_act_auto"):
            self._tray_act_auto.setChecked(is_autostart_enabled())

    def toggle_lock(self) -> None:
        self.cfg.locked = not self.cfg.locked
        save_config(self.cfg)
        self.apply_config(self.cfg)

    def set_layer_mode(self, mode: str) -> None:
        self.cfg.layer_mode = mode
        save_config(self.cfg)
        self.apply_config(self.cfg)

    def set_theme(self, theme: str) -> None:
        self.cfg.theme = theme
        save_config(self.cfg)
        self.apply_config(self.cfg)

    def toggle_autostart(self, checked: bool) -> None:
        ok, msg = set_autostart(checked)
        if not ok:
            QMessageBox.warning(None, "开机自启", msg)

    def open_settings(self, tab_index: int = 0) -> None:
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.tabs.setCurrentIndex(max(0, min(2, tab_index)))
            self._settings_dlg.showNormal()
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
            bring_window_to_front(int(self._settings_dlg.winId()))
            return
        dlg = SettingsDialog(self.cfg, initial_tab=tab_index, parent=None)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.destroyed.connect(self._on_settings_closed)
        self._settings_dlg = dlg
        dlg.adjustSize()
        screen = self.screen()
        if screen is not None:
            center = screen.availableGeometry().center()
            dlg.move(center.x() - dlg.width() // 2, center.y() - dlg.height() // 2)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        QTimer.singleShot(20, lambda d=dlg: bring_window_to_front(int(d.winId())) if d.isVisible() else None)

    def _on_settings_closed(self) -> None:
        self._settings_dlg = None

    def _on_settings_saved(self, new_cfg: AppConfig) -> None:
        self.apply_config(new_cfg)
        self.trigger_refresh()

    def trigger_refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_dot.setStyleSheet("color: #60A5FA; background: transparent;")
        self.footer_left.setText("正在刷新...")
        worker = RefreshWorker(self.cfg, demo_mode=self.demo_mode)
        self._worker = worker
        worker.finished_data.connect(self._on_refresh_done)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _cleanup_worker(self, worker: RefreshWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _on_refresh_done(self, agy: ProviderQuota, oc: ProviderQuota) -> None:
        self._last_agy = agy
        self._last_oc = oc
        self.agy_card.set_quota(agy)
        self.oc_card.set_quota(oc)

        if agy.status == "ok" and oc.status == "ok":
            self.status_dot.setStyleSheet("color: #34D399; background: transparent;")
        elif agy.status == "error" or oc.status == "error":
            self.status_dot.setStyleSheet("color: #F87171; background: transparent;")
        else:
            self.status_dot.setStyleSheet("color: #FBBF24; background: transparent;")

        now_str = datetime.now().strftime("%H:%M")
        self.footer_left.setText(f"上次刷新 {now_str}")
        self.adjustSize()
        self.update()

    def _re_render_cached(self) -> None:
        if self._last_agy is not None:
            self.agy_card.set_quota(self._last_agy)
        if self._last_oc is not None:
            self.oc_card.set_quota(self._last_oc)
