"""mpv 子进程 + JSON IPC 客户端（SPEC §2.1）。

为什么用 mpv：本机实测能直接播 ``/api/`` 返回的时效 URL，且支持
``--stream-record`` 边播边落盘（零额外带宽，SPEC §8 E16/E17）。

IPC 约定（已实测，见 SPEC §8 E16/E17 与 acceptance.md）：
- ``{"command": [...], "request_id": N}`` 发命令，回包带同一 ``request_id``；
- ``event`` 字段是事件（``start-file`` / ``file-loaded`` / ``end-file`` / ``property-change`` …）；
- ``observe_property`` 用**整数 id**（用字符串 id 时不会收到 property-change）；
- ``stream-record`` 的输出格式由**扩展名**决定：用 ``.part`` 之类的未知后缀 mpv 会**静默不写文件**，
  必须给正确扩展名（mp3/flac/m4a…）。这条是踩过的坑，见 ``tests/test_mpv_ipc.py``。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from .. import config
from ..util import log

_log = log.get("player.mpv")

#: 需要观察的 mpv 属性 → 整数观察 id
OBSERVED = {
    1: "time-pos",
    2: "duration",
    3: "pause",
    4: "eof-reached",
    5: "path",
    6: "idle-active",
}
OBS_INVERSE = {name: oid for oid, name in OBSERVED.items()}

START_TIMEOUT_MS = 6000
CONNECT_RETRY_MS = 80

#: 实例计数：同一进程里可能同时存在多个 MpvPlayer（测试/预取），socket 必须各不相同
_INSTANCE_SEQ = 0


def cleanup_stale_sockets() -> int:
    """清理上一轮异常退出遗留的 IPC socket（进程还在的绝不删）。"""
    removed = 0
    if not config.RUNTIME_DIR.exists():
        return 0
    for path in config.RUNTIME_DIR.glob("mpv-*.sock"):
        parts = path.name.split("-")
        pid_part = parts[1] if len(parts) > 1 else ""
        if pid_part.isdigit() and Path(f"/proc/{pid_part}").exists():
            continue  # 进程活着，属于正在运行的实例
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover
            pass
    return removed


class MpvPlayer(QObject):
    """一个 mpv 进程的薄封装。所有操作都在 Qt 主线程（事件驱动，不阻塞）。"""

    #: 文件开始播放（参数为 url/path）
    file_loaded = Signal(str)
    #: 播放结束，reason ∈ {eof, stop, error, quit, redirect}
    ended = Signal(str)
    #: 播放位置（秒，浮点）
    position_changed = Signal(float)
    duration_changed = Signal(float)
    paused_changed = Signal(bool)
    #: mpv 起不来/崩溃
    failed = Signal(str)
    ready = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        socket_path: Path | None = None,
        extra_args: tuple[str, ...] = (),
        mpv_bin: str | None = None,
        log_file: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        global _INSTANCE_SEQ
        _INSTANCE_SEQ += 1
        self._bin = mpv_bin or _find_mpv()
        if socket_path is None:
            # 每个实例一个独立 socket：同名 socket 会被后启动的 mpv 接管，导致 IPC 串线
            socket_path = config.RUNTIME_DIR / f"mpv-{os.getpid()}-{_INSTANCE_SEQ}.sock"
        self._socket_path = Path(socket_path)
        self._extra = tuple(extra_args)
        #: 排查“为什么不出声”用：把 mpv 的 stderr 写到文件（默认丢弃）
        self._log_file = Path(log_file) if log_file else None
        self._proc: subprocess.Popen | None = None
        self._sock: QLocalSocket | None = None
        self._buffer = b""
        self._req = 0
        self._pending_replies: dict[int, list] = {}
        self._connected = False
        self._connect_tries = 0
        #: IPC 未就绪时的待发命令队列（坑：刚启动就点播放，命令会被丢掉）
        self._pending: list[dict] = []
        self._timer = QTimer(self)
        self._timer.setInterval(CONNECT_RETRY_MS)
        self._timer.timeout.connect(self._try_connect)
        #: 音量（0-100），mpv 的 volume 属性上限为 100，静音用 mute 属性
        self.volume = 70
        self.muted = False

    # ============================================================ 生命周期
    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """启动 mpv 并连接 IPC。"""
        if self.running:
            return True
        if not self._bin:
            self.failed.emit("未找到 mpv，请先安装（Fedora: sudo dnf install mpv）")
            return False
        config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_stale_sockets()
        for stale in (self._socket_path,):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        args = [
            self._bin,
            "--idle=yes",  # 无文件时保持进程存活，便于复用
            "--no-video",
            "--no-terminal",
            "--really-quiet",
            "--msg-level=all=error",
            "--audio-display=no",
            "--keep-open=no",
            "--cache=yes",
            "--force-seekable=yes",
            f"--input-ipc-server={self._socket_path}",
            f"--user-agent={config.USER_AGENT}",
            f"--referrer={config.REFERER}",
            *self._extra,
        ]
        stderr_target = subprocess.DEVNULL
        if self._log_file is not None:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            stderr_target = open(self._log_file, "ab", buffering=0)  # noqa: SIM115 - 进程退出后由 OS 回收
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr_target)
        except OSError as exc:
            _log.error("mpv 启动失败：%s", exc)
            self.failed.emit(f"mpv 启动失败：{exc}")
            return False
        _log.info("mpv 已启动 pid=%s socket=%s", self._proc.pid, self._socket_path)
        self._connected = False
        self._connect_tries = 0
        self._timer.start()
        self._try_connect()
        return True

    def shutdown(self) -> None:
        """退出 mpv 并清理 socket（AC-6.3）。"""
        self._timer.stop()
        if self._sock is not None:
            try:
                self._write({"command": ["quit"]})
                self._sock.flush()
            except (OSError, RuntimeError):
                pass
            self._sock.disconnectFromServer()
            self._sock.deleteLater()
            self._sock = None
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass
            except OSError:  # pragma: no cover
                pass
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        _log.info("mpv 已退出并清理 socket")

    # ============================================================ 命令
    def load(self, target: str, *, record_to: str | Path | None = None, start_ms: int = 0) -> None:
        """加载并播放；``record_to`` 非空时边播边落盘（须带正确扩展名）。"""
        # stream-record 必须先于 loadfile 设置（实测：loadfile 之后再设置对本文件无效）
        self._write({"command": ["set_property", "stream-record", str(record_to) if record_to else ""]})
        self._write({"command": ["set_property", "pause", False]})
        self._write({"command": ["set_property", "volume", self.volume]})
        self._write({"command": ["set_property", "mute", self.muted]})
        self._write({"command": ["loadfile", target, "replace"]})
        if start_ms > 0:
            self.seek(start_ms)

    def set_pause(self, paused: bool) -> None:
        self._write({"command": ["set_property", "pause", bool(paused)]})

    def toggle_pause(self) -> None:
        self._write({"command": ["cycle", "pause"]})

    def seek(self, position_ms: int) -> None:
        self._write({"command": ["seek", max(0, position_ms) / 1000.0, "absolute+exact"]})

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, int(volume)))
        self._write({"command": ["set_property", "volume", self.volume]})

    def set_mute(self, muted: bool) -> None:
        self.muted = bool(muted)
        self._write({"command": ["set_property", "mute", self.muted]})

    def stop(self) -> None:
        self._write({"command": ["stop"]})

    # ============================================================ IPC 内部
    def _write(self, payload: dict) -> bool:
        if self._sock is None or not self._connected:
            # 还没连上：排队等握手完成再发（否则“刚启动就点播放”会静默失败）
            if self.running:
                self._pending.append(payload)
                _log.debug("IPC 未就绪，命令入队：%s", payload.get("command"))
            else:
                _log.debug("mpv 未运行，丢弃命令：%s", payload.get("command"))
            return False
        self._req += 1
        payload["request_id"] = self._req
        if _log.isEnabledFor(10):  # DEBUG
            _log.debug("IPC → %s", payload.get("command"))
        line = (json.dumps(payload) + "\n").encode("utf-8")
        if self._sock.write(line) < 0:
            _log.warning("IPC 写入失败：%s", self._sock.errorString())
            return False
        self._sock.flush()
        return True

    def _try_connect(self) -> None:
        if self._connected:
            self._timer.stop()
            return
        if not self.running:
            self._timer.stop()
            self.failed.emit("mpv 进程已退出（启动失败或被杀）")
            return
        if not self._socket_path.exists():
            self._connect_tries += 1
            if self._connect_tries * CONNECT_RETRY_MS > START_TIMEOUT_MS:
                self._timer.stop()
                self.failed.emit("mpv IPC socket 未出现，启动超时")
            return
        if self._sock is None:
            self._sock = QLocalSocket(self)
            self._sock.readyRead.connect(self._on_ready_read)
            self._sock.disconnected.connect(self._on_disconnected)
            self._sock.errorOccurred.connect(self._on_socket_error)
        if self._sock.state() == QLocalSocket.UnconnectedState:
            self._sock.connectToServer(str(self._socket_path))
            return
        if self._sock.state() == QLocalSocket.ConnectedState:
            self._on_connected()

    def _on_connected(self) -> None:
        if self._connected:
            return
        self._connected = True
        self._timer.stop()
        for oid, name in OBSERVED.items():
            self._write({"command": ["observe_property", oid, name]})
        self._write({"command": ["set_property", "volume", self.volume]})
        self._write({"command": ["set_property", "mute", self.muted]})
        pending, self._pending = self._pending, []
        for payload in pending:  # 按原顺序补发启动期间排队的命令
            self._write(payload)
        if pending:
            _log.info("IPC 就绪后补发了 %d 条排队命令", len(pending))
        _log.info("mpv IPC 已连接，开始观察 %d 个属性", len(OBSERVED))
        self.ready.emit()

    def _on_socket_error(self, _error) -> None:  # noqa: ANN001 - Qt 枚举
        if self._sock is not None and self._sock.state() != QLocalSocket.ConnectedState:
            self._connect_tries += 1
            if not self._timer.isActive() and self.running:
                self._timer.start()

    def _on_disconnected(self) -> None:
        self._connected = False
        if self.running and not self._timer.isActive():
            self._timer.start()

    def _on_ready_read(self) -> None:
        if self._sock is None:
            return
        self._buffer += bytes(self._sock.readAll())
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                _log.debug("IPC 非法 JSON：%s", line[:120])
                continue
            self._handle(message)

    def _handle(self, message: dict) -> None:
        if "event" in message:
            self._handle_event(message)
            return
        if "request_id" in message and message.get("error") not in (None, "success"):
            _log.debug("IPC 命令失败：%s", message)

    def _handle_event(self, message: dict) -> None:
        event = message.get("event")
        if event == "property-change":
            self._handle_property(message)
        elif event == "file-loaded":
            path = str(message.get("playlist_entry_id") or "")
            self.file_loaded.emit(path)
        elif event == "end-file":
            reason = str(message.get("reason") or "")
            if message.get("error") and reason != "eof":
                _log.info("mpv 播放出错：reason=%s error=%s", reason, message.get("error"))
            self.ended.emit(reason)
        elif event in ("start-file", "playback-restart", "audio-reconfig"):
            pass
        else:
            _log.debug("未处理事件：%s", event)

    def _handle_property(self, message: dict) -> None:
        name = message.get("name") or OBSERVED.get(message.get("id"))
        data = message.get("data")
        if name == "time-pos" and isinstance(data, (int, float)):
            self.position_changed.emit(float(data))
        elif name == "duration" and isinstance(data, (int, float)):
            self.duration_changed.emit(float(data))
        elif name == "pause":
            self.paused_changed.emit(bool(data))
        elif name == "eof-reached":
            self._eof = bool(data)
        elif name == "path":
            self._path = data or ""


def _find_mpv() -> str | None:
    """在 PATH 与常见位置找 mpv。"""
    found = shutil.which("mpv")
    if found:
        return found
    for candidate in ("/usr/bin/mpv", "/usr/local/bin/mpv", "/snap/bin/mpv"):
        if Path(candidate).exists():
            return candidate
    return None


def available() -> bool:
    """系统是否有 mpv（UI 与测试用来做降级提示）。"""
    return _find_mpv() is not None
