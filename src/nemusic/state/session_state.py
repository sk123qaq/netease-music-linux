"""登录态：账号信息、cookie 生命周期、收藏歌单 id（SPEC §2.5 / AC-1.3）。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..config import Config
from ..model.types import Account
from ..net.api import Api
from ..net.session import NeedLoginError
from ..util import log
from ..util.async_task import AsyncTask

_log = log.get("state.session")


class SessionState(QObject):
    """全局登录态（单例由 MainWindow 持有并注入其它组件）。"""

    #: 登录态发生变化（True=已登录）
    login_changed = Signal(bool)
    #: 账号信息更新（Account 或 None）
    account_changed = Signal(object)
    #: 需要用户重新登录（cookie 失效），UI 应跳登录页并提示
    need_login = Signal(str)
    #: **用户主动点退出登录**（区别于 cookie 失效/重启恢复失败）——只有这条才清本地用户数据
    logged_out = Signal()

    def __init__(self, api: Api, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._config = config
        self._account: Account | None = None
        #: 「我喜欢的音乐」歌单 id（M3 由 library 填充）
        self.star_playlist_id: int = 0

    # ------------------------------------------------------------ 只读属性
    @property
    def account(self) -> Account | None:
        return self._account

    @property
    def uid(self) -> int:
        return self._account.uid if self._account else 0

    @property
    def nickname(self) -> str:
        if self._account and self._account.nickname:
            return self._account.nickname
        return self._config.nickname or "未登录"

    @property
    def avatar(self) -> str:
        return self._account.avatar if self._account else ""

    @property
    def logged_in(self) -> bool:
        return self._account is not None and self._api.session.logged_in

    # ------------------------------------------------------------ 生命周期
    def fetch_account(self) -> tuple[Account | None, str]:
        """**子线程里跑**：只做网络请求，绝不发信号（SPEC §4 规则 1）。

        :return: ``(account, status)``，status ∈ ok / no-cookie / expired / network
        """
        if not self._api.session.logged_in:
            return None, "no-cookie"
        try:
            return self._api.account(), "ok"
        except NeedLoginError:
            return None, "expired"
        except Exception:  # noqa: BLE001 - 状态由主线程判断
            return None, "network"

    def apply_fetched(self, payload: tuple) -> bool:
        """**主线程里跑**：按 fetch_account 的结果落地登录态并发信号。"""
        account, status = payload
        if status == "ok" and account is not None:
            self._set_account(account)
            _log.info("复用本地登录态成功：uid=%s", account.uid)
            return True
        if status == "expired":
            _log.info("本地 cookie 已失效，清空并要求重新登录")
            self._api.session.clear_cookies()
            self._clear_account()
            self.need_login.emit("登录状态已过期，请重新扫码登录")
            return False
        if status == "network":
            # 网络异常：不删 cookie、不清账号，下次启动再试
            _log.warning("恢复登录态失败（网络异常，保留 cookie 稍后重试）")
            return False
        return False

    def restore(self) -> bool:
        """同步版恢复（测试用）：内部走同一套逻辑，不自行发信号。"""
        return self.apply_fetched(self.fetch_account())

    def on_login(self, account: Account) -> None:
        """扫码成功后写入账号信息。"""
        self._set_account(account)

    def logout_async(self) -> None:
        """异步退出登录：登出请求在子线程，清账号在主线程。"""
        task = AsyncTask(self._api.logout)
        task.done.connect(self._on_logout_done)
        task.start()

    def _on_logout_done(self, _payload) -> None:
        self._clear_account()
        self.star_playlist_id = 0
        self.need_login.emit("已退出登录")
        self.logged_out.emit()

    def logout(self) -> None:
        """同步退出登录（测试用）。"""
        self._api.logout()
        self._clear_account()
        self.star_playlist_id = 0
        self.need_login.emit("已退出登录")
        self.logged_out.emit()

    # ------------------------------------------------------------ 内部
    def _set_account(self, account: Account) -> None:
        self._account = account
        self._config.uid = account.uid
        self._config.nickname = account.nickname
        self._config.save()
        self.account_changed.emit(account)
        self.login_changed.emit(True)

    def _clear_account(self) -> None:
        self._account = None
        self._config.uid = 0
        self._config.nickname = ""
        self._config.save()
        self.account_changed.emit(None)
        self.login_changed.emit(False)
