"""扫码登录页（SPEC §4 AC-1.*）。

状态机::

    idle ──点击"扫码登录"──► loading ──拿到 unikey──► qr(轮询 2s)
      ▲                        │                       │
      └────── 失败/过期重试 ◄───┘       800(过期)→自动刷新 801→等待 802→已扫描 803→成功
"""

from __future__ import annotations

import io

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..net.api import Api
from ..net.session import ApiError, NeedLoginError
from ..state.session_state import SessionState
from ..util import icons, log
from ..util.async_task import AsyncTask
from .widgets import circular_pixmap, pixmap_from_bytes, remote_bytes

_log = log.get("ui.login")

POLL_INTERVAL_MS = 2000
QR_SIZE = 220


class LoginView(QWidget):
    """扫码登录与已登录信息展示。"""

    login_succeeded = Signal(object)  # Account

    def __init__(self, api: Api, session_state: SessionState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._state = session_state
        self._key: str = ""
        self._polling = False
        self._active = False

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self._build()
        # 关键：启动时的登录态是**异步**恢复的，构造时还没结果。
        # 不监听就永远停在"扫码登录"那一帧（用户反馈：已登录却显示扫码页）。
        self._state.account_changed.connect(self._on_state_changed)
        self._state.login_changed.connect(self._on_state_changed)
        self._render_state()

    def _on_state_changed(self, *_args) -> None:
        """登录态变化（扫码成功 / 重启恢复 / 退出）→ 重画整页。"""
        if self._state.logged_in:
            # 已登录就没必要继续轮询二维码了
            self._timer.stop()
            self._polling = False
        self._render_state()

    def refresh(self) -> None:
        """被切到这一页时主动刷一次（兜底，防止漏信号）。"""
        self._on_state_changed()

    # ============================================================ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(14)
        root.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("loginCard")
        card = QVBoxLayout(self.card)
        card.setContentsMargins(32, 28, 32, 28)
        card.setSpacing(12)
        card.setAlignment(Qt.AlignHCenter)

        self.lbl_title = QLabel("扫码登录")
        self.lbl_title.setObjectName("loginTitle")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        card.addWidget(self.lbl_title)

        self.lbl_sub = QLabel("打开手机端「网易云音乐」App，扫描下方二维码")
        self.lbl_sub.setObjectName("loginSub")
        self.lbl_sub.setAlignment(Qt.AlignCenter)
        self.lbl_sub.setWordWrap(True)
        card.addWidget(self.lbl_sub)

        self.lbl_qr = QLabel()
        self.lbl_qr.setObjectName("qrBox")
        self.lbl_qr.setFixedSize(QR_SIZE + 16, QR_SIZE + 16)
        self.lbl_qr.setAlignment(Qt.AlignCenter)
        card.addWidget(self.lbl_qr, 0, Qt.AlignHCenter)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("loginStatus")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(36)
        self.lbl_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        card.addWidget(self.lbl_status)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.btn_action = QPushButton("扫码登录")
        self.btn_action.setObjectName("primaryButton")
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.clicked.connect(self.start_qr_login)
        self.btn_refresh = QPushButton("刷新二维码")
        self.btn_refresh.setObjectName("secondaryButton")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(lambda: self.start_qr_login())
        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setObjectName("secondaryButton")
        self.btn_logout.setCursor(Qt.PointingHandCursor)
        self.btn_logout.clicked.connect(self._do_logout)
        for b in (self.btn_action, self.btn_refresh, self.btn_logout):
            row.addWidget(b)
        row.addStretch(1)
        card.addLayout(row)

        # 已登录信息区
        self.me_box = QFrame()
        self.me_box.setObjectName("meBox")
        me = QHBoxLayout(self.me_box)
        me.setContentsMargins(0, 6, 0, 0)
        me.setSpacing(12)
        self.lbl_avatar = QLabel()
        self.lbl_avatar.setFixedSize(56, 56)
        self.lbl_avatar.setObjectName("avatarBox")
        self.lbl_avatar.setAlignment(Qt.AlignCenter)
        self.lbl_me = QLabel("")
        self.lbl_me.setObjectName("meLabel")
        me.addStretch(1)
        me.addWidget(self.lbl_avatar)
        me.addWidget(self.lbl_me)
        me.addStretch(1)
        card.addWidget(self.me_box)

        root.addWidget(self.card, 0, Qt.AlignHCenter)
        root.addStretch(2)

    # ============================================================ 对外
    def set_message(self, text: str, warn: bool = False) -> None:
        """由主窗口写入提示（例如 cookie 失效）。"""
        self.lbl_status.setText(text)
        self.lbl_status.setProperty("warn", warn)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)
        if self._state.logged_in:
            self._render_state()

    # ============================================================ 状态渲染
    def _render_state(self) -> None:
        logged_in = self._state.logged_in
        account = self._state.account
        self.me_box.setVisible(logged_in)
        self.lbl_qr.setVisible(not logged_in)
        self.btn_refresh.setVisible(not logged_in)
        self.btn_action.setVisible(not logged_in)
        self.btn_logout.setVisible(logged_in)
        self.lbl_sub.setVisible(not logged_in)

        if logged_in and account:
            self.lbl_title.setText(f"已登录：{account.nickname or '用户'}")
            self.lbl_sub.setVisible(True)
            self.lbl_sub.setText(f"UID {account.uid}" + ("　（黑胶 VIP）" if account.vip_type else ""))
            self.lbl_me.setText(f"{account.nickname}\nUID {account.uid}")
            self._load_avatar(account.avatar)
            self.lbl_status.setText("登录成功，可以开始搜索和播放了")
            self.lbl_qr.clear()
        else:
            self.lbl_title.setText("扫码登录")
            self.lbl_sub.setText("打开手机端「网易云音乐」App，扫描下方二维码")
            self.lbl_qr.setPixmap(QPixmap())
            self.lbl_qr.setText("点击下方按钮获取二维码")
            if not self.lbl_status.text() or self.lbl_status.property("warn"):
                self.lbl_status.setText("尚未登录（登录后可播放与收藏）")

    def _load_avatar(self, url: str) -> None:
        if not url:
            self.lbl_avatar.setText("♪")
            return
        task = AsyncTask(remote_bytes, url)
        task.done.connect(self._on_avatar)
        task.start()

    def _on_avatar(self, data: bytes) -> None:
        pm = pixmap_from_bytes(data)
        if pm.isNull():
            self.lbl_avatar.setText("♪")
            return
        self.lbl_avatar.setPixmap(circular_pixmap(pm, 56))
        self.lbl_avatar.setText("")

    # ============================================================ 扫码流程
    def start_qr_login(self) -> None:
        """申请二维码并开始轮询。"""
        self._timer.stop()
        self._polling = False
        self._key = ""
        self.lbl_status.setText("正在获取二维码…")
        self.lbl_status.setProperty("warn", False)
        self.lbl_qr.setPixmap(QPixmap())
        self.lbl_qr.setText("加载中…")
        self.btn_action.setEnabled(False)
        self.btn_refresh.setEnabled(False)

        task = AsyncTask(self._api.qr_unikey)
        task.done.connect(self._on_unikey)
        task.failed.connect(self._on_unikey_failed)
        task.start()

    def _on_unikey(self, key: str) -> None:
        self._key = key
        self.btn_action.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self._render_qr(key)
        self.lbl_status.setText("请使用手机 App 扫描二维码")
        self._active = True
        self._timer.start()

    def _on_unikey_failed(self, exc: Exception) -> None:
        self.btn_action.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.lbl_qr.setText("二维码获取失败")
        self.lbl_status.setText(_friendly_error(exc, "获取二维码失败，请检查网络后重试"))
        self.lbl_status.setProperty("warn", True)

    def _render_qr(self, key: str) -> None:
        content = self._api.qr_content(key)
        try:
            import segno
        except ImportError:  # pragma: no cover - 依赖缺失时给出可操作提示
            self.lbl_qr.setText("缺少 segno 依赖\n请运行 scripts/setup.sh")
            self.lbl_status.setText("二维码库缺失：请执行 ./run.sh 完成依赖安装")
            return
        buf = io.BytesIO()
        qr = segno.make(content, error="m")  # type: ignore[attr-defined]
        qr.save(buf, kind="png", scale=6, border=2, dark="#111111", light="#ffffff")
        pm = pixmap_from_bytes(buf.getvalue())
        self.lbl_qr.setPixmap(pm.scaled(QR_SIZE, QR_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.lbl_qr.setText("")
        _log.debug("二维码已生成：%s", content)

    def _poll(self) -> None:
        if self._polling or not self._key:
            return
        self._polling = True
        task = AsyncTask(self._api.qr_poll, self._key)
        task.done.connect(self._on_poll)
        task.failed.connect(self._on_poll_failed)
        task.start()

    def _on_poll(self, status) -> None:
        self._polling = False
        if status.ok:
            self._timer.stop()
            self.lbl_status.setText("登录成功，正在获取账号信息…")
            task = AsyncTask(self._api.account)
            task.done.connect(self._on_account)
            task.failed.connect(self._on_account_failed)
            task.start()
            return
        if status.expired:
            self.lbl_status.setText("二维码已过期，正在自动刷新…")
            self.start_qr_login()
            return
        if status.scanned:
            self.lbl_status.setText("已扫描，请在手机上点击确认")
            return
        self.lbl_status.setText("请使用手机 App 扫描二维码")

    def _on_poll_failed(self, exc: Exception) -> None:
        self._polling = False
        self.lbl_status.setText(_friendly_error(exc, "轮询失败，稍后自动重试"))
        self.lbl_status.setProperty("warn", True)

    def _on_account(self, account) -> None:
        self._state.on_login(account)
        self._render_state()
        self.login_succeeded.emit(account)

    def _on_account_failed(self, exc: Exception) -> None:
        # 有 MUSIC_U 即视为登录成功，只是拿不到昵称
        _log.warning("获取账号信息失败：%s", exc)
        from ..model.types import Account

        self._state.on_login(Account(uid=0, nickname="已登录"))
        self._render_state()
        self.login_succeeded.emit(self._state.account)

    def _do_logout(self) -> None:
        self._state.logout_async()  # 登出请求在子线程，避免卡界面
        self._key = ""
        self._timer.stop()
        self.lbl_status.setProperty("warn", False)
        self._render_state()


def _friendly_error(exc: Exception, fallback: str) -> str:
    if isinstance(exc, NeedLoginError):
        return exc.user_message
    if isinstance(exc, ApiError):
        return exc.user_message
    return fallback
