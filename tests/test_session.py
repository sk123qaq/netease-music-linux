"""会话层测试：错误码映射、拼接 JSON 容错、cookie 持久化与权限（AC-1.3 / AC-1.4）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.net.session import (  # noqa: E402
    ApiError,
    NeedLoginError,
    NetworkError,
    Session,
    _parse_concatenated,
)


class TestParseConcatenated(unittest.TestCase):
    def test_single_object(self) -> None:
        self.assertEqual(_parse_concatenated('{"code":200,"a":1}'), {"code": 200, "a": 1})

    def test_two_objects_returns_last(self) -> None:
        text = '{"msg":"参数错误","code":400}{"msg":"参数错误","code":400}'
        self.assertEqual(_parse_concatenated(text), {"msg": "参数错误", "code": 400})

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_concatenated("<html>blocked</html>"))


class TestSessionParsing(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()
        self.session = Session()

    def test_ok_codes(self) -> None:
        self.assertEqual(self.session._parse('{"code":200,"x":1}', "t", 200), {"code": 200, "x": 1})
        self.assertEqual(self.session._parse('{"x":1}', "t", 200), {"x": 1})  # 无 code 视为成功

    def test_need_login_codes(self) -> None:
        for code in (301, -462):
            with self.assertRaises(NeedLoginError) as ctx:
                self.session._parse(json.dumps({"code": code}), "t", 200)
            self.assertIn("登录", ctx.exception.user_message)

    def test_business_error_message(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.session._parse('{"code":400,"msg":"参数错误"}', "t", 200)
        self.assertEqual(ctx.exception.code, 400)
        self.assertTrue(ctx.exception.user_message)

    def test_concatenated_body_is_tolerated(self) -> None:
        payload = self.session._parse('{"msg":"参数错误","code":400}{"code":200,"x":2}', "t", 200)
        self.assertEqual(payload["x"], 2)

    def test_non_json_raises_network_error(self) -> None:
        with self.assertRaises(NetworkError):
            self.session._parse("<html>waf</html>", "t", 403)

    def test_empty_body_raises_network_error(self) -> None:
        with self.assertRaises(NetworkError):
            self.session._parse("", "t", 200)

    def test_eapi_retry_detection(self) -> None:
        self.assertTrue(self.session._should_retry_eapi("", 200))
        self.assertTrue(self.session._should_retry_eapi('{"msg":"参数错误","code":400}', 200))
        self.assertTrue(self.session._should_retry_eapi("<html>", 200))
        self.assertFalse(self.session._should_retry_eapi('{"code":200}', 200))

    def test_qr_status_codes_pass_through_ok_codes(self) -> None:
        """回归测试：二维码轮询的 800/801/802/803 是状态，不是错误。

        初版把 801 当成 ApiError 抛出，导致登录页永远拿不到“等待扫描”状态；
        这是联网只读探测（tests/integration_probe.py）发现的问题。
        """
        for code in (800, 801, 802, 803):
            payload = self.session._parse('{"code":%d}' % code, "t", 200, {800, 801, 802, 803})
            self.assertEqual(payload["code"], code)

    def test_qr_status_codes_raise_without_ok_codes(self) -> None:
        with self.assertRaises(ApiError):
            self.session._parse('{"code":801}', "t", 200)


class TestCookiePersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.session = Session()

    def test_not_logged_in_initially(self) -> None:
        self.assertFalse(self.session.logged_in)

    def test_set_cookie_persists_with_600_perms(self) -> None:
        self.session.set_cookie("MUSIC_U", "token-abc")
        self.assertTrue(self.session.logged_in)
        path = self.tmp / "cookies.json"
        self.assertTrue(path.exists())
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["MUSIC_U"], "token-abc")

    def test_reload_in_new_session(self) -> None:
        self.session.set_cookie("MUSIC_U", "token-xyz")
        fresh = Session()
        self.assertTrue(fresh.logged_in)
        self.assertEqual(fresh.all_cookies()["MUSIC_U"], "token-xyz")

    def test_clear_removes_file(self) -> None:
        self.session.set_cookie("MUSIC_U", "t")
        self.session.clear_cookies()
        self.assertFalse(self.session.logged_in)
        self.assertFalse((self.tmp / "cookies.json").exists())

    def test_need_login_guard(self) -> None:
        """需要登录的端点未登录时必须直接抛 NeedLoginError（不发请求）。"""
        with self.assertRaises(NeedLoginError):
            self.session.request(ep.ACCOUNT)

    def test_cookie_header_merges_login_cookie(self) -> None:
        """回归测试：登录 cookie 必须真的出现在请求头里。

        requests 在请求头已有 ``Cookie`` 时不会再用 jar 生成（models.py 有明确说明），
        初版因此把 ``MUSIC_U`` 丢了，服务端一直当访客（``profile: null``）。
        """
        self.session.set_cookie("MUSIC_U", "token-abc")
        header = self.session._headers()["Cookie"]
        self.assertIn("MUSIC_U=token-abc", header)
        self.assertIn("os=pc", header)  # 静态字段不能丢

    def test_cookie_header_without_login(self) -> None:
        header = self.session._headers()["Cookie"]
        self.assertIn("os=pc", header)
        self.assertNotIn("MUSIC_U", header)


class TestConfigRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()

    def test_defaults_and_save(self) -> None:
        from nemusic.config import Config

        cfg = Config.load()
        self.assertTrue(cfg.device_id)
        self.assertEqual(cfg.play_mode, "list_loop")
        cfg.volume = 42
        cfg.play_mode = "shuffle"
        cfg.save()
        again = Config.load()
        self.assertEqual(again.volume, 42)
        self.assertEqual(again.play_mode, "shuffle")
        self.assertEqual(again.device_id, cfg.device_id)

    def test_invalid_play_mode_falls_back(self) -> None:
        from nemusic.config import Config

        (self.tmp / "config.json").write_text(json.dumps({"play_mode": "bogus"}), encoding="utf-8")
        self.assertEqual(Config.load().play_mode, "list_loop")

    def test_corrupt_config_does_not_crash(self) -> None:
        from nemusic.config import Config

        (self.tmp / "config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(Config.load().volume, 70)

    def test_cache_limit_mb(self) -> None:
        from nemusic.config import Config

        cfg = Config()
        cfg.set_cache_limit_mb(100)
        self.assertEqual(cfg.cache_limit_bytes, 100 * 1024**2)


if __name__ == "__main__":
    unittest.main()
