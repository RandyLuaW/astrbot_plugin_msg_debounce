<div align="center">

# 连发三句，只回一次

<img src="https://count.getloli.com/@astrbot_plugin_msg_debounce?name=astrbot_plugin_msg_debounce&theme=rule34&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto" alt="count" />

**AstrBot 消息防抖插件：把同一个人短时间连发的多条消息合并成一条再处理，避免连发三句触发三轮 LLM 回复。**

![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17-5865f2?style=flat-square)
![Version](https://img.shields.io/badge/version-1.0.0-22c55e?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square)
![Platform](https://img.shields.io/badge/platform-aiocqhttp-f97316?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square)

简体中文 | [English](README_EN.md)

<img src="logo.png" alt="消息防抖 Logo" width="180">

</div>

> 📦 **规范性说明**：本仓库是一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) **插件组件仓库**，仅作为 AstrBot 的插件使用，不能独立运行。请将本插件安装至 AstrBot 的 `data/plugins/` 目录，由 AstrBot 框架加载并调度。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [功能一览](#功能一览)
- [安装](#安装)
- [配置项](#配置项)
- [行为对照表](#行为对照表)
- [工作机制](#工作机制)
- [调参建议](#调参建议)
- [注意事项](#注意事项)
- [与其它插件的关系](#与其它插件的关系)
- [测试](#测试)
- [版本历史](#版本历史)

---

## 它解决什么问题

群聊里常见这样的节奏：

```
小明：在吗
小明：问你个事
小明：那个插件怎么配
```

没有防抖时，这三条会**分别走一次完整 pipeline**。模型要回三次，前两次往往是「在的」「你说」这类无意义内容，既浪费 token，也让对话节奏变得很碎。

开启本插件后，三条会在 2 秒窗口内合并成一条：

```
在吗
问你个事
那个插件怎么配
```

只触发一次回复，模型能看到完整意图，一次答完。

---

## 功能一览

| 能力 | 说明 |
|------|------|
| 连发合并 | 同一个人在窗口内的多条消息合并成一条，只触发一轮 LLM |
| 不重复触发 | 批次 token 机制保证同一批次只有一个事件继续往下走，其余主动让位 |
| 条数上限 | 达到上限立即发出，长串消息不会被无限延迟 |
| @ 豁免 | 被点名时立即处理，不用等窗口 |
| 指令豁免 | `/` 开头的指令立即处理 |
| GIF 豁免 | 含 GIF 的消息不合并（GIF 有时效性，合并容易丢帧） |
| 范围可选 | 只合并群聊，或群聊 + 私聊都合并 |
| 非文本保留 | 图片、@、表情等消息段原样拼接，不只是拼文字 |

---

## 安装

### 方式一：插件市场（推荐）

AstrBot WebUI → 插件市场 → 搜索「消息防抖」→ 安装。

### 方式二：Releases 下载

到 [Releases](https://github.com/RandyLuaW/astrbot_plugin_msg_debounce/releases) 下载最新的 `astrbot_plugin_msg_debounce-v1.0.0.zip`，在 WebUI 插件页「上传安装」。

### 方式三：手动克隆

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_msg_debounce.git
```

安装后在插件管理页启用即可，配置文件会自动生成。

---

## 配置项

| 项 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `enabled` | bool | `true` | 总开关 |
| `listen_seconds` | float | `2.0` | 等待窗口（秒）。收到消息后最多等这么久，期间同一个人再发消息就合并；设为 `0` 等于关闭 |
| `max_merge_count` | int | `3` | 最多合并几条。达到上限后立即发出，不再继续等；设为 `0` 表示不限制 |
| `scope` | string | `group` | `group` 只合并群聊消息，`all` 群聊和私聊都合并 |
| `exempt_at` | bool | `true` | 被 @ 时不等待，立即处理 |
| `exempt_command` | bool | `true` | 指令消息不等待，立即处理 |
| `command_prefixes` | list | `["/"]` | 指令前缀列表，用于判断是否豁免 |
| `exempt_gif` | bool | `true` | 消息里带 GIF 时不合并 |
| `debug` | bool | `false` | 输出单条放行的调试日志 |

---

## 行为对照表

| 场景 | 行为 |
|------|------|
| 单条消息 | 等满窗口后正常处理，内容不变 |
| 窗口内又来一条 | 后到的消息接管，先到的事件主动 `stop_event()` 让位，不会重复触发 LLM |
| 达到条数上限 | 立即带全部内容发出，不再继续等 |
| @ 机器人 | 默认豁免，立即处理 |
| 指令（`/` 开头） | 默认豁免，立即处理 |
| 含 GIF | 默认豁免 |
| 私聊 | `scope=group` 时不参与防抖 |
| 机器人自己发的消息 | 直接跳过 |
| 不同用户同时发言 | 各算各的批次，互不干扰 |

---

## 工作机制

整个插件只注册**一个**消息钩子（`priority=9999`），不注册 LLM 工具、不改回复格式、不碰 AstrBot 内部 runner。

核心是**批次 token**：

```python
# 第一条消息：建立批次，拿到 token_A
batch = _Batch(token=object(), chain=chain, count=1)
my_token = batch.token

await asyncio.sleep(listen_seconds)   # 睡窗口

# 醒来后检查：批次还在吗？token 还是我的吗？
current = self._batches.get(key)
if current is not batch or current.token is not my_token:
    event.stop_event()                # 已有人接管，本事件让位
    return

self._batches.pop(key, None)
self._emit(event, batch)              # 只有最后一条走到这里
```

窗口内每来一条新消息，都会：

1. 把消息链和文本并入已有批次；
2. 换一次 `token = object()`，使先前所有事件的 token 全部失效；
3. 自己带着最新 token 进入睡眠。

于是**先到的事件醒来发现 token 已变，主动让位；只有最后一条消息负责处理**，且它手里的批次已经包含全部内容。

达到 `max_merge_count` 时不进睡眠，直接 `_emit` 带全部内容发出，避免长串消息被无限延迟。

`_emit` 里改写三个字段，让后续 stage 看到的就是合并后的消息：

```python
event.message_obj.message = list(batch.chain)
event.message_obj.message_str = batch.plain
event.message_str = batch.plain
```

---

## 调参建议

| 场景 | 建议 |
|------|------|
| 一般群聊 | 默认 `2.0s` / `3` 条，够用 |
| 追求响应速度 | `listen_seconds: 1.0`~`1.5`，代价是合并成功率下降 |
| 用户打字慢、喜欢分句 | `listen_seconds: 2.5`~`3.0`，`max_merge_count: 5` |
| 群里有刷屏习惯 | 把 `max_merge_count` 调到 `3`，防止一条回复吞掉十几句 |
| 只想要群聊效果 | 保持 `scope: group` |
| 私聊也想合并 | `scope: all` |

---

## 注意事项

- **固定延迟是固有成本**：窗口内消息必然要等 `listen_seconds`。这是「真防抖」无法避免的代价，不是 bug。
- **热重载会丢批次**：插件重载时 `terminate()` 会清空未完成的批次，正在等待的消息随旧实例一起丢弃。重载后第一条消息可能不回复，属预期行为。
- **`max_merge_count` 最小有效值为 2**：填 1 会被自动修正为 2（合并没有意义）。
- **不改变回复内容**：本插件只调整「几条消息算一轮」，不负责改写、清洗或格式化回复。回复形态请交给 outputpro 之类的输出管道插件。
- **合并文本用换行分隔**：模型看到的是多行文本，语义上仍能区分原句。

---

## 与其它插件的关系

`priority=9999` 是刻意选的：

| 插件 | 优先级 | 与本插件的关系 |
|------|--------|----------------|
| tattle（发言审查） | 10000 | 更高。被封禁用户的消息先被拦掉，不会进入合并批次 |
| **本插件** | **9999** | 在消息入口处合并 |
| active_reply_ctl（主动回复） | 0 | 更低。它只看到合并后的那一条，不会因为连发而多次判定主动回复 |
| iris（聊天记忆） | 0 | 更低。记忆里记录的是合并后的完整内容，不会把一句话拆成三条 |

如果你装了别的消息入口插件并遇到「消息被吞」或「回复两次」，优先检查优先级顺序。

---

## 测试

仓库自带离线单测，**不需要 AstrBot 运行环境**（内置了 `astrbot` 模块的最小 mock）：

```bash
python tests/test_debounce.py
```

覆盖用例：

- 单条消息：放行、内容不变、批次已清理
- 两条连续：先到的让位，后到的携带全部内容
- 达到上限：立即带全部内容发出，不再等待
- @ 机器人：立即放行，不等待
- 指令消息：立即放行，不等待
- 私聊：`group` 模式下不参与防抖
- 不同用户：批次互不干扰
- 关闭时：直接放行

---

## 版本历史

### v1.0.0

首个开源版本。连发合并、批次 token、条数上限、@ / 指令 / GIF 豁免、范围可选。

完整记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件框架
- [NapCat](https://github.com/NapNeko/NapCatQQ) —— QQ 协议端

---

## 许可证

[MIT](LICENSE)
