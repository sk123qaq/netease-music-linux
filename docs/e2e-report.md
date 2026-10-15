# 端到端播放验证报告

- 时间：2026-10-14 17:36:45
- 输出：--ao=null（静音）
- 结果：**4/6 通过**

| 检查项 | 结果 | 说明 |
|---|---|---|
| 能播放且进度推进 | ✅ | 位置 0.5s |
| 切歌后临时文件被清理 | ✅ | 残留 0 个旧临时文件 |
| 播完后写入缓存文件 | ❌ | 网络截断，已按设计丢弃（.800000001_exhigh.mp3（实际 8912896 字节，期望 ~9081645））——非逻辑问题，见 acceptance.md |
| 第二次播放为本地文件直读 | ❌ | 上一步没有缓存文件，跳过 |
| 下一首/上一首切歌正确 | ✅ | 800000002 → 800000004 → 800000002 |
| 退出后无 socket 残留 | ✅ | 0 个 |

## 播放源变化（音质/缓存来源）

```
800000001 level=exhigh br=320000 source=cache-part
800000003 level=exhigh br=320000 source=cache-part
800000001 level=exhigh br=320000 source=cache-part
800000001 level=exhigh br=320000 source=cache-part
800000002 level=exhigh br=320000 source=cache-part
```

## 缓存事件

```
song 800000001: discarded
song 800000003: discarded
song 800000001: discarded
song 800000001: discarded
song 800000002: discarded
```

## 事件流

```
开始播放：山河图
开始播放：寄往未来的信
开始播放：山河图
开始播放：山河图
开始播放：幕后者
开始播放：只为遇见
开始播放：幕后者
```
