"""全局配置与路径（唯一权威来源，其它模块不得硬编码路径）。

目录布局（见 SPEC §5）::

    ~/.config/netease-music-linux/
    ├── config.json          # 音量/静音/播放模式/音质策略/缓存上限/窗口几何/上次页面
    ├── cookies.json         # 登录 cookie（明文，chmod 600）
    ├── search_history.json  # 搜索历史（≤50）
    ├── cache/
    │   ├── audio/           # <songId>_<level>.<ext>
    │   ├── playlist/        # playlist_<id>.json
    │   └── lyric/           # lyric_<songId>.json
    └── logs/app.log
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import APP_NAME

# ---------------------------------------------------------------- 路径


def _config_root() -> Path:
    """配置根目录，尊重 XDG_CONFIG_HOME。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


CONFIG_DIR: Path = _config_root()
CONFIG_FILE: Path = CONFIG_DIR / "config.json"
COOKIE_FILE: Path = CONFIG_DIR / "cookies.json"
SEARCH_HISTORY_FILE: Path = CONFIG_DIR / "search_history.json"
CACHE_DIR: Path = CONFIG_DIR / "cache"
AUDIO_CACHE_DIR: Path = CACHE_DIR / "audio"
PLAYLIST_CACHE_DIR: Path = CACHE_DIR / "playlist"
LYRIC_CACHE_DIR: Path = CACHE_DIR / "lyric"
COVER_CACHE_DIR: Path = CACHE_DIR / "cover"
#: 用户上传的应用背景图（外观页管理）
BACKGROUND_DIR: Path = CONFIG_DIR / "backgrounds"
LOG_DIR: Path = CONFIG_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "app.log"

# 运行期临时文件（socket 等）放 /tmp，退出时清理
RUNTIME_DIR: Path = Path("/tmp") / APP_NAME


def ensure_dirs() -> None:
    """创建所有需要的目录（幂等）。"""
    for d in (CONFIG_DIR, CACHE_DIR, AUDIO_CACHE_DIR, PLAYLIST_CACHE_DIR, LYRIC_CACHE_DIR, COVER_CACHE_DIR, BACKGROUND_DIR, LOG_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def write_private(path: Path, text: str) -> None:
    """以 600 权限写文件（用于 cookie 等敏感内容，见 SPEC §7.5）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- 常量

#: 音质自动降级阶梯（SPEC §2.3）：不请求无损/hires/全景声
QUALITY_LADDER: tuple[str, ...] = ("exhigh", "higher", "standard")

#: 各音质对应的缓存文件后缀名猜测
QUALITY_SUFFIX = {"standard": "mp3", "higher": "mp3", "exhigh": "mp3", "lossless": "flac", "hires": "flac"}

#: 请求头（与 PC 客户端一致的 web 形态；见 SPEC §2.2）
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REFERER = "https://music.163.com/"
BASE_URL = "https://music.163.com"
COOKIE_HINT = "os=pc; appver=3.1.40; osver=Linux"

#: 网络超时（秒）：(连接, 读取)
TIMEOUT = (6.0, 15.0)
MAX_RETRY = 2

#: 搜索分页每页条数
SEARCH_PAGE_SIZE = 30
#: 搜索历史上限
SEARCH_HISTORY_LIMIT = 50
#: 歌单/歌词缓存 TTL（秒）
PLAYLIST_CACHE_TTL = 24 * 3600
LYRIC_CACHE_TTL = 7 * 24 * 3600
#: TTL 字典（缓存管理器与各状态共用）
CACHE_TTL = {"playlist": PLAYLIST_CACHE_TTL, "lyric": LYRIC_CACHE_TTL}
#: 封面图 TTL：图片不常变，给 30 天
COVER_CACHE_TTL = 30 * 24 * 3600
#: 音频缓存默认上限（SPEC D2：2 GB）
DEFAULT_CACHE_LIMIT_BYTES = 2 * 1024**3


# ---------------------------------------------------------------- 配置对象


@dataclass
class Config:
    """持久化配置（config.json）。"""

    volume: int = 70
    muted: bool = False
    #: order | list_loop | single_loop | shuffle
    play_mode: str = "list_loop"
    #: 音质阶梯（只读展示，实际策略在 config.QUALITY_LADDER）
    quality: str = "exhigh"
    cache_limit_bytes: int = DEFAULT_CACHE_LIMIT_BYTES
    cache_audio_enabled: bool = True
    #: 窗口几何 [x, y, w, h]
    window_geometry: list[int] = field(default_factory=lambda: [100, 100, 1180, 720])
    #: 上次所在页面：login | library | search | settings | background
    last_page: str = "login"
    #: 外观：主题 light | dark
    theme: str = "light"
    #: 背景：预设 id（见 ui/theme.py PRESETS）或 "custom:<文件名>"
    background: str = "mist"
    #: 稳定设备 id（首次生成后固定，供服务端识别）
    device_id: str = ""
    #: 已登录用户（展示用缓存）
    uid: int = 0
    nickname: str = ""

    # -------------------------------------------------- 读写

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                raw.pop("blur_background", None)  # 旧版布尔开关，已被 blur_strength 取代
                for k, v in raw.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except (OSError, json.JSONDecodeError):  # 损坏则回退默认值，不阻断启动
                pass
        if not cfg.device_id:
            cfg.device_id = uuid.uuid4().hex[:32]
        if cfg.play_mode not in ("order", "list_loop", "single_loop", "shuffle"):
            cfg.play_mode = "list_loop"
        return cfg

    def save(self) -> None:
        ensure_dirs()
        CONFIG_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------------------------------------------- 便捷

    @property
    def cache_limit_mb(self) -> float:
        return self.cache_limit_bytes / 1024**2

    def set_cache_limit_mb(self, mb: float) -> None:
        self.cache_limit_bytes = max(50, int(mb)) * 1024**2

    #: 自定义背景的模糊度：0=用原图，100=最糊。
    #: 花照片（带字的截图之类）直接当背景，界面文字会没法读，所以默认开一档。
    blur_strength: int = 60

    @property
    def background_path(self) -> Path | None:
        """当前自定义背景图的绝对路径；用预设或没设置时返回 None。

        默认返回模糊版（导入时生成），关掉模糊才返回原图。
        """
        if not self.background.startswith("custom:"):
            return None
        name = self.background[len("custom:"):]
        candidate = BACKGROUND_DIR / name
        if not candidate.is_file():
            return None
        if self.blur_strength > 0:
            blurred = candidate.with_name(f"{candidate.stem}_blur.jpg")
            if blurred.is_file():
                return blurred
        return candidate
