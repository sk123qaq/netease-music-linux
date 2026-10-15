"""应用装配：QApplication、全局样式、异常兜底、启动入口。"""

from __future__ import annotations

import argparse
import sys
import traceback

from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_TITLE, __version__
from .config import Config, ensure_dirs
from .net import crypto
from .net.api import Api
from .ui.main_window import MainWindow
from .ui import theme
from .util import log

_log = log.get("app")

def build_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow, Config]:
    """构建 QApplication 与主窗口（测试与 --self-check 复用）。"""
    ensure_dirs()
    log.setup()
    crypto.selftest()  # AES 坏了一切都不可信，启动即校验

    config = Config.load()
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(__version__)
    app.setStyleSheet(theme.build_stylesheet(config.theme, theme.backdrop_css_for(
        config.background, str(config.background_path) if config.background_path else None)))
    install_excepthook()

    api = Api()
    window = MainWindow(api, config)
    return app, window, config


def install_excepthook() -> None:
    """把未捕获异常写进日志，避免静默退出（AC-6.4）。"""

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log.error("未捕获异常：%s\n%s", exc, "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    """程序入口。"""
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_TITLE)
    parser.add_argument("--self-check", action="store_true", help="构建界面后立即退出（无头冒烟测试）")
    parser.add_argument("--page", default="", help="启动后直接切到指定页面：login/library/search/settings")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    app, window, _config = build_app([sys.argv[0]])
    if args.page:
        window.show_page(args.page)

    if args.self_check:
        window.show()
        app.processEvents()
        _log.info("self-check 通过：窗口已构建 (%dx%d)", window.width(), window.height())
        print(f"[self-check] OK — {APP_TITLE} {__version__}，页面数 {window.stack.count()}，AES 自检通过")
        window.close()
        app.processEvents()
        return 0

    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
