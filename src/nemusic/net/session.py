"""HTTP 会话：统一请求头、cookie 持久化、错误码 → 中文提示、eapi 自动重试。

设计要点（SPEC §2.2）：
1. 明文 ``/api/`` 为主通道；返回非 JSON 或明确参数错误且端点支持 eapi 时，改用 ``/eapi/`` 重试一次。
2. cookie（NMTID / MUSIC_U / __csrf）持久化到 ``cookies.json``（chmod 600），实现重启免扫码。
3. 错误码统一翻成中文；需要登录的错误抛出 :class:`NeedLoginError`，由 UI 捕获后跳登录页。
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

from .. import config
from ..util import log
from . import crypto, endpoints as ep

_log = log.get("net.session")

try:  # requests 优先（系统已装）
    import requests

    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - 回退路径
    _HAS_REQUESTS = False


# ---------------------------------------------------------------- 异常


class ApiError(Exception):
    """接口返回的业务错误。"""

    def __init__(self, code: int, message: str, endpoint: str = "", raw: Any = None) -> None:
        self.code = code
        self.raw = raw
        super().__init__(f"[{code}] {message}" + (f" ({endpoint})" if endpoint else ""))

    @property
    def user_message(self) -> str:
        """给用户看的中文提示。"""
        return CODE_MESSAGES.get(self.code, self.args[0] if self.args else "请求失败")


class NeedLoginError(ApiError):
    """需要登录（code 301 / -462）。"""


class NetworkError(ApiError):
    """网络层错误（连接失败/超时/非 JSON 响应）。"""

    def __init__(self, message: str, endpoint: str = "", raw: Any = None) -> None:
        super().__init__(0, message, endpoint, raw)


#: 错误码 → 中文提示（SPEC §2.2）
CODE_MESSAGES: dict[int, str] = {
    301: "需要登录后才能使用该功能",
    -462: "登录状态已失效，请重新登录",
    400: "请求参数有误（接口可能已变更）",
    403: "没有访问权限",
    404: "内容不存在或已下架",
    250: "操作过于频繁，请稍后再试",
    460: "无版权或仅限特定地区播放",
    501: "服务端拒绝请求（可能被风控拦截）",
    502: "服务端异常，请稍后重试",
    -110: "设备或 IP 受限，请稍后重试",
}

#: 触发 eapi 重试的线索：明文通道返回这些内容说明该端点要求加密载荷
_EAPI_RETRY_HINTS = ("参数错误", "加密", "encrypt", "Illegal")


# ---------------------------------------------------------------- 会话


class Session:
    """网易云接口会话（线程安全靠"每请求独立"的简单策略：内部串行化 cookie 读写）。"""

    def __init__(self, base_url: str = ep.BASE_URL) -> None:
        self.base_url = base_url
        self._requests = _new_requests_session() if _HAS_REQUESTS else None
        self._urllib_cookies: dict[str, str] = {}
        self._cookie_cache: dict[str, str] = {}
        self._last_cookie_dump = ""
        self.load_cookies()

    # -------------------------------------------------- 请求头 / cookie
    def cookie_header(self) -> str:
        """拼出最终 Cookie 头：静态提示（os/appver/osver）+ 会话 cookie（登录态优先）。

        为什么不用 requests 的 cookie jar 自动合并？
        因为 requests 的 ``PreparedRequest.prepare_cookies`` 明确规定：
        **只要请求头里已有 ``Cookie``，就不再从 jar 生成**（见 requests/models.py 的文档注释）。
        本项目需要携带 ``os=pc`` 等静态字段，于是踩了这个坑：初版登录后
        ``MUSIC_U`` 从未真正发出，服务端一直当访客（``profile: null``）。
        所以这里自己合成 Cookie 头，不再依赖 jar 的自动注入。
        """
        pairs: dict[str, str] = {}
        for item in config.COOKIE_HINT.split(";"):
            item = item.strip()
            if "=" in item:
                name, _, value = item.partition("=")
                pairs[name.strip()] = value.strip()
        pairs.update(self.all_cookies())  # 会话 cookie 覆盖静态提示
        return "; ".join(f"{name}={value}" for name, value in pairs.items())

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": config.USER_AGENT,
            "Referer": config.REFERER,
            "Cookie": self.cookie_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
        }

    def all_cookies(self) -> dict[str, str]:
        """当前 cookie（含会话内新增）。"""
        out = dict(self._cookie_cache)
        if self._requests is not None:
            for c in self._requests.cookies:
                out[c.name] = c.value
        out.update(self._urllib_cookies)
        return out

    @property
    def logged_in(self) -> bool:
        """是否存在有效登录态（``MUSIC_U`` 为实名登录，``MUSIC_A`` 为匿名）。"""
        return bool(self.all_cookies().get("MUSIC_U"))

    @property
    def csrf(self) -> str:
        return self.all_cookies().get("__csrf", "")

    def set_cookie(self, name: str, value: str) -> None:
        """写入 cookie（同时写进 requests 会话与本地缓存）。"""
        self._cookie_cache[name] = value
        if self._requests is not None:
            self._requests.cookies.set(name, value, domain="music.163.com", path="/")
        self.save_cookies()

    def clear_cookies(self) -> None:
        """退出登录：清空会话 cookie 与本地文件。"""
        self._cookie_cache.clear()
        self._urllib_cookies.clear()
        if self._requests is not None:
            self._requests.cookies.clear()
        try:
            if config.COOKIE_FILE.exists():
                config.COOKIE_FILE.unlink()
        except OSError:
            pass
        _log.info("已清空本地 cookie")

    def load_cookies(self) -> None:
        if not config.COOKIE_FILE.exists():
            return
        try:
            data = json.loads(config.COOKIE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        self._cookie_cache.update({str(k): str(v) for k, v in data.items()})
        for name, value in self._cookie_cache.items():
            if self._requests is not None:
                self._requests.cookies.set(name, value, domain="music.163.com", path="/")
            self._urllib_cookies[name] = value
        _log.info("已载入本地 cookie：%s", ", ".join(sorted(self._cookie_cache)) or "<空>")

    def save_cookies(self) -> None:
        """把当前 cookie 落盘（内容变化时才写，避免频繁 IO）。"""
        cookies = self.all_cookies()
        dump = json.dumps(cookies, ensure_ascii=False, sort_keys=True)
        if dump == self._last_cookie_dump:
            return
        try:
            config.write_private(config.COOKIE_FILE, json.dumps(cookies, ensure_ascii=False, indent=2))
            self._last_cookie_dump = dump
        except OSError as exc:
            _log.warning("cookie 落盘失败：%s", exc)

    # -------------------------------------------------- 请求
    def request(
        self,
        endpoint: ep.Endpoint,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        path: str | None = None,
        need_login: bool | None = None,
        allow_eapi_retry: bool = True,
        ok_codes: set[int] | None = None,
    ) -> dict[str, Any]:
        """发起请求并返回 JSON。

        :param ok_codes: 额外视为“成功”的业务码（如二维码轮询的 800/801/802/803，
            它们不是错误而是状态），不传则只有 200/0/无 code 算成功
        :raises NeedLoginError: 未登录或登录失效
        :raises NetworkError: 网络异常/响应非 JSON
        :raises ApiError: 业务错误码
        """
        requires_login = endpoint.need_login if need_login is None else need_login
        if requires_login and not self.logged_in:
            raise NeedLoginError(301, CODE_MESSAGES[301], str(endpoint))

        full_path = path or endpoint.path
        payload = dict(data or {})
        query = dict(params or {})
        if endpoint.method == "POST" and self.csrf:
            payload.setdefault("csrf_token", self.csrf)

        text, status = self._send(endpoint.method, full_path, payload, query, str(endpoint))

        # 明文通道失败 → eapi 重试一次
        if allow_eapi_retry and endpoint.eapi and self._should_retry_eapi(text, status):
            _log.info("明文通道返回异常，改用 eapi 重试：%s", full_path)
            eapi_data = {**payload}
            eapi_query = {k: v for k, v in query.items()}
            enc = crypto.eapi_params(full_path.split("?")[0], {**payload, **eapi_query})
            eapi_ep = ep.Endpoint(crypto.eapi_path(full_path), method="POST", eapi=False)
            text, status = self._send("POST", eapi_ep.path, enc, {}, str(endpoint) + " (eapi)")

        return self._parse(text, str(endpoint), status, ok_codes)

    # -------------------------------------------------- 内部
    def _send(
        self, method: str, path: str, data: dict[str, Any], params: dict[str, Any], tag: str
    ) -> tuple[str, int]:
        url = self.base_url + path
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRY + 1):
            try:
                if self._requests is not None:
                    resp = self._requests.request(
                        method,
                        url,
                        data=data if method == "POST" else None,
                        params=params or None,
                        headers=self._headers(),
                        timeout=config.TIMEOUT,
                    )
                    text, status = resp.text, resp.status_code
                else:  # pragma: no cover - 回退路径
                    text, status = _urllib_request(method, url, data, params, self._headers())
                if attempt == 0 or status < 500:
                    self._sync_jar_to_cache()
                    self.save_cookies()
                return text, status
            except Exception as exc:  # requests.RequestException 等
                last_exc = exc
                if attempt < config.MAX_RETRY:
                    time.sleep(0.4 * (attempt + 1))
                    _log.debug("请求重试 %s（第 %d 次）：%s", tag, attempt + 2, exc)
        raise NetworkError(f"网络请求失败：{last_exc}", tag)

    def _sync_jar_to_cache(self) -> None:
        """把 jar 里新增/更新的 cookie 同步进本地缓存（服务端下发 Set-Cookie 时）。"""
        if self._requests is None:
            return
        changed = False
        for c in self._requests.cookies:
            if self._cookie_cache.get(c.name) != c.value:
                self._cookie_cache[c.name] = c.value
                changed = True
        if changed:
            _log.info("服务端更新了 cookie：%s", ", ".join(sorted(self._cookie_cache)))

    def _should_retry_eapi(self, text: str, status: int) -> bool:
        if status >= 400:
            return True
        if not text.strip():
            return True
        try:
            json.loads(text)
        except json.JSONDecodeError:
            return True
        return any(hint in text for hint in _EAPI_RETRY_HINTS) and "参数错误" in text

    def _parse(self, text: str, tag: str, status: int, ok_codes: set[int] | None = None) -> dict[str, Any]:
        if not text.strip():
            raise NetworkError("服务端返回空响应", tag, raw=text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # 网易偶尔返回两个拼接的 JSON（风控/重试叠加），尝试取最后一个完整对象
            payload = _parse_concatenated(text)
            if payload is None:
                raise NetworkError("服务端返回了非 JSON 响应（可能被风控拦截）", tag, raw=text[:200]) from exc
        code = payload.get("code")
        if code in (200, 0, None) or (ok_codes and int(code) in ok_codes):
            return payload
        if code in (301, -462):
            raise NeedLoginError(int(code), CODE_MESSAGES[int(code)], tag, raw=payload)
        raise ApiError(int(code), str(payload.get("message") or payload.get("msg") or "请求失败"), tag, raw=payload)


# ---------------------------------------------------------------- 辅助


def _parse_concatenated(text: str) -> dict[str, Any] | None:
    """解析 ``{...}{...}`` 这种拼接响应，返回最后一个合法对象。"""
    decoder = json.JSONDecoder()
    idx = 0
    found: dict[str, Any] | None = None
    while idx < len(text):
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            found = obj
        idx = end
        while idx < len(text) and text[idx] in " \r\n\t":
            idx += 1
    return found


def _new_requests_session():
    s = requests.Session()
    s.headers.update({"Accept-Encoding": "gzip, deflate"})
    return s


def _urllib_request(  # pragma: no cover - 回退路径
    method: str, url: str, data: dict[str, Any], params: dict[str, Any], headers: dict[str, str]
) -> tuple[str, int]:
    """无 requests 时的最小回退实现。"""
    import urllib.error
    import urllib.request

    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    body = urlencode(data).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT[1]) as resp:  # noqa: S310
            return resp.read().decode("utf-8", "replace"), resp.status
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8", "replace"), exc.code


#: 全局默认会话（UI 层通过它共享登录态）
_default: Session | None = None


def default_session() -> Session:
    global _default
    if _default is None:
        _default = Session()
    return _default
