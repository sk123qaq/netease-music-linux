"""登录态状态机（AC-1.3）：子线程只做请求，主线程才发信号（SPEC §4 规则 1）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.config import Config  # noqa: E402
from nemusic.model.types import Account  # noqa: E402
from nemusic.net.session import NeedLoginError, NetworkError  # noqa: E402
from nemusic.state.session_state import SessionState  # noqa: E402


class FakeSession:
    def __init__(self, logged_in: bool) -> None:
        self.logged_in = logged_in
        self.cleared = False

    def clear_cookies(self) -> None:
        self.cleared = True
        self.logged_in = False


class FakeApi:
    def __init__(self, *, logged_in: bool = True, account=None, raises=None) -> None:
        self.session = FakeSession(logged_in)
        self._account = account
        self._raises = raises
        self.logout_called = False

    def account(self):
        if self._raises is not None:
            raise self._raises
        return self._account

    def logout(self) -> None:
        self.logout_called = True


def make_state(**kwargs) -> tuple[SessionState, FakeApi]:
    api = FakeApi(**kwargs)
    return SessionState(api, Config.load()), api


ACCOUNT = Account(uid=12345, nickname="小明")


class TestFetchAccount(unittest.TestCase):
    """子线程部分：只做请求，不发信号。"""

    def setUp(self) -> None:
        redirect_config()

    def test_no_cookie(self) -> None:
        state, _api = make_state(logged_in=False)
        self.assertEqual(state.fetch_account(), (None, "no-cookie"))

    def test_ok(self) -> None:
        state, _api = make_state(account=ACCOUNT)
        account, status = state.fetch_account()
        self.assertEqual(status, "ok")
        self.assertEqual(account.uid, 12345)

    def test_expired(self) -> None:
        state, _api = make_state(raises=NeedLoginError(301, "cookie 失效"))
        self.assertEqual(state.fetch_account(), (None, "expired"))

    def test_network_error(self) -> None:
        state, _api = make_state(raises=NetworkError(-1, "网络不通"))
        self.assertEqual(state.fetch_account(), (None, "network"))


class TestApplyFetched(unittest.TestCase):
    """主线程部分：落地登录态并发信号。"""

    def setUp(self) -> None:
        redirect_config()

    def test_ok_sets_account_and_emits(self) -> None:
        state, _api = make_state(account=ACCOUNT)
        logins: list[bool] = []
        accounts: list[object] = []
        state.login_changed.connect(logins.append)
        state.account_changed.connect(accounts.append)
        self.assertTrue(state.apply_fetched((ACCOUNT, "ok")))
        self.assertTrue(state.logged_in)
        self.assertEqual(state.uid, 12345)
        self.assertEqual(state.nickname, "小明")
        self.assertEqual(logins, [True])
        self.assertEqual([a.uid for a in accounts], [12345])

    def test_no_cookie(self) -> None:
        state, _api = make_state(logged_in=False)
        self.assertFalse(state.apply_fetched((None, "no-cookie")))
        self.assertFalse(state.logged_in)

    def test_expired_clears_cookie_and_asks_login(self) -> None:
        state, api = make_state(account=ACCOUNT, raises=NeedLoginError(301, "失效"))
        messages: list[str] = []
        state.need_login.connect(messages.append)
        self.assertFalse(state.apply_fetched((None, "expired")))
        self.assertTrue(api.session.cleared)
        self.assertEqual(messages, ["登录状态已过期，请重新扫码登录"])
        self.assertFalse(state.logged_in)

    def test_network_error_keeps_state(self) -> None:
        state, api = make_state(account=ACCOUNT, raises=NetworkError(-1, "网络不通"))
        self.assertFalse(state.apply_fetched((None, "network")))
        self.assertFalse(api.session.cleared)  # 不清 cookie，下次再试
        self.assertFalse(state.logged_in)

    def test_sync_restore_helper(self) -> None:
        """restore() 只是两步拼接，便于测试与脚本复用。"""
        state, _api = make_state(account=ACCOUNT)
        self.assertTrue(state.restore())
        self.assertEqual(state.uid, 12345)

    def test_logout_async_clears_account(self) -> None:
        """退出登录：请求在子线程，账号清理在主线程。"""
        import time

        from support import qt_app

        app = qt_app()
        state, api = make_state(account=ACCOUNT)
        state.apply_fetched((ACCOUNT, "ok"))
        messages: list[str] = []
        state.need_login.connect(messages.append)
        state.logout_async()
        deadline = time.time() + 5
        while time.time() < deadline and state.logged_in:
            app.processEvents()
            time.sleep(0.005)
        self.assertTrue(api.logout_called)
        self.assertFalse(state.logged_in)
        self.assertEqual(messages, ["已退出登录"])


if __name__ == "__main__":
    unittest.main()


class TestLogoutClearsDataOnlyWhenExplicit(unittest.TestCase):
    """用户要求：**只有主动点退出登录才清用户数据**，cookie 失效/重启一律不清。"""

    def setUp(self) -> None:
        redirect_config()

    def _state(self):
        from nemusic.net.api import Api
        from nemusic.state.session_state import SessionState

        return SessionState(Api(), Config.load())

    def test_expired_cookie_does_not_emit_logged_out(self) -> None:
        ss = self._state()
        got: list[str] = []
        ss.logged_out.connect(lambda: got.append("out"))
        # status=expired：只清账号 + 要求重登，**不发 logged_out**
        ss.apply_fetched((None, "expired"))
        self.assertEqual(got, [])
        self.assertIsNone(ss.account)

    def test_network_failure_keeps_everything(self) -> None:
        ss = self._state()
        got: list[str] = []
        ss.logged_out.connect(lambda: got.append("out"))
        ss.apply_fetched((None, "network"))
        self.assertEqual(got, [])
        self.assertIsNone(ss.account)

    def test_explicit_logout_emits_logged_out(self) -> None:
        ss = self._state()
        got: list[str] = []
        ss.logged_out.connect(lambda: got.append("out"))
        ss.logout()
        self.assertEqual(got, ["out"])
