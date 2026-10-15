# 验收清单（逐条对应 SPEC §4）

> 用法：每个里程碑收尾时更新本表；**只有能复现的条目才可勾选**。
> 人工项（需真人操作）标 👤。自动化项标 🤖（对应测试/命令）。
> 图例：`[x]` 通过 / `[ ]` 未验证 / `[~]` 部分通过（附说明）

## AC-1 登录（M1）
- [x] AC-1.1 🤖 二维码内容 = `https://music.163.com/login?codekey=<unikey>`，zxing-cpp 可反解 → `tests/test_qr.py`（4 项：登录 URL / 64 位 key / 中文载荷 / PNG 有效性）
- [x] AC-1.2 🤖+👤 状态机已按 800/801/802/803 实现；801 探测通过，**803 已真机扫码确认**（uid=100000001 登录成功，见下方"M2 记录"）
- [x] AC-1.3 🤖+👤 cookie 落盘 600、跨进程复用（复用成功 uid=100000001）、失效清理 → `tests/test_session.py::TestCookiePersistence` + `tests/test_session_state.py`
- [x] AC-1.4 🤖 网络异常不崩溃、错误可读 → `tests/test_session.py::TestSessionParsing`（超时/空响应/非 JSON/拼接 JSON/错误码中文）

## AC-2 搜索（M2）
- [~] AC-2.1 单曲 tab 列表字段完整（#/歌曲/歌手/专辑/时长/♥）→ `tests/test_ui_smoke.py::TestSearchViewSmoke`；👤 观感待确认
- [~] AC-2.2 👤 双击播放已接到 `controller.play_songs()`；**听声待你在 GUI 里确认**
- [~] AC-2.3 收藏 / 取消收藏双向（`controller.set_like()` + 行级 ♥ 同步）→ `tests/test_ui_smoke.py`；👤 真机点一次
- [x] AC-2.4 歌手/专辑/歌单双击 → 进详情页列出曲目、播第一首（`ui/entity_view.py` + `main_window._open_entity`，歌单按 trackIds 顺序补全）→ `tests/test_ui_smoke.py::TestTrackTableAndEntityView`
- [x] AC-2.5 四 tab 滚动加载更多、按 id 去重、"没有更多了" → `tests/test_search_state.py`（分页/去重/到底不再请求）
- [x] AC-2.6 搜索历史（≤50/去重/单删/清空/持久化/损坏容错）→ `tests/test_search_history.py`（11 项）
- [x] AC-2.7 旧请求丢弃、loading 提示 → `tests/test_search_state.py::TestStaleResults` + 页脚加载中提示

## AC-3 我喜欢的音乐（M4）
- [x] AC-3.1 全量加载（真机 589 首，1.3s）→ `tests/test_library.py`；该接口忽略 `s` 偏移，只请求一次
- [~] AC-3.2 双击点播 + 顺序继续 → 已接线，👤 真机确认
- [~] AC-3.3 播放全部 → `tests/test_ui_smoke.py::test_play_all_emits_request`，👤 真机确认
- [x] AC-3.4 当前行高亮跟随 → `tests/test_ui_smoke.py::test_highlight_current_row`
- [x] AC-3.5 失败降级到缓存 / 明确报错（缓存含完整元数据，断网可用）→ `tests/test_library.py::TestLibraryCache`

## AC-4 播放控制与歌词（M4/M5）
- [x] AC-4.1 上一首/播放暂停/下一首行为正确 → `tests/test_queue.py` + `tests/e2e_playback.py` 第 5 步
- [ ] AC-4.2 进度条推进 + 拖动 seek + 时间显示（拖动已接 `player.seek`，👤 待确认）
- [ ] AC-4.2 进度条推进 + 拖动 seek + 时间显示
- [ ] AC-4.3 音量/静音 + 重启记忆
- [x] AC-4.4 点封面进歌词页、返回不断播 → `player_bar.cover_clicked/lyrics_clicked` → `main_window._open_lyrics()`；`← 返回` 回到 `config.last_page`；`tests/test_ui_smoke.py::TestLyricsView`
- [x] AC-4.5 歌词居中滚动 + 手动滚动 5s 恢复 → `LyricsView._center_on()`（500ms OutCubic 缓动）+ 事件过滤器监听滚轮/点击/拖动 → `_on_user_scroll` 暂停跟随 5s → `tests/test_ui_smoke.py::test_manual_scroll_pauses_follow`
- [x] AC-4.6 行级高亮 + 翻译副行 + 无歌词提示 → `model/lrc.py`（认 `[mm:ss.xx]` 与 `[mm:ss:xx]`、一行多时间戳、翻译按时间戳合并、纯音乐/暂无歌词当无歌词）+ `LyricIndex` + `LyricsView` QSS `[current="true"]`；`tests/test_lrc.py` 21 项
- [x] AC-4.7 四模式切换生效 + 重启记忆 → `player_bar.mode_changed` → `controller.set_mode()` + `config.save()`（`play_mode` 落盘，`Config.load` 校验合法值）；`tests/test_queue.py` 覆盖四模式
- [x] AC-4.8 URL 过期自愈（`code:404`/取流失败自动重取一次，仍失败自动跳下一首）→ `controller._on_ended` + 真机 404 样本验证

## AC-5 缓存与清理（M4/M6）
- [x] AC-5.1 播完落盘且文件有效（位置到底即提交，尾包丢失 ±5% 兜底）→ `tests/test_playback_cache.py` + `tests/e2e_playback.py` 第 3 步
- [x] AC-5.2 中途切歌不留半首歌（位置没到底 → 丢弃 + 竞态孤儿兜底清扫）→ `tests/test_playback_cache.py::test_stop_midway_discards` + `TestPartSweep` + e2e 第 2 步
- [~] AC-5.3 二次播放走本地文件 → `tests/e2e_playback.py` 第 4 步（依赖 5.1，本轮复测中）
- [x] AC-5.4 歌单/歌词缓存生效
- [x] AC-5.5 设置页显示四类缓存占用与文件数 + 逐类清空 + 清空全部 + 打开缓存目录 → `ui/settings_view.py`（统计/清理均在子线程）；真机探针：audio 10.8 MB/1 个、playlist 167.6 KB/1、lyric 8.1 KB/3、cover 868.5 KB/3，与 `du` 一致；清歌词后数字刷新为 0；`tests/test_settings_view.py` 11 项
- [x] AC-5.6 超上限 LRU 淘汰 + 设置页上限可调（调小立即淘汰）→ `CacheManager.trim_audio()` 按 mtime 最旧优先；`SettingsView` 滑块 50 MB~8 GB，`valueChanged` 落盘并立即淘汰；`tests/test_settings_view.py::test_slider_saves_limit_and_trims`、`test_trim_removes_oldest_first`

## AC-6 工程（M1~M6）
- [x] AC-6.1 🤖 `./run.sh` 一键起（建 venv + 阿里云镜像 + 依赖校验 + 启动）→ 实测
- [x] AC-6.2 🤖 `scripts/selftest.sh` 离线全绿（**337 项**）
- [x] AC-6.3 退出无 mpv 残留 → `tests/e2e_playback.py` 第 6 步（0 个 socket）+ `tests/test_mpv_ipc.py`
- [x] AC-6.4 🤖 未捕获异常写入日志 → `app.install_excepthook`
- [x] AC-6.5 🤖 AGENTS.md 文件地图一致性（56 条路径）→ `scripts/check_agents_map.py`

---

## 探测记录（只读，可重复执行）

### 2026-10-14 · M1 收尾
命令：
```bash
scripts/selftest.sh                    # 64 项离线测试 + 地图一致性 → 全绿
NET=1 NET_PLAY=1 scripts/selftest.sh   # 追加联网只读探测 → 14/14 通过
```

联网探测输出要点：
| 项目 | 结果 |
|---|---|
| AES 向量自检 | 通过（FIPS-197 + NIST + openssl 交叉验证） |
| eapi 报文构造 | 192 hex 字符（6 个 16 字节分组） |
| 二维码 unikey | `code:200`，扫码链接 `https://music.163.com/login?codekey=…` |
| 轮询状态 | `code=801`（等待扫描）✅ **回归 bug 已修**（参见下方"发现的问题"） |
| 搜索 单曲/歌手/专辑/歌单 | `total=273 / 82 / 592 / 464`，各返回 5 条 |
| 歌手热门 | 50 首 |
| 专辑曲目 | 《神的游戏》9 首 |
| 歌单 trackIds | 热歌榜 200 条（首批返回 3 条曲目） |
| 歌词 | `lrc` 有、`tlyric` 该曲无翻译 |
| 取流 | **`exhigh` 320kbps mp3**（size 12,764,517 B）——未登录也可拿到 320k |
| mpv 播放 + 边播边存 | 12,764,641 B 落盘，退出码 0（≈API size +124 B） |

### 2026-10-14 · 外观/背景 + 五个界面问题修复（用户反馈）
用户一次提了 5 个问题，全部修完并有回归测试：

| # | 问题 | 根因 | 修法 |
|---|---|---|---|
| 1 | 搜索页展开历史下拉后，点左导航没反应 | 历史下拉用 `Qt.Popup`，那个标志是**模态**的，会吞掉点导航的那一下；且左导航是 `NoFocus`，失焦也不会收下拉 | 改成非模态工具窗 `Qt.Tool` + `Qt.FramelessWindowHint` + 应用级事件过滤「点在别处就关」；过滤器只在本页可见时挂着（`showEvent`/`hideEvent`），否则进程内越积越慢 |
| 2 | 已登录时点「登录」还是显示扫码页 | 启动时的登录态是**异步**恢复的，`LoginView` 构造完就固定渲染成扫码态，之后再不刷新 | `LoginView` 监听 `account_changed`/`login_changed`，`refresh()` 兜底 |
| 3 | 未全屏时有横向滚动条，深色下几乎看不见 | 歌手/专辑列用 `ResizeToContents`，内容多宽就多宽**永不收缩**，六列最小宽之和超过视口就必然横向滚 | 两列改 `Interactive`，由 `_fit_columns()` 按视口统一分配（数据变化/resize 都重算）；顺带**不再隐藏**横向滚动条（`height:0` 会把溢出藏起来） |
| 4 | 设置页显示"数字在子线程里统计，不卡界面" | 实现细节不该暴露给用户 | 删掉 |
| 5 | 要能设置应用背景图片 | — | 新增左导航第 5 项「外观」：主题切换 + 5 种背景预设 + 上传/删除自定义图，全部带预览；**按图片平均亮度自动选浅色/深色**（深照片配浅主题会把图糊住） |

附带修掉的两个真 bug：
- **表格文字在白底上看不清**：item 的 foreground 写死了深色主题的十六进制。改为从
  `palette().color(QPalette.Text)` 现取（实测 QSS 的 `color:` 会同步到 palette，但**不会**
  同步到已创建的 item），次要列用 `setAlphaF(0.62)` 弱一档；切主题时 `refresh_colors()` 重染。
- **测试会写进用户真实的背景图目录**：`redirect_config()` 漏了 `BACKGROUND_DIR`。

| # | 问题 | 根因 | 修法 |
|---|---|---|---|
| 6 | 换预设/传图片"完全没反应"，只有浅色深色在变 | ① `central` 和 7 个页面根控件都是没起名的 QWidget，被 `QWidget { background }` 命中，各画一层不透明底板把 QMainWindow 盖死；② `backdrop_css_for()` 对预设只返回 `qlineargradient(...)`，**漏了 `background:` 属性名**，整条声明被 QSS 解析器丢掉 | ① 给 `central` 和每个页面设对象名并显式透明；② 补属性名；③ 删掉 `QWidget` 兜底 background 规则（连带给 `QHeaderView`/`QAbstractItemView`/`QScrollArea`/`QScrollBar` 补显式底色，否则回落系统调色板） |
| 7 | 表格表头在深色下是一条白带；右缘一条纯黑竖条 | 同上：删掉兜底规则后这些控件回落系统调色板；滚动条把手用 `rgba()` 会渲染成纯黑，凹槽写 `transparent` 时 Qt 不画也回落成黑 | 表头/视图显式透明；滑块和滚动条全部改实色 |
| 8 | 上传的花照片当背景，界面文字没法读 | 照片里有亮得发白的区块，深色/浅色文字压上去都看不见 | 导入时生成**模糊版**（缩到 1/N 再放大回去，纯 Qt 无第三方依赖）+ 按图片平均亮度朝主题基色压一档 + 自定义图用更浓的纱罩；**模糊度做成滑块可调**（0=原图，100=最糊） |
| 9 | 模糊功能上线前传的图没有模糊版，且改了模糊度不生效 | 懒生成漏了；`_ensure_blur` 传 `str` 给要 `Path` 的函数，异常被 `except: pass` 吞掉 | 懒生成 + 改档重出；`_blurred_path` 统一转 `Path`，异常改成记日志不再静默 |

| # | 问题 | 根因 | 修法 |
|---|---|---|---|
| 10 | 历史下拉位置飘到屏幕左上角/页面中部，不在搜索框下面 | 下拉用的是 `Qt.Tool` **顶层窗口**，靠 `mapToGlobal()` 定位；而 **Wayland 下客户端拿不到窗口真实屏幕位置**，`mapToGlobal()` 返回的是"以窗口为原点"的坐标，顶层窗就被放到屏幕左上角 | 改成**页面内的普通子控件** + `raise_()`，坐标用页面内局部坐标，与窗口管理器无关 |
| 11 | 历史列了全部、还带滚动条 | 没有条数上限，高度按内容算但策略允许滚动 | 只列最近 5 条（`HISTORY_VISIBLE`），高度正好等于 5 行内容，并显式关掉横竖滚动条策略 |
| 12 | 搜索历史被探针脚本灌进测试词 | 探针没走 `tests/support.py::redirect_config()` 隔离，直接写进真实配置目录 | 探针一律先隔离；已污染的数据不主动清（等用户决定） |

### 2026-10-14 · M5/M6 收尾（歌词页 + 播放条封面 + 设置页）
- AC-4.4/4.5/4.6（歌词页）、AC-5.5/5.6（设置页缓存）勾选；离线 **296 项**全绿。
- 真机探针：封面 14 KB 落盘并显示在播放条与歌词页；61 行歌词按位置正确高亮（25s→行 0、80s→行 17）；
  日文歌《打上花火》翻译副行合并正确；设置页统计与 `du` 一致；登出链路无报错。
- 用户明确约束：**只有主动点退出登录才清用户数据**，cookie 失效/重启恢复失败一律不清 →
  `SessionState.logged_out` 专用信号，`tests/test_session_state.py::TestLogoutClearsDataOnlyWhenExplicit`
  + `tests/test_ui_smoke.py::TestLogoutPolicy` 两条回归钉住。

### 2026-10-14 · M4 收尾：真机登录后「我喜欢的音乐」白屏
命令：`PYTHONPATH=src .venv/bin/python -u /tmp/diag_lib2.py`（复现 + 验证）

| # | 问题 | 现象 | 根因 | 修法 |
|---|---|---|---|---|
| 11 | **`/api/v3/song/detail` 的 `c` 参数写错** | 「我喜欢的音乐」永远加载不出歌，界面停在"正在加载" | 传 `c="[]"` 时服务端**不报错**，只返回 `{"songs": [], "code": 200}`；必须传和 `ids` 同一份 JSON | `api.song_detail()` 里 `c = ids`；回归 `tests/test_api_params.py::test_song_detail_c_must_equal_ids` |
| 12 | **歌单接口忽略 `s` 偏移，我却按 offset 分页** | 加载要 13 秒、发 30+ 个请求 | `playlist_detail(n=500, s=500)` 返回的仍是全量 589 条 → 我的循环 `len(ids) >= page` 永远为真，一路空转到 offset>5000 | 改成一次拉全 + 去重保序；回归 `tests/test_library.py::test_requests_playlist_once` |
| 13 | 静默空结果没有兜底提示 | 请求"成功"但零结果，用户只看到无限 loading | 上层只信 `code==200` | `_fetch_all` 在 `ordered` 为空时返回明确错误（`该歌单没有可播放的歌曲`），`PlaylistView` 显示失败原因 |
| 14 | **切歌竞态留下孤儿临时文件** | 连点几首歌后缓存目录残留一堆完整大小的 `.mp3`（真机 100MB） | 切歌后 22ms 内又切下一首：`discard` 时 mpv 还没建出临时文件（`part.exists()` 为 False → 静默跳过），之后 mpv 才把文件建出来，无人认领 | `CacheManager.sweep_parts(keep=...)` 只保留当前录制文件；控制器每次收尾 + 每 10s 兜底清扫；回归 `tests/test_cache.py::test_sweep_parts_keeps_current_recording`、`tests/test_playback_cache.py::TestPartSweep` |
| 18 | 设置页账号一直显示"未登录" | 真机探针发现 | 账号接线的两行被误缩进到 `_on_cache_limit_changed` 里，永远不执行 | 修回 `_wire_settings()`，并加回归测试 `test_settings_view_follows_account_changes` |
| 19 | 登出时崩在 `controller.stop()` / `queue.clear()` / `_ResultTable.set_songs()` 不存在 | 真机探针登出链路报 AttributeError | 这三个方法是新写的，名字和既有 API 不一致 | 控制器加 `stop()`、队列加 `clear()`、搜索表格加 `clear_rows()`；补 `test_logout_stops_playback_and_clears_queue` |
| 20 | 封面 token 测试偶发失败 | CI 连跑时 `[111,222]` 顺序偶发颠倒 | 两个 AsyncTask 在线程池并发，完成顺序本就不保证；断言写死了顺序 | 改成集合断言 |
| 17 | 播放条封面位置是空的 | 用户反馈"下方没有歌曲图片" | `set_song()` 只设文字，封面按钮一直是占位图标，且没有加载封面的代码 | 新增 `state/cover_loader.py`（磁盘缓存 + 子线程下载 + token 防串台）+ `player_bar.set_cover()`；切歌时先回落占位图避免闪上一首封面 |
| 16 | **进详情页被弹回登录页** | 双击专辑/歌单后界面跳到登录页（导航第一行被选中） | `show_page()` 用 `next(..., 0)` 找导航行号，详情页不在导航里 → 默认跳第 0 行（"登录"）→ `currentRowChanged` 又把页面切回登录页 | 非导航页只取消选中（`setCurrentRow(-1)`），不默认跳第 0 行；回归 `tests/test_ui_smoke.py::TestPageSwitching` |
| 15 | 专辑/歌单看不到具体曲目 | 搜索结果里双击专辑/歌单只默默开始播放，用户看不到曲目 | 只有"载入为队列"，没有可查看的列表 | 新增 `ui/entity_view.py` 详情页（返回 + 播放全部 + 双击点播 + ♥ + 当前行高亮），曲目表格抽成 `ui/widgets/track_table.py` 与「我喜欢的音乐」共用 |

修复后实测：589 首 / 1.3 秒，缓存文件落盘。

### 发现并修复的问题（探测的价值所在）
1. **二维码轮询把 `801` 当错误抛出**：`/api/login/qrcode/client/login` 用 800/801/802/803 表示**状态**，
   而会话层默认只认 200 → 登录页永远拿不到"等待扫描"。
   修法：`Session.request(..., ok_codes={800,801,802,803})`，并补两条回归测试
   （`tests/test_session.py::test_qr_status_codes_*`）+ 断言 `qr_poll` 确实传了 `ok_codes`
   （`tests/test_api_params.py`）。这也说明：**假 session 的单测发现不了这类问题，联网探测必须常跑**。

### 环境备忘
- uv 默认会挑自己管理的 Python（3.13），那样 `--system-site-packages` 看不到系统 PySide6；
  `scripts/setup.sh` 已强制选择"能 import PySide6 的 python"（本机 `/usr/bin/python3` 3.14.7）。

---

### 2026-10-14 · M2 收尾（播放内核 + 搜索页）发现并修复的问题
命令：
```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests   # 184 项全绿
python tests/e2e_playback.py                                   # 联网 + 真 mpv，约 4.5 分钟
```

| # | 问题 | 现象 | 根因 | 修法 |
|---|---|---|---|---|
| 1 | **`requests` 在有 `Cookie` 请求头时不注入 jar** | `/api/nuser/account/get` 返回 `profile:null`（看似"未登录"） | requests 的 `merge_cookies`：请求头已带 `Cookie` 时不再合并 jar | `Session.cookie_header()` 自己合成 Cookie 头；回归 `tests/test_session.py::test_cookie_header_merges_login_cookie` |
| 2 | **AsyncTask 信号丢失** | 任务跑完但回调永不触发（播放链路整条卡死） | 任务对象（信号接收者）在 `run()` 返回后被回收，排队事件被丢弃 | `_KEEPALIVE` 强引用 + emit 之后才排队 `_retire`；回调一律连绑定方法；`tests/test_async_task.py` |
| 3 | **mpv `--stream-record` 扩展名决定封装** | `.part` 后缀录出 0 字节文件 | mpv 按后缀猜封装格式，`part` 不认识 → 静默不写 | 临时文件改成**隐藏文件 + 正确扩展名**（`.800000001_exhigh.mp3`）；回归 `tests/test_cache.py::test_part_path_has_valid_extension` |
| 4 | **`pathlib.glob("*")` 会匹配隐藏文件** | 录制中的半成品被算进缓存统计/LRU | pathlib 与 `glob.glob` 行为不同 | `_audio_files()` 手动排除 `.` 前缀；`tests/test_cache.py::test_stats_ignores_part_files` |
| 5 | **`audio_lookup()` 把临时文件当缓存命中** | 第二次播放 `source=cache-part`、播放半成品、永远不再录制 | `glob(f"{id}_*")` 命中了 `.{id}_exhigh.mp3` | `audio_lookup()` 跳过 `.` 前缀；回归 `tests/test_cache.py::test_lookup_ignores_part_files` |
| 6 | **缓存提交判据错** | 整首播完却没落盘（实测录到 8,912,896 B vs API 9,081,645 B，差 1.86%） | ① 用 mpv 的 `end-file` reason 判完整性，而网络流自然播完时 mpv 也可能报 `error`；② ±1% 字节容差挡掉尾包丢失 | 改成**位置判据**：`time-pos ≥ duration-5s` 才算"听完整首"，此时容差放宽到 ±5%；`tests/test_playback_cache.py`（9 项） |
| 7 | **error 时重头重播** | 尾包丢失后用户已听完，客户端却从头再播一遍 | 同上：把"播完报的 error"当成"播放出错" | `_on_ended`：位置已到底的 error 按 eof 处理（继续下一首） |
| 8 | **`SessionState.restore()` 在子线程发信号** | 退出时报 `RuntimeError: Signal source has been deleted` | 违反 SPEC §4 规则 1（跨线程只走 Qt 信号） | 拆成 `fetch_account()`（子线程只请求）+ `apply_fetched()`（主线程落地并发信号）；`tests/test_session_state.py`（10 项） |
| 9 | **换关键词只重置当前 tab** | 切关键词后旧 tab 还留着上一个词的结果 | `search()` 只 reset 了当前 tab | 关键词变化时重置全部 tab；`tests/test_search_state.py::test_tabs_are_isolated` |
| 10 | **pkill -f 自杀** | 想杀测试进程把自己 shell 杀了 | 模式匹配到自身命令行 | 用 `ps -eo pid,args | awk '/pat[t]ern/'` 括号技巧或专用脚本 |

端到端实测（`docs/e2e-report.md`）：能播放且进度推进 ✅ / 切歌清理半成品 ✅ / 上一首下一首 ✅ / 退出无 socket 残留 ✅；
播完落盘与二次命中缓存在第 6 项修完后复测。
