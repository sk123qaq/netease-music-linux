#!/usr/bin/env python3
"""端到端播放验证（**需要联网 + 已登录 + 系统 mpv**，手动运行）。

验证那些只有真跑一遍才能确认的条目：
- AC-2.2 / AC-4.1  能播放、位置推进、上一首/下一首生效
- AC-5.1           播完落盘且文件大小与 API 声明一致（完整提交）
- AC-5.2           中途切歌不留半首歌（丢弃 .part）
- AC-5.3           第二次播放走本地缓存（不再取流）
- AC-4.8           （可选）播放失败时自动重取

用法::

    python tests/e2e_playback.py                 # 默认静音输出（--ao=null），不动真实缓存
    python tests/e2e_playback.py --audio         # 用系统默认声卡（能听到声音）
    python tests/e2e_playback.py --keep-cache    # 写进真实缓存目录（默认写临时目录）

默认会把真实 cookie **复制**到临时配置目录，因此不会污染真实缓存/配置。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import nemusic.config as config  # noqa: E402
from nemusic.model.types import Song  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="网易云播放链路端到端验证")
    p.add_argument("--audio", action="store_true", help="使用真实声卡输出（否则 --ao=null）")
    p.add_argument("--keep-cache", action="store_true", help="写入真实缓存目录")
    p.add_argument("--wait-ms", type=int, default=20000, help="等待开播的超时（毫秒）")
    p.add_argument("--level", default="", help="限定音质（如 standard）；默认按 exhigh→higher→standard 降级。"
                                              "320k 试听流可能被服务端截断，此时用 --level standard 复测缓存")
    p.add_argument("--report", default=str(ROOT / "docs" / "e2e-report.md"), help="报告输出路径")
    return p.parse_args()


def use_temp_config() -> Path:
    """把真实 cookie 复制到临时目录，隔离缓存/配置。"""
    real = config.CONFIG_DIR
    tmp = Path(tempfile.mkdtemp(prefix="nemusic-e2e-"))
    config.CONFIG_DIR = tmp
    config.CONFIG_FILE = tmp / "config.json"
    config.COOKIE_FILE = tmp / "cookies.json"
    config.SEARCH_HISTORY_FILE = tmp / "search_history.json"
    config.CACHE_DIR = tmp / "cache"
    config.AUDIO_CACHE_DIR = tmp / "cache" / "audio"
    config.PLAYLIST_CACHE_DIR = tmp / "cache" / "playlist"
    config.LYRIC_CACHE_DIR = tmp / "cache" / "lyric"
    config.LOG_DIR = tmp / "logs"
    config.LOG_FILE = tmp / "logs" / "app.log"
    config.RUNTIME_DIR = tmp / "run"
    config.ensure_dirs()
    for name in ("cookies.json", "config.json"):
        src = real / name
        if src.exists():
            shutil.copy2(src, tmp / name)
    return tmp


class Recorder:
    """收集信号事件 + 判定结果。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.results: list[tuple[str, bool, str]] = []

    def add(self, line: str) -> None:
        self.events.append(line)
        print("   ·", line)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        print(f"   {'✅' if ok else '❌'} {name}{'：' + detail if detail else ''}")

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.results if ok)


def _truncation_note(log_file: Path, song_id: int) -> str | None:
    """从日志里找"缓存不完整，丢弃"记录，返回字节差说明（用于区分网络截断与逻辑 bug）。"""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if "缓存不完整，丢弃" in line and f".{song_id}_" in line:
            return line.split("缓存不完整，丢弃：")[-1].strip()
    return None


def main() -> int:
    args = parse_args()
    if not args.keep_cache:
        tmp = use_temp_config()
        print(f"隔离配置目录：{tmp}")
    else:
        config.ensure_dirs()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from nemusic.net.api import Api
    from nemusic.player.controller import PlaybackController
    from nemusic.player.mpv import MpvPlayer, available as mpv_available
    from nemusic.state.cache import CacheManager
    from nemusic.state.session_state import SessionState

    rec = Recorder()
    if not mpv_available():
        print("❌ 系统缺少 mpv")
        return 1

    cfg = config.Config.load()
    cfg.cache_audio_enabled = True
    if args.level:
        # 只用一个音质：避开"320k 试听被服务端截断"的干扰，专测缓存链路
        config.QUALITY_LADDER = (args.level,)
    api = Api()
    session = SessionState(api, cfg)
    if not session.restore():
        print("❌ 未登录（请先在应用里扫码，或确认 cookies.json 存在）")
        return 1
    print(f"账号：{session.nickname} (uid={session.uid})")

    star = api.star_playlist(session.uid)
    if star is None:
        print("❌ 没找到「我喜欢的音乐」歌单")
        return 1
    detail = api.playlist_detail(star.id, limit=500)
    songs = detail.tracks
    print(f"「我喜欢的音乐」：{star.track_count} 首（本次取 {len(songs)} 首做样本）")
    if not songs:
        print("❌ 歌单里没有可用曲目")
        return 1

    # 找一首最短的歌，便于完整验证"播完 → 落盘"
    pool = api.song_detail(detail.track_ids[:300])
    pool = [s for s in pool if s.duration_ms > 0]
    shortest = min(pool, key=lambda s: s.duration_ms) if pool else songs[0]
    print(f"最短样本：{shortest.name} - {shortest.artist_text}（{shortest.duration_text}）\n")

    cache = CacheManager(cfg)
    extra = () if args.audio else ("--ao=null",)
    player = MpvPlayer(extra_args=extra)
    ctl = PlaybackController(api, cfg, cache, session, player=player)
    ctl.set_volume(cfg.volume if args.audio else 0)

    positions: list[int] = []
    cache_events: list[tuple[int, str]] = []
    sources: list[tuple[int, str, int, str]] = []
    errors: list[str] = []
    ctl.progress.connect(lambda p, _d: positions.append(p) if p >= 0 else None)
    ctl.cache_event.connect(lambda sid, kind: cache_events.append((sid, kind)))
    ctl.source_changed.connect(lambda *a: sources.append(a))
    ctl.error.connect(errors.append)
    ctl.song_changed.connect(lambda s: rec.add(f"开始播放：{s.name if s else '(空)'}"))

    def wait(predicate, timeout_ms: int, label: str) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
            if predicate():
                return True
        app.processEvents()
        print(f"   ⏳ 等待超时：{label}")
        return False

    # ---------------------------------------------------------- 1) 能播放
    print("【1】播放第一首（验证能出声 / 位置推进）")
    ctl.play_songs(songs[:5], 0, context="我喜欢的音乐")
    started = wait(lambda: any(p > 500 for p in positions), args.wait_ms, "开播（位置 > 0.5s）")
    rec.check("能播放且进度推进", started, f"位置 {positions[-1] / 1000:.1f}s" if positions else "无进度")

    # ---------------------------------------------------------- 2) 切歌丢弃半成品
    print("\n【2】播放中切下一首（验证不留半首歌）")
    before = len(cache_events)
    ctl.next()
    discarded = wait(lambda: len(cache_events) > before and any(k == "discarded" for _, k in cache_events), 8000, "丢弃缓存")
    # 注意：新歌正在边播边存，它自己的临时文件存在是正常的，
    # 这里只检查**上一首**的临时文件已经被清掉（AC-5.2）。
    stale = [p for p in config.AUDIO_CACHE_DIR.glob(".*")
             if ctl.current_song and int(p.name.strip(".").split("_")[0]) != ctl.current_song.id]
    rec.check("切歌后临时文件被清理", discarded and not stale, f"残留 {len(stale)} 个旧临时文件")

    # ---------------------------------------------------------- 3) 播完落盘
    print(f"\n【3】完整播放最短样本并校验落盘：《{shortest.name}》{shortest.duration_text}")
    url_info = api.song_url_best(shortest.id)
    api_size = url_info.size or 0
    print(f"   本次取流：level={url_info.level} size={api_size}（服务端对 320k 试听可能限流截断）")
    ctl.play_song(shortest)
    wait(lambda: ctl.current_song is not None and ctl.current_song.id == shortest.id, 5000, "切换歌曲")
    stored = wait(lambda: any(k == "stored" for sid, k in cache_events if sid == shortest.id), (shortest.duration_ms + 30_000), "播完并提交缓存")
    cached_file = cache.audio_lookup(shortest.id)
    truncated = None
    if not stored and cached_file is None:
        # 没落盘时区分两种情况：网络把流截断了（按设计丢弃）还是逻辑坏了
        truncated = _truncation_note(config.LOG_FILE, shortest.id)
    if stored and cached_file is not None:
        delta = abs(cached_file.stat().st_size - api_size) / api_size if api_size else 0
        rec.check("播完后写入缓存文件", delta <= 0.05,
                  f"{cached_file.name} {cached_file.stat().st_size} B（API {api_size} B，差 {delta:.2%}）")
    elif truncated is not None:
        rec.check("播完后写入缓存文件", False,
                  f"网络截断，已按设计丢弃（{truncated}）——非逻辑问题，见 acceptance.md")
    else:
        rec.check("播完后写入缓存文件", False, "无文件且未记录丢弃原因")

    # ---------------------------------------------------------- 4) 二次播放走缓存
    print("\n【4】再次播放同一首（应命中本地缓存，不再请求取流）")
    if cached_file is None:
        rec.check("第二次播放为本地文件直读", False, "上一步没有缓存文件，跳过")
    else:
        ctl.play_song(songs[0])  # 先切走
        wait(lambda: True, 1500, "切换")
        src_before = len(sources)
        ctl.play_song(shortest)
        hit = wait(lambda: any(s[3] == "cache" for s in sources[src_before:]), 10000, "命中缓存")
        rec.check("第二次播放为本地文件直读", hit, str(sources[-1] if sources else ""))

    # ---------------------------------------------------------- 5) 上下首
    print("\n【5】上一首 / 下一首")
    ctl.play_songs(songs[:5], 2, context="模式测试")
    wait(lambda: any(p > 300 for p in positions[-20:]), 8000, "第三首开播")
    current_before = ctl.current_song.id if ctl.current_song else 0
    ctl.next()
    wait(lambda: ctl.current_song and ctl.current_song.id != current_before, 8000, "下一首")
    nxt = ctl.current_song.id if ctl.current_song else 0
    ctl.prev()
    wait(lambda: ctl.current_song and ctl.current_song.id != nxt, 8000, "上一首")
    back = ctl.current_song.id if ctl.current_song else 0
    rec.check("下一首/上一首切歌正确", nxt != current_before and back != nxt, f"{current_before} → {nxt} → {back}")

    print("\n【6】清理与收尾")
    ctl.shutdown()
    leftovers = list(config.RUNTIME_DIR.glob("*.sock"))
    rec.check("退出后无 socket 残留", not leftovers, f"{len(leftovers)} 个")
    stats = cache.stats()
    print(f"   缓存统计：音频 {stats['audio']['count']} 个 / {stats['audio']['mb']} MB")

    if errors:
        print("\n错误记录：")
        for e in errors:
            print("   !", e)

    print(f"\n===== 端到端结果：{rec.passed}/{len(rec.results)} 通过 =====")
    write_report(args, rec, sources, cache_events)
    return 0 if rec.passed == len(rec.results) else 1


def write_report(args, rec: "Recorder", sources, cache_events) -> None:
    """把结果写成 markdown 报告（便于贴进验收清单）。"""
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 端到端播放验证报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 输出：{'真实声卡' if args.audio else '--ao=null（静音）'}",
        f"- 结果：**{rec.passed}/{len(rec.results)} 通过**",
        "",
        "| 检查项 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for name, ok, detail in rec.results:
        lines.append(f"| {name} | {'✅' if ok else '❌'} | {detail} |")
    lines += ["", "## 播放源变化（音质/缓存来源）", "", "```"]
    lines += [f"{sid} level={level or '-'} br={br or 0} source={src}" for sid, level, br, src in sources]
    lines += ["```", "", "## 缓存事件", "", "```"]
    lines += [f"song {sid}: {kind}" for sid, kind in cache_events]
    lines += ["```", "", "## 事件流", "", "```"]
    lines += rec.events[-40:]
    lines += ["```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入：{path}")


if __name__ == "__main__":
    raise SystemExit(main())
