from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from config import APP_DISPLAY_NAME, get_app_data_dir, load_config
from ui_widgets import QuotaWidgetWindow

SINGLE_INSTANCE_KEY = "QuotaDesktopWidget_SingleInstance_v1"


def _install_error_logger() -> None:
    log_path = get_app_data_dir() / "widget.log"

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().isoformat()}] Unhandled exception:\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def create_app_icon() -> QIcon:
    """Programmatically draw a crisp vector icon for tray & dialogs."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    bg = QPainterPath()
    bg.addRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    grad = QLinearGradient(4, 4, 60, 60)
    grad.setColorAt(0.0, QColor("#6366F1"))
    grad.setColorAt(1.0, QColor("#10B981"))
    p.fillPath(bg, grad)

    # Ring gauge motif
    p.setPen(QPen(QColor(255, 255, 255, 70), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(16, 16, 32, 32), 0, 360 * 16)

    p.setPen(QPen(QColor("#FFFFFF"), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(16, 16, 32, 32), 90 * 16, -240 * 16)
    p.end()
    return QIcon(pm)


def _notify_existing_instance() -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if sock.waitForConnected(250):
        sock.write(b"refresh\n")
        sock.flush()
        sock.waitForBytesWritten(250)
        sock.disconnectFromServer()
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="AGY & OpenCode Go Desktop Quota Widget")
    parser.add_argument("--demo", action="store_true", help="使用演示数据启动预览")
    parser.add_argument("--theme", choices=["dark", "light"], default=None, help="临时覆盖主题")
    parser.add_argument("--screenshot", type=str, default="", help="渲染小组件截图到指定 PNG 路径后退出")
    args = parser.parse_args()

    _install_error_logger()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setQuitOnLastWindowClosed(False)

    icon = create_app_icon()
    app.setWindowIcon(icon)

    is_utility_run = bool(args.screenshot) or args.demo
    server: QLocalServer | None = None
    if not is_utility_run:
        if _notify_existing_instance():
            return 0
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        server = QLocalServer()
        server.listen(SINGLE_INSTANCE_KEY)

    cfg = load_config()
    if args.theme:
        cfg.theme = args.theme

    window = QuotaWidgetWindow(cfg, demo_mode=args.demo)
    window.setWindowIcon(icon)
    window.quit_requested.connect(app.quit)

    if server is not None:
        def _on_new_conn() -> None:
            conn = server.nextPendingConnection()
            if conn:
                conn.readAll()
                conn.close()
            window.trigger_refresh()
            window.open_settings(0)

        server.newConnection.connect(_on_new_conn)

    # System tray icon (all controls except desktop lock live here)
    tray: QSystemTrayIcon | None = None
    tray_menu: QMenu | None = None
    if not args.screenshot and QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip(f"{APP_DISPLAY_NAME}（双击打开设置，右键管理）")

        tray_menu = window.create_tray_menu()
        tray.setContextMenu(tray_menu)

        def _on_tray_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
                QTimer.singleShot(0, lambda: window.open_settings(0))

        tray.activated.connect(_on_tray_activated)
        tray.show()

    window.show()

    if args.screenshot:
        out_path = Path(args.screenshot).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def _capture_and_exit() -> None:
            window.adjustSize()
            pm = window.grab()
            pm.save(str(out_path), "PNG")
            app.quit()

        QTimer.singleShot(600, _capture_and_exit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
