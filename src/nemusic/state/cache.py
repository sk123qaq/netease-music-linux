"""缓存管理：音频（边播边存）、歌单、歌词，以及统计 / LRU 淘汰 / 清理（SPEC §2.7 / AC-5）。

命名约定::

    cache/audio/<songId>_<level>.<ext>      正式缓存（可被 mpv 直接打开）
    cache/audio/.<songId>_<level>.<ext>     录制中的临时文件（隐藏）
    cache/playlist/playlist_<id>.json
    cache/lyric/lyric_<songId>.json

两个踩过的坑（都有测试盯着）：
1. ``stream-record`` 的输出格式由**扩展名**决定，未知后缀 mpv 会**静默不写文件**，
   所以临时文件也必须带正确扩展名（用隐藏文件而不是 ``.part``）。
2. 中途切歌/拖动进度会写出**不完整**文件，必须用 API 返回的 ``size`` 校验后再提交。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .. import config as app_config
from ..util import log

_log = log.get("state.cache")

#: 校验容差（±1%，SPEC §2.5）
#: 大小校验容差。正常情况 ±1% 即可；确认真听完了整首时放宽到 ±5%，
#: 以容忍网络尾包丢失造成的少量缺字节（判据见 controller._finish_record）。
SIZE_TOLERANCE = 0.01
SIZE_TOLERANCE_PLAYED = 0.05


class CacheManager:
    """缓存目录的唯一入口（其它模块不要自己拼路径）。"""

    def __init__(self, cfg: app_config.Config | None = None) -> None:
        self.cfg = cfg or app_config.Config.load()

    # ============================================================ 封面
    def cover_path(self, url: str) -> Path:
        """封面缓存路径：URL 的 md5（同一 URL 只下一份）。"""
        digest = hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
        return app_config.COVER_CACHE_DIR / f"cover_{digest}.img"

    def cover_lookup(self, url: str) -> Path | None:
        """命中且未过期才返回；过期/不存在返回 None。"""
        if not url:
            return None
        path = self.cover_path(url)
        try:
            st = path.stat()
        except OSError:
            return None
        if st.st_size <= 0:
            return None
        if time.time() - st.st_mtime > app_config.COVER_CACHE_TTL:
            return None
        return path

    # ============================================================ 音频
    def audio_final_path(self, song_id: int, level: str, ext: str) -> Path:
        return app_config.AUDIO_CACHE_DIR / f"{int(song_id)}_{level}.{ext}"

    def audio_part_path(self, song_id: int, level: str, ext: str) -> Path:
        """录制中的临时文件：隐藏 + **正确扩展名**（坑 1）。"""
        return app_config.AUDIO_CACHE_DIR / f".{int(song_id)}_{level}.{ext}"

    def audio_lookup(self, song_id: int) -> Path | None:
        """查已缓存的音频（任意音质），返回可播放的本地路径。

        只认**正式缓存文件**，排除隐藏的 ``.`` 临时文件（坑：``glob`` 的 ``*``
        会匹配隐藏文件，录制中的半成品曾被当成缓存命中，导致播放半首歌且
        再也不录制——见 docs/acceptance.md）。
        """
        if not self.cfg.cache_audio_enabled:
            return None
        for path in sorted(app_config.AUDIO_CACHE_DIR.glob(f"{int(song_id)}_*")):
            if path.name.startswith("."):
                continue
            if path.is_file() and path.stat().st_size > 0:
                return path
        return None

    def commit_audio(
        self,
        part: Path,
        song_id: int,
        level: str,
        ext: str,
        expected_size: int = 0,
        *,
        played_to_end: bool = False,
    ) -> Path | None:
        """校验并提交录制文件；不完整则删除并返回 None（AC-5.2）。

        ``played_to_end=True`` 表示控制器已确认这首听完了（位置到底），
        此时大小容差放宽：尾包丢失会少几百 KB，但内容确实是完整的。
        """
        if not part.exists():
            return None
        actual = part.stat().st_size
        if actual <= 0:
            self.discard(part)
            return None
        if expected_size > 0:
            tolerance = SIZE_TOLERANCE_PLAYED if played_to_end else SIZE_TOLERANCE
            low = expected_size * (1 - tolerance)
            high = expected_size * (1 + tolerance)
            if not (low <= actual <= high):
                _log.info(
                    "缓存不完整，丢弃：%s（实际 %d 字节，期望 ~%d）", part.name, actual, expected_size
                )
                self.discard(part)
                return None
        final = self.audio_final_path(song_id, level, ext)
        try:
            part.replace(final)
        except OSError as exc:
            _log.warning("缓存提交失败：%s", exc)
            self.discard(part)
            return None
        _log.info("已缓存音频：%s（%.1f MB）", final.name, actual / 1024**2)
        self.trim_audio()
        return final

    def discard(self, part: Path | None) -> None:
        """删除临时录制文件（中途切歌时调用）。"""
        if part is None:
            return
        try:
            part.unlink()
            _log.info("已清理临时缓存：%s", part.name)
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover
            _log.warning("临时缓存清理失败：%s", exc)

    def sweep_parts(self, keep: Path | None = None) -> int:
        """清掉所有遗留的隐藏临时文件（上次异常退出的残留 / 切歌竞态的孤儿）。

        ``keep`` 为**正在录制**的临时文件路径（控制器 ``_record`` 里的那个）。
        为什么需要它：切歌时若下一首的 ``loadfile`` 刚发出去，mpv 还没创建临时文件，
        此时的丢弃会因 ``part.exists()`` 为 False 而静默跳过，等 mpv 建出文件就已无人认领
        （真机复现：连点几首歌后残留一堆完整大小的 ``.mp3``，见 acceptance.md）。
        """
        removed = 0
        for path in app_config.AUDIO_CACHE_DIR.glob(".*"):
            if not path.is_file() or (keep is not None and path == keep):
                continue
            self.discard(path)
            removed += 1
        return removed

    def _audio_files(self) -> list[Path]:
        """正式音频缓存文件列表（**排除隐藏的临时文件**）。

        注意：``pathlib.Path.glob()`` 与 ``glob.glob()`` 不同，``*`` **会**匹配隐藏文件，
        所以这里必须手动过滤，否则录制中的半成品会被算进统计/淘汰。
        """
        return [p for p in app_config.AUDIO_CACHE_DIR.glob("*") if p.is_file() and not p.name.startswith(".")]

    def trim_audio(self, limit_bytes: int | None = None) -> int:
        """按 LRU（mtime 最旧优先）淘汰，直到低于上限（AC-5.6）。"""
        limit = self.cfg.cache_limit_bytes if limit_bytes is None else limit_bytes
        files = self._audio_files()
        total = sum(p.stat().st_size for p in files)
        if total <= limit:
            return 0
        removed = 0
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= limit:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
                removed += 1
            except OSError:  # pragma: no cover
                continue
        if removed:
            _log.info("按 LRU 淘汰了 %d 个音频缓存（当前 %.1f MB / 上限 %.1f MB）", removed, total / 1024**2, limit / 1024**2)
        return removed

    # ============================================================ 歌单 / 歌词
    def _json_path(self, kind: str, key: str | int) -> Path:
        if kind == "playlist":
            return app_config.PLAYLIST_CACHE_DIR / f"playlist_{key}.json"
        if kind == "lyric":
            return app_config.LYRIC_CACHE_DIR / f"lyric_{key}.json"
        raise ValueError(f"未知缓存类型：{kind}")

    def get_json(self, kind: str, key: str | int, ttl: float) -> dict[str, Any] | None:
        path = self._json_path(kind, key)
        if not path.exists():
            return None
        try:
            if ttl > 0 and time.time() - path.stat().st_mtime > ttl:
                _log.debug("缓存过期：%s", path.name)
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def put_json(self, kind: str, key: str | int, payload: dict[str, Any]) -> None:
        path = self._json_path(kind, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            _log.warning("缓存写入失败 %s：%s", path.name, exc)

    # ============================================================ 统计 / 清理
    def dir_stats(self, kind: str) -> tuple[int, int]:
        """返回 (文件数, 字节数)；隐藏的临时文件不计入。"""
        if kind == "audio":
            directory, pattern = app_config.AUDIO_CACHE_DIR, None
        elif kind == "playlist":
            directory, pattern = app_config.PLAYLIST_CACHE_DIR, "*.json"
        elif kind == "lyric":
            directory, pattern = app_config.LYRIC_CACHE_DIR, "*.json"
        elif kind == "cover":
            directory, pattern = app_config.COVER_CACHE_DIR, "*.img"
        else:
            raise ValueError(f"未知缓存类型：{kind}")
        count = total = 0
        if pattern is None:
            files: list[Path] = self._audio_files()
        else:
            files = [p for p in directory.glob(pattern) if p.is_file() and not p.name.startswith(".")]
        for path in files:
            count += 1
            total += path.stat().st_size
        return count, total

    def stats(self) -> dict[str, dict[str, float]]:
        """给设置页用的缓存统计（AC-5.5）。"""
        out: dict[str, dict[str, float]] = {}
        for kind in ("audio", "playlist", "lyric", "cover"):
            count, size = self.dir_stats(kind)
            out[kind] = {"count": count, "bytes": size, "mb": round(size / 1024**2, 2)}
        return out

    def clear(self, kind: str) -> tuple[int, int]:
        """清理某一类缓存，返回 (删除文件数, 释放字节数)。``kind='all'`` 清全部。"""
        if kind == "all":
            counts = [self.clear(k) for k in ("audio", "playlist", "lyric", "cover")]
            return sum(c[0] for c in counts), sum(c[1] for c in counts)
        count, size = self.dir_stats(kind)
        directory = {
            "audio": app_config.AUDIO_CACHE_DIR,
            "playlist": app_config.PLAYLIST_CACHE_DIR,
            "lyric": app_config.LYRIC_CACHE_DIR,
            "cover": app_config.COVER_CACHE_DIR,
        }[kind]
        for path in directory.iterdir() if directory.exists() else []:
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:  # pragma: no cover
                continue
        _log.info("已清理 %s 缓存：%d 个文件 / %.1f MB", kind, count, size / 1024**2)
        return count, size

    def open_dir(self, kind: str = "audio") -> Path:
        """设置页"打开缓存目录"用。"""
        directory = {
            "audio": app_config.AUDIO_CACHE_DIR,
            "playlist": app_config.PLAYLIST_CACHE_DIR,
            "lyric": app_config.LYRIC_CACHE_DIR,
            "cover": app_config.COVER_CACHE_DIR,
        }.get(kind, app_config.CACHE_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def audio_size_bytes(self) -> int:
        return self.dir_stats("audio")[1]

    @staticmethod
    def format_size(num_bytes: float) -> str:
        """人类可读体积。"""
        for unit in ("B", "KB", "MB", "GB"):
            if abs(num_bytes) < 1024 or unit == "GB":
                return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024
        return f"{num_bytes:.1f} GB"


#: 模块级便捷函数（设置页等处直接 from ..state.cache import format_size）
format_size = CacheManager.format_size

__all__ = ["CacheManager", "SIZE_TOLERANCE", "SIZE_TOLERANCE_PLAYED", "format_size"]
