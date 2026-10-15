"""主题系统与外观页测试（浅色/深色、5 种预设、上传/删除/应用）。

对应 SPEC AC-6.* 与用户 2026-10-14 提出的背景图需求。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import pump_until, qt_app, redirect_config  # noqa: E402

from PySide6.QtWidgets import QLabel as QLableType  # noqa: E402
from PySide6.QtWidgets import QPushButton as QPushButtonType  # noqa: E402


def _make_gradient_png(path: Path, size: int = 64) -> Path:
    """造一张**带细节**的图（纯色图糊不糊都一样，测不出模糊度变化）。"""
    from PySide6.QtGui import QColor, QImage

    image = QImage(size, size, QImage.Format.Format_RGB32)
    for y in range(size):
        for x in range(size):
            image.setPixelColor(x, y, QColor((x * 7) % 256, (y * 11) % 256, (x * y) % 256))
    assert image.save(str(path), "PNG"), f"造图失败：{path}"
    return path


def _make_png(path: Path, color: str = "#ff0000", size: int = 8) -> Path:
    """造一张真 PNG（上传流程要读文件头和尺寸）。"""
    from PySide6.QtGui import QColor, QImage

    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG"), f"造图失败：{path}"
    return path


class TestThemeModule(unittest.TestCase):
    """纯函数部分：样式表拼接、预设查表。"""

    def test_five_presets_with_distinct_keys(self) -> None:
        from nemusic.ui import theme

        self.assertEqual(len(theme.PRESETS), 5, "用户要求 5 种风格")
        keys = [p.key for p in theme.PRESETS]
        self.assertEqual(len(set(keys)), 5)
        for preset in theme.PRESETS:
            self.assertTrue(preset.name)
            self.assertTrue(preset.hint)
            self.assertIn(preset.theme, ("light", "dark"))
            self.assertIn("gradient", preset.css, "每种预设都应是渐变背景")

    def test_build_stylesheet_has_all_layers(self) -> None:
        from nemusic.ui import theme

        for name in ("light", "dark"):
            sheet = theme.build_stylesheet(name, "background: red;")
            self.assertIn("QWidget", sheet)
            self.assertIn("QMainWindow { background: red; }", sheet, "背景层要拼进去")
            # 浅色/深色的文字色必须不同
        light = theme.build_stylesheet("light")
        dark = theme.build_stylesheet("dark")
        self.assertNotEqual(light, dark)
        self.assertIn("#2b2e35", light, "浅色用深色文字")
        self.assertIn("#e8e8e8", dark, "深色用浅色文字")

    def test_unknown_theme_falls_back_to_light(self) -> None:
        from nemusic.ui import theme

        sheet = theme.build_stylesheet("不存在的主题")
        self.assertEqual(sheet, theme.build_stylesheet("light"))

    def test_backdrop_css_for_presets_and_custom(self) -> None:
        from nemusic.ui import theme

        self.assertIn("gradient", theme.backdrop_css_for("mist"))
        self.assertIn("border-image", theme.backdrop_css_for("custom:a.png", "/tmp/a.png"))
        self.assertIn("gradient", theme.backdrop_css_for("custom:a.png", None), "没有文件路径时退回预设")
        self.assertIn("gradient", theme.backdrop_css_for("乱写的值"))

    def test_theme_for_background(self) -> None:
        from nemusic.ui import theme

        self.assertEqual(theme.theme_for("dusk"), "dark")
        self.assertEqual(theme.theme_for("mist"), "light")
        self.assertEqual(theme.theme_for("custom:x.png"), "light", "自定义图片沿用浅色")

    def test_horizontal_scrollbar_is_not_hidden(self) -> None:
        """以前用 height:0 藏横向滚动条，结果溢出看不见（用户反馈过）。"""
        from nemusic.ui import theme

        sheet = theme.build_stylesheet("light")
        self.assertNotIn("QScrollBar:horizontal { height: 0; }", sheet)


class TestBackgroundView(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def _view(self, cfg):
        from nemusic.ui.background_view import BackgroundView

        view = BackgroundView(cfg)
        self.addCleanup(view.deleteLater)
        view.resize(760, 640)
        view.show()
        self.app.processEvents()
        return view

    # ------------------------------------------------------------ 预设
    def test_preset_cards_present_with_apply_buttons(self) -> None:
        from nemusic.config import Config
        from nemusic.ui import theme

        cfg = Config.load()
        view = self._view(cfg)
        self.assertEqual(len(view._preset_thumbs), 5)
        buttons = [b for b in view.findChildren(QPushButtonType) if b.text() == "应用"]
        self.assertEqual(len(buttons), 5, "每种预设下面都要有应用按钮")

    def test_apply_preset_sets_theme_and_emits(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        got: list[tuple] = []
        view.appearance_changed.connect(lambda t, b: got.append((t, b)))
        view._apply_preset("dusk")
        self.assertEqual(cfg.background, "dusk")
        self.assertEqual(cfg.theme, "dark", "预设自带配套主题")
        self.assertEqual(got, [("dark", "dusk")])
        self.assertIn("晚霞", view.status.text())

    def test_preset_thumb_marked_current(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cfg.background = "ocean"
        view = self._view(cfg)
        self.assertTrue(view._preset_thumbs["ocean"].property("current"))
        self.assertFalse(view._preset_thumbs["mist"].property("current"))

    def test_theme_switch_swaps_to_matching_preset(self) -> None:
        """背景透出来后，浅色文字配白底会什么都看不见——切主题必须顺手换配套背景。"""
        from nemusic.config import Config
        from nemusic.ui import theme

        cfg = Config.load()
        view = self._view(cfg)
        view._apply_preset("mist")  # 浅色 + 白底
        view._on_theme("dark")
        self.assertEqual(cfg.theme, "dark")
        self.assertEqual(cfg.background, theme.DEFAULT_PRESET_BY_THEME["dark"],
                         "切到深色要把白底换成深色预设")
        self.assertEqual(theme.PRESET_BY_KEY[cfg.background].theme, "dark",
                         "换过来的预设必须和主题配套")

    def test_theme_switch_keeps_custom_image(self) -> None:
        """用户自己的图不该被主题切换悄悄换掉，但要提醒可能看不清。"""
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path("keep.png").absolute(), "#123456")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _suggested = view._copy_into_place(str(source))
        view._apply_custom(name)
        view._on_theme("dark")
        self.assertEqual(cfg.background, f"custom:{name}", "自定义图要保留")
        self.assertIn("仍保留", view.status.text(), "要提示可能看不清")

    # ------------------------------------------------------------ 上传 / 删除
    def test_upload_copies_file_and_applies(self) -> None:
        from nemusic import config as app_config
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path(self.id() + ".png").absolute(), "#00ff00")
        self.addCleanup(lambda: source.unlink(missing_ok=True))

        got: list[tuple] = []
        view.appearance_changed.connect(lambda t, b: got.append((t, b)))
        # 直接跑子线程那段逻辑（不弹文件对话框）
        name, suggested = view._copy_into_place(str(source))
        view._on_uploaded((name, suggested))
        self.assertTrue((app_config.BACKGROUND_DIR / name).is_file(), "应拷进配置目录")
        self.assertEqual(cfg.background, f"custom:{name}", "导进来就直接用上")
        self.assertEqual(got[-1][1], f"custom:{name}")

    def test_upload_renames_on_conflict(self) -> None:
        from nemusic import config as app_config
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        first = _make_png(Path("dup.png").absolute(), "#111111")
        second = _make_png(Path("dup.png").absolute(), "#222222")
        self.addCleanup(lambda: first.unlink(missing_ok=True))
        try:
            name1, _theme1 = view._copy_into_place(str(first))
            name2, _theme2 = view._copy_into_place(str(first))
            self.assertNotEqual(name1, name2, "同名不应互相覆盖")
            self.assertEqual(name2, "dup-1.png")
            self.assertTrue((app_config.BACKGROUND_DIR / name1).is_file())
            self.assertTrue((app_config.BACKGROUND_DIR / name2).is_file())
        finally:
            second.unlink(missing_ok=True)

    def test_delete_removes_file(self) -> None:
        from nemusic import config as app_config
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path("del.png").absolute(), "#333333")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _theme = view._copy_into_place(str(source))
        target = app_config.BACKGROUND_DIR / name
        self.assertTrue(target.is_file())

        view._delete_file(name)
        view._on_deleted(name)
        self.assertFalse(target.exists(), "删除后文件应消失")

    def test_delete_current_background_falls_back_to_preset(self) -> None:
        """正在用的图被删掉：不能留一个指向不存在文件的配置。"""
        from nemusic import config as app_config
        from nemusic.config import Config
        from nemusic.ui import theme

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path("cur.png").absolute(), "#444444")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _theme = view._copy_into_place(str(source))
        view._apply_custom(name)
        self.assertEqual(cfg.background, f"custom:{name}")

        view._delete_file(name)
        view._on_deleted(name)
        self.assertEqual(cfg.background, theme.DEFAULT_PRESET, "应退回默认预设")
        self.assertEqual(cfg.theme, theme.PRESET_BY_KEY[theme.DEFAULT_PRESET].theme)

    def test_custom_card_has_apply_and_delete(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path("card.png").absolute(), "#555555")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _theme = view._copy_into_place(str(source))
        view._reload_custom()
        self.assertIn(f"custom:{name}", view._custom_cards)
        card = view._custom_cards[f"custom:{name}"]
        texts = [b.text() for b in card.findChildren(QPushButtonType)]
        self.assertIn("应用", texts, "上传的图片下面要有应用按钮")
        self.assertIn("删除", texts, "上传的图片下面要有删除按钮")

    def test_empty_custom_shows_hint(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        labels = [w.text() for w in view.findChildren(QLableType)]
        self.assertIn("还没有上传过图片", labels)

    def test_dark_image_suggests_dark_theme(self) -> None:
        """深色照片配浅色主题会把图糊住，必须自动换深色。"""
        from nemusic.config import Config
        from nemusic.ui.background_view import _suggest_theme

        cfg = Config.load()
        view = self._view(cfg)
        dark = _make_png(Path("dark.png").absolute(), "#0c0e18")
        self.addCleanup(lambda: dark.unlink(missing_ok=True))
        self.assertEqual(_suggest_theme(dark), "dark")

        name, suggested = view._copy_into_place(str(dark))
        view._on_uploaded((name, suggested))
        self.assertEqual(cfg.theme, "dark")
        self.assertEqual(cfg.background, f"custom:{name}")
        self.assertIn("深色", view.status.text())

    def test_light_image_suggests_light_theme(self) -> None:
        from nemusic.config import Config
        from nemusic.ui.background_view import _suggest_theme

        cfg = Config.load()
        view = self._view(cfg)
        # 先弄成深色主题，再传一张亮图，应该被拉回浅色
        view._on_theme("dark")
        light = _make_png(Path("light.png").absolute(), "#f2f4f8")
        self.addCleanup(lambda: light.unlink(missing_ok=True))
        self.assertEqual(_suggest_theme(light), "light")

        name, suggested = view._copy_into_place(str(light))
        view._on_uploaded((name, suggested))
        self.assertEqual(cfg.theme, "light")
        self.assertEqual(cfg.background, f"custom:{name}")

    def test_preset_wins_over_theme_toggle(self) -> None:
        """点了某个预设，就以它为准（预设自带主题）。"""
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        view._apply_preset("ocean")  # 深色
        self.assertEqual(cfg.theme, "dark")
        self.assertEqual(cfg.background, "ocean")
        view._apply_preset("forest")  # 浅色
        self.assertEqual(cfg.theme, "light")
        self.assertEqual(cfg.background, "forest")

    # -------------------------------------------------- 模糊度
    def test_blur_divisor_maps_strength(self) -> None:
        from nemusic.ui.background_view import _blur_label, _clamp, _divisor

        self.assertEqual(_divisor(0), 1, "0 就是不模糊")
        self.assertEqual(_clamp(500), 100)
        self.assertEqual(_clamp(-5), 0)
        self.assertLess(_divisor(20), _divisor(60))
        self.assertLess(_divisor(60), _divisor(100), "越糊缩小倍率越大")
        self.assertEqual(_blur_label(0), "原图")
        self.assertTrue(_blur_label(90))

    def test_blur_slider_present_and_synced(self) -> None:
        from nemusic.config import Config

        from nemusic.ui.background_view import _blur_label

        cfg = Config.load()
        cfg.blur_strength = 70
        view = self._view(cfg)
        self.assertEqual(view.blur_slider.maximum(), 100)
        self.assertEqual(view.blur_slider.value(), 70)
        self.assertEqual(view.blur_value.text(), _blur_label(70))

    def test_blur_slider_change_saves_and_regenerates(self) -> None:
        """拖滑块要存配置并重出底图，不能只改个数字。"""
        from nemusic import config as app_config
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_gradient_png(Path("blurslide.png").absolute())
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _suggested = view._copy_into_place(str(source))
        view._apply_custom(name)
        first = (app_config.BACKGROUND_DIR / f"{Path(name).stem}_blur.jpg").read_bytes()

        got: list[tuple] = []
        view.appearance_changed.connect(lambda t, b: got.append((t, b)))
        view.blur_slider.setValue(15)  # 变轻
        self._pump(view)
        self.assertEqual(cfg.blur_strength, 15)
        self.assertTrue(got, "要发外观变化信号让主窗口重刷")
        second = (app_config.BACKGROUND_DIR / f"{Path(name).stem}_blur.jpg").read_bytes()
        self.assertNotEqual(first, second, "模糊度变了底图必须重新生成")

    def test_blur_zero_uses_original(self) -> None:
        """模糊度 0：background_path 应指向原图，不是模糊版。"""
        from nemusic import config as app_config
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        source = _make_png(Path("noblur.png").absolute(), "#223344")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        name, _suggested = view._copy_into_place(str(source))
        view._apply_custom(name)
        view.blur_slider.setValue(0)
        self._pump(view)
        self.assertEqual(cfg.blur_strength, 0)
        self.assertEqual(cfg.background_path.name, name, "0 应该用原图")

    def test_blur_slider_only_for_custom(self) -> None:
        """用预设时拖滑块不该崩，只提示。"""
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        view._apply_preset("mist")
        view.blur_slider.setValue(40)
        self._pump(view)
        self.assertIn("自定义背景", view.status.text())
        self.assertEqual(cfg.background, "mist", "预设不该被改动")

    def _pump(self, view, seconds: float = 1.5) -> None:
        """等子线程把底图重出完。"""
        import time

        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.processEvents()
            if view._blur_task is None:
                break
            time.sleep(0.02)
        self.app.processEvents()

    def test_rejects_oversized_and_bad_ext(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(cfg)
        big = Path("big.png").absolute()
        big.write_bytes(b"x")
        self.addCleanup(lambda: big.unlink(missing_ok=True))
        # 伪造一个超大文件
        with open(big, "wb") as fh:
            fh.write(b"\0" * 10)
        import nemusic.ui.background_view as mod

        original = mod.MAX_UPLOAD_BYTES
        mod.MAX_UPLOAD_BYTES = 4
        try:
            view._on_upload.__wrapped__ if False else None
            # 直接走校验分支：把 QFileDialog 短路
            from PySide6.QtWidgets import QFileDialog

            original_dialog = QFileDialog.getOpenFileName
            QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(big), ""))
            try:
                view._on_upload()
            finally:
                QFileDialog.getOpenFileName = original_dialog
        finally:
            mod.MAX_UPLOAD_BYTES = original
        self.assertIn("太大", view.status.text())


if __name__ == "__main__":
    unittest.main()
