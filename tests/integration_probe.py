"""联网只读探测：验证接口可用性与关键参数（**不写账号数据、不修改真实 cookie**）。

用法::

    NET=1 scripts/selftest.sh          # 由自检脚本调用
    python tests/integration_probe.py  # 直接运行
    NET_PLAY=1 python tests/integration_probe.py   # 额外做 mpv 播放实测

覆盖 SPEC §8 的 E2/E5/E6/E7/E8/E14/E15/E16。任何写操作（收藏/登录）都不在这里执行。

``SEARCH_UI=1`` 时额外用**真接口**驱动搜索页 UI（offscreen）：验证
「状态机 → 表格渲染 → 播放/收藏信号」这条链路在真实数据下也通。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from support import redirect_config  # noqa: E402

from nemusic.net import crypto  # noqa: E402
from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.net.api import Api  # noqa: E402
from nemusic.net.session import ApiError, Session  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

DEMO_SONG = 5257138  # 公开可搜索到的免费曲目，仅用于只读探测
DEMO_ARTIST = 6452  # 周杰伦
DEMO_ALBUM = 32311  # 神的游戏
DEMO_PLAYLIST = 3778678  # 热歌榜


def check(name: str, fn) -> None:
    try:
        detail = fn()
        RESULTS.append((name, True, str(detail or "")))
        print(f"  ✅ {name}: {detail}")
    except Exception as exc:  # noqa: BLE001 - 探测器需要报告所有失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  ❌ {name}: {type(exc).__name__}: {exc}")


def probe_search_ui(api: Api) -> None:
    """真接口 + 真 UI：搜索一页 → 表格渲染 → 双击/收藏信号（AC-2.1~2.5）。"""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        print("\n【搜索页 UI】⏭ 缺少 PySide6，跳过")
        return

    print("\n【搜索页 UI（offscreen，真接口）】")
    app = QApplication.instance() or QApplication([])

    from nemusic.net import endpoints as ep
    from nemusic.state.search_history import SearchHistory
    from nemusic.state.search_state import SearchState
    from nemusic.ui.search_view import SearchView

    state = SearchState(api, history=SearchHistory())
    view = SearchView(state)
    view.resize(1000, 620)
    view.show()

    def spin(ms: int) -> None:
        deadline = time.time() + ms / 1000
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)

    view.input.setText("周杰伦")
    view._do_search()
    spin(6000)

    tab = state.tab(ep.SEARCH_TYPE_SONG)
    check(
        "单曲 tab 渲染行数 = 状态机条数",
        lambda: f"表格 {view._tables[ep.SEARCH_TYPE_SONG].rowCount()} 行 / 状态 {tab.count} 条"
        if view._tables[ep.SEARCH_TYPE_SONG].rowCount() == tab.count and tab.count > 0
        else (_ for _ in ()).throw(AssertionError(f"表格 {view._tables[ep.SEARCH_TYPE_SONG].rowCount()} != 状态 {tab.count}")),
    )
    check("页脚提示含已加载条数", lambda: view.footer.text() if "已加载" in view.footer.text() or "共" in view.footer.text() else (_ for _ in ()).throw(AssertionError(view.footer.text())))
    check("空状态已隐藏", lambda: "是" if not view.empty.isVisible() else (_ for _ in ()).throw(AssertionError("仍显示空状态")))

    played: list[tuple] = []
    liked: list[tuple] = []
    entities: list[tuple] = []
    view.play_requested.connect(lambda items, i: played.append((len(items), i)))
    view.like_toggled.connect(lambda song, want: liked.append((song.id, want)))
    view.open_entity.connect(lambda kind, eid: entities.append((kind, eid)))

    if tab.count:
        view._on_row_activated(ep.SEARCH_TYPE_SONG, 0)
        check("双击单曲 → play_requested", lambda: str(played[-1]) if played else (_ for _ in ()).throw(AssertionError("没有信号")))
        button = view._like_buttons[ep.SEARCH_TYPE_SONG].get(tab.items[0].id)
        if button is not None:
            view._toggle_like(tab.items[0], button)
            check("点红心 → like_toggled(期望 True)", lambda: str(liked[-1]) if liked and liked[-1][1] else (_ for _ in ()).throw(AssertionError(str(liked))))
        check("外部同步收藏状态不报错", lambda: (view.set_liked(tab.items[0].id, True), "ok")[1])

    # 另外三个 tab 也各搜一页，验证实体表渲染与双击信号
    for stype, label, kind in ((ep.SEARCH_TYPE_ARTIST, "歌手", "artist"), (ep.SEARCH_TYPE_ALBUM, "专辑", "album"), (ep.SEARCH_TYPE_PLAYLIST, "歌单", "playlist")):
        state.search("周杰伦", stype)
        spin(6000)

        def _probe_tab(stype=stype, label=label, kind=kind) -> str:
            state_tab = state.tab(stype)
            view._switch_tab(stype)          # 切 tab 才会重绘这个表
            spin(50)
            rows = view._tables[stype].rowCount()
            view._on_row_activated(stype, 0)  # 双击第一行
            if rows != state_tab.count or rows == 0:
                raise AssertionError(f"{label} 表格 {rows} 行 != 状态 {state_tab.count} 条")
            if not entities or entities[-1][0] != kind:
                raise AssertionError(f"双击没有发出 {kind} 信号：{entities}")
            return f"{rows} 行 / 状态 {state_tab.count} 条 / 信号 {entities[-1]}"

        check(f"{label} tab 渲染 + 双击 → open_entity", _probe_tab)

    # 历史下拉
    check("搜索历史已记录关键词", lambda: str(state.history.items) if state.history.items else (_ for _ in ()).throw(AssertionError("历史为空")))
    view.close()


def main() -> int:
    tmp = redirect_config(tempfile.mkdtemp(prefix="nemusic-probe-"))
    print(f"探测会话（隔离 cookie 目录）：{tmp}\n")
    session = Session()
    api = Api(session)

    print("【加密 / 会话】")
    check("AES 向量自检", crypto.selftest)
    check("eapi 报文可构造", lambda: f"{len(crypto.eapi_params('/api/login/qrcode/unikey', {'type': 1})['params'])} hex 字符")

    print("\n【登录（只取 unikey，不扫码）】")
    holder: dict[str, str] = {}

    def _unikey() -> str:
        key = api.qr_unikey()
        holder["key"] = key
        return f"unikey={key[:8]}…  扫码链接={Api.qr_content(key)[:46]}…"

    check("申请二维码 key（SPEC E2）", _unikey)
    if holder.get("key"):
        check(
            "轮询一次（应为 801 等待扫描）",
            lambda: f"code={api.qr_poll(holder['key']).code}",
        )

    print("\n【搜索四类（SPEC E14）】")
    for stype, label in ((1, "单曲"), (100, "歌手"), (10, "专辑"), (1000, "歌单")):
        check(
            f"搜索 type={stype} {label}",
            lambda stype=stype: (
                lambda page: f"total={page.total} 返回={len(page.items)} 首个={getattr(page.items[0], 'name', '') if page.items else '—'}"
            )(api.search("周杰伦", stype, limit=5)),
        )

    print("\n【歌手 / 专辑 / 歌单（SPEC E15/E6）】")
    check("歌手热门 50 首", lambda: f"{len(api.artist_top_songs(DEMO_ARTIST))} 首")
    check("专辑曲目", lambda: f"{api.album_songs(DEMO_ALBUM)[0].name} / {len(api.album_songs(DEMO_ALBUM)[1])} 首")
    check(
        "歌单详情 trackIds",
        lambda: (lambda pl: f"{pl.name} trackIds={len(pl.track_ids)} 首批曲目={len(pl.tracks)}")(api.playlist_detail(DEMO_PLAYLIST, limit=3)),
    )

    print("\n【歌词（SPEC E7，行级 + 翻译）】")
    check(
        "歌词接口",
        lambda: (lambda raw: f"lrc={'有' if (raw.get('lrc') or {}).get('lyric') else '无'} tlyric={'有' if (raw.get('tlyric') or {}).get('lyric') else '无'}")(api.lyric(DEMO_SONG)),
    )

    print("\n【取流（SPEC E8，未登录亦可用）】")
    url_holder: dict[str, str] = {}

    def _url() -> str:
        result = api.song_url_best(DEMO_SONG)
        if not result.playable:
            raise ApiError(result.code or -1, result.message or "无可用地址")
        url_holder["url"] = result.url or ""
        return f"level={result.level} br={result.br} type={result.type} size={result.size}"

    check("音质降级取流", _url)

    if os.environ.get("NET_PLAY") == "1" and url_holder.get("url"):
        print("\n【mpv 播放实测（SPEC E9/E16，需要系统 mpv）】")
        if not any(Path(p).exists() for p in ("/usr/bin/mpv", "/usr/local/bin/mpv")):
            print("  ⏭  未找到 mpv，跳过")
        else:
            out = Path(tempfile.mkdtemp(prefix="nemusic-play-")) / "rec.mp3"
            started = time.time()
            proc = subprocess.run(
                ["mpv", "--no-video", "--ao=null", "--really-quiet", f"--stream-record={out}", "--length=6", url_holder["url"]],
                capture_output=True,
                text=True,
                timeout=60,
            )
            size = out.stat().st_size if out.exists() else 0
            ok = proc.returncode == 0 and size > 100_000
            RESULTS.append(("mpv 播放+边播边存", ok, f"{size} 字节 / {time.time() - started:.1f}s"))
            print(f"  {'✅' if ok else '❌'} mpv 播放+边播边存: {size} 字节（退出码 {proc.returncode}）")
            if proc.stderr.strip():
                print("     stderr:", proc.stderr.strip()[:200])

    if os.environ.get("SEARCH_UI") == "1":
        probe_search_ui(api)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== 探测结果：{passed}/{total} 通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  失败：{name} — {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
