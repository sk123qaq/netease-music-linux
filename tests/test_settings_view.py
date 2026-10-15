"""设置页（AC-5.5/5.6）：缓存统计、清理、上限滑块、退出登录、QSS。

界面构造走 offscreen；磁盘操作全部用临时目录隔离，不碰真实缓存。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import pump_until, qt_app, redirect_config  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from nemusic.state.cache import CacheManager, format_size  # noqa: E402


def _seed(cache: CacheManager) -> None:
    """造 4 类缓存 + 一个录制中的临时文件（后者不该计入统计）。"""
    cache.open_dir("audio").joinpath("1_exhigh.mp3").write_bytes(b"x" * 2048)
    cache.open_dir("audio").joinpath(".2_exhigh.mp3").write_bytes(b"y" * 9999)  # 临时文件
    cache.open_dir("playlist").joinpath("playlist_1.json").write_text("{}", encoding="utf-8")
    cache.open_dir("lyric").joinpath("lyric_1.json").write_text("{}", encoding="utf-8")
    cache.open_dir("cover").joinpath("cover_x.img").write_bytes(b"z" * 512)


class TestSettingsView(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例独立临时目录：缓存统计是对着磁盘真算的，共用目录会互相污染
        redirect_config()
        self.app = qt_app()

    def _view(self, cache, cfg):
        from nemusic.ui.settings_view import SettingsView

        view = SettingsView(cache, cfg)
        self.addCleanup(view.deleteLater)
        view.resize(760, 620)
        view.show()
        self.app.processEvents()
        return view

    # ------------------------------------------------------------ 统计
    def test_stats_show_counts_and_sizes(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        view = self._view(cache, cfg)
        self.assertTrue(pump_until(lambda: view._rows["audio"]["size"].text() != "…", 5000))
        # 临时文件（. 前缀）不计入
        self.assertEqual(view._rows["audio"]["size"].text(), format_size(2048))
        self.assertEqual(view._rows["audio"]["count"].text(), "1 个")
        self.assertEqual(view._rows["lyric"]["count"].text(), "1 个")
        self.assertEqual(view._rows["cover"]["size"].text(), format_size(512))
        self.assertEqual(view._rows["playlist"]["count"].text(), "1 个")

    def test_stats_run_in_worker_thread(self) -> None:
        """统计必须在子线程做（SPEC §4）。"""
        from nemusic.config import Config
        from nemusic.util.async_task import AsyncTask

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        seen: list[int] = []

        class Probe(CacheManager):
            def stats(self):  # type: ignore[override]
                import threading

                seen.append(threading.get_ident())
                return super().stats()

        view = self._view(Probe(cfg), cfg)
        self.assertTrue(pump_until(lambda: seen, 5000))
        import threading

        self.assertNotEqual(seen[0], threading.get_ident(), "stats 应该在子线程执行")
        self.assertTrue(pump_until(lambda: view._rows["audio"]["size"].text() != "…", 5000))

    # ------------------------------------------------------------ 清理
    def test_clear_one_kind_only(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        view = self._view(cache, cfg)
        self.assertTrue(pump_until(lambda: view._rows["lyric"]["count"].text() != "…", 5000))

        view._on_clear("lyric")
        self.assertTrue(pump_until(lambda: not list(cache.open_dir("lyric").glob("*.json")), 5000))
        # 只清了歌词：音频/封面还在
        self.assertTrue(cache.open_dir("audio").joinpath("1_exhigh.mp3").exists())
        self.assertTrue(cache.open_dir("cover").joinpath("cover_x.img").exists())
        self.assertIn("已清理歌词", view.status.text())
        # 统计自动刷新
        self.assertTrue(pump_until(lambda: view._rows["lyric"]["count"].text() == "0 个", 5000))
        self.assertEqual(view._rows["audio"]["count"].text(), "1 个")

    def test_clear_all(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        view = self._view(cache, cfg)
        self.assertTrue(pump_until(lambda: view._rows["cover"]["size"].text() != "…", 5000))

        view._on_clear("all")
        self.assertTrue(pump_until(lambda: view._rows["cover"]["count"].text() == "0 个", 5000))
        self.assertEqual(view._rows["audio"]["count"].text(), "0 个")
        self.assertEqual(view._rows["playlist"]["count"].text(), "0 个")
        self.assertEqual(view._rows["lyric"]["count"].text(), "0 个")
        self.assertIn("已清理全部缓存", view.status.text())

    def test_buttons_disabled_while_busy(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        view = self._view(cache, cfg)
        self.assertTrue(pump_until(lambda: view._rows["audio"]["count"].text() != "…", 5000))
        view._set_busy(True)
        self.assertTrue(all(not b.isEnabled() for b in view._buttons))
        view._set_busy(False)
        self.assertTrue(all(b.isEnabled() for b in view._buttons))

    # ------------------------------------------------------------ 上限与 LRU
    def test_slider_saves_limit_and_trims(self) -> None:
        """AC-5.6：上限调小 → 立刻 LRU 淘汰。"""
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        for i in range(4):
            cache.open_dir("audio").joinpath(f"{i}_exhigh.mp3").write_bytes(b"a" * (1024 * 1024))
        view = self._view(cache, cfg)
        self.assertTrue(pump_until(lambda: view._rows["audio"]["size"].text() != "…", 5000))

        got: list[int] = []
        view.limit_changed.connect(lambda b: got.append(b))
        view.slider.setValue(50)  # 50 MB 下限
        self.app.processEvents()
        self.assertEqual(cfg.cache_limit_bytes, 50 * 1024**2)
        self.assertIn(50 * 1024**2, got)
        # 音频总量 4MB < 50MB，不该淘汰
        self.assertEqual(view._rows["audio"]["count"].text(), "4 个")

    def test_trim_removes_oldest_first(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        _seed(cache)
        paths = []
        for i in range(3):
            path = cache.open_dir("audio").joinpath(f"old{i}_exhigh.mp3")
            path.write_bytes(b"a" * (1024 * 1024))
            paths.append(path)
        # 人为造 mtime 顺序
        import os as _os

        for i, path in enumerate(paths):
            _os.utime(path, (1000 + i * 100, 1000 + i * 100))
        # 总量 3MB+2KB，上限 2MB：要删掉最旧的两个才够
        removed = cache.trim_audio(2 * 1024**2)
        self.assertEqual(removed, 2)
        self.assertFalse(paths[0].exists(), "最旧的先淘汰")
        self.assertFalse(paths[1].exists())
        self.assertTrue(paths[2].exists(), "最新的保留")

    # ------------------------------------------------------------ 账号 / 展示
    def test_set_account(self) -> None:
        from nemusic.config import Config
        from nemusic.model.types import Account

        cfg = Config.load()
        view = self._view(CacheManager(cfg), cfg)
        view.set_account(None)
        self.assertEqual(view.account_label.text(), "未登录")
        view.set_account(Account(uid=1, nickname="小明", vip_type=110))
        self.assertIn("小明", view.account_label.text())
        self.assertIn("VIP", view.account_label.text())
        self.assertIn("UID 1", view.account_label.text())

    def test_quality_ladder_shown(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(CacheManager(cfg), cfg)
        labels = [w.text() for w in view.findChildren(QLabel)]
        joined = "\n".join(labels)
        self.assertIn("320 kbps", joined)
        self.assertIn("192 kbps", joined)
        self.assertIn("128 kbps", joined)

    def test_logout_signal(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        view = self._view(CacheManager(cfg), cfg)
        got: list[int] = []
        view.logout_requested.connect(lambda: got.append(1))
        # 等首轮统计跑完（统计期间按钮是禁用的），主窗口的确认弹窗不参与本测试
        self.assertTrue(pump_until(lambda: all(b.isEnabled() for b in view._buttons), 5000))
        button = next(b for b in view._buttons if b.text() == "退出登录")
        button.click()
        self.assertEqual(got, [1])

    def test_open_dir_button_does_not_crash(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        cache = CacheManager(cfg)
        view = self._view(cache, cfg)
        # 没有 xdg-open 的环境下应该退化成"请手动打开"提示，而不是崩
        view._on_open_dir()
        self.assertTrue(view.status.text())


if __name__ == "__main__":
    unittest.main()
