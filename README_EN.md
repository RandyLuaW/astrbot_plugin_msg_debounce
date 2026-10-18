<div align="center">

# Three messages in a row, one reply

<img src="https://count.getloli.com/@astrbot_plugin_msg_debounce?name=astrbot_plugin_msg_debounce&theme=rule34&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto" alt="count" />

**AstrBot message debounce plugin: merges a burst of messages from the same user into a single message before processing, so three consecutive lines trigger one LLM round instead of three.**

![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17-5865f2?style=flat-square)
![Version](https://img.shields.io/badge/version-1.0.0-22c55e?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square)
![Platform](https://img.shields.io/badge/platform-aiocqhttp-f97316?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square)

[简体中文](README.md) | English

<img src="logo.png" alt="Message Debounce Logo" width="180">

</div>

> 📦 **Note**: This repository is an [AstrBot](https://github.com/AstrBotDevs/AstrBot) **plugin component**. It cannot run standalone. Install it into AstrBot's `data/plugins/` directory and let the framework load it.

---

## Table of Contents

- [The Problem](#the-problem)
- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Behavior Matrix](#behavior-matrix)
- [How It Works](#how-it-works)
- [Tuning Tips](#tuning-tips)
- [Caveats](#caveats)
- [Interaction With Other Plugins](#interaction-with-other-plugins)
- [Testing](#testing)
- [Changelog](#changelog)

---

## The Problem

A very common rhythm in group chats:

```
Alice: you there?
Alice: got a question
Alice: how do I configure that plugin
```

Without debouncing, these three messages each run through the full pipeline. The model replies three times — the first two are usually filler like "yeah?" and "go ahead". That wastes tokens and chops the conversation into fragments.

With this plugin, all three are merged within a 2-second window:

```
you there?
got a question
how do I configure that plugin
```

One reply, full intent visible, answered in one pass.

---

## Features

| Feature | Description |
|---------|-------------|
| Burst merging | Multiple messages from the same user within the window become one, triggering a single LLM round |
| No duplicate triggers | A batch token guarantees only one event proceeds; the rest yield |
| Count cap | Emits immediately when the cap is reached, so long bursts never wait indefinitely |
| @-mention exempt | Direct mentions are processed instantly, no window delay |
| Command exempt | Messages starting with `/` are processed instantly |
| GIF exempt | Messages containing GIFs are skipped (GIFs are time-sensitive) |
| Scope option | Merge group chats only, or group + private chats |
| Non-text preserved | Images, mentions and emoji segments are concatenated, not just plain text |

---

## Installation

### Option 1: Plugin marketplace (recommended)

AstrBot WebUI → Plugin Marketplace → search "消息防抖" → Install.

### Option 2: Releases

Download the latest `astrbot_plugin_msg_debounce-v1.0.0.zip` from [Releases](https://github.com/RandyLuaW/astrbot_plugin_msg_debounce/releases) and use "Upload & Install" in the WebUI plugin page.

### Option 3: Manual clone

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_msg_debounce.git
```

Enable it on the plugin management page afterwards. The config file is generated automatically.

---

## Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Master switch |
| `listen_seconds` | float | `2.0` | Debounce window in seconds. Set to `0` to disable |
| `max_merge_count` | int | `3` | Max messages to merge before emitting immediately. `0` means unlimited |
| `scope` | string | `group` | `group` for group chats only, `all` for both group and private |
| `exempt_at` | bool | `true` | Process @-mentions immediately |
| `exempt_command` | bool | `true` | Process commands immediately |
| `command_prefixes` | list | `["/"]` | Prefixes used to detect commands |
| `exempt_gif` | bool | `true` | Skip messages containing GIFs |
| `debug` | bool | `false` | Log single-message pass-through |

---

## Behavior Matrix

| Scenario | Behavior |
|----------|----------|
| Single message | Processed normally after the window, content unchanged |
| Another message arrives | The newer message takes over; earlier events `stop_event()` and yield |
| Cap reached | Emits immediately with all content, no further waiting |
| @-mention | Exempt by default, instant |
| Command (`/`) | Exempt by default, instant |
| Contains GIF | Exempt by default |
| Private chat | Skipped when `scope=group` |
| Bot's own messages | Skipped |
| Different users talking at once | Independent batches, no interference |

---

## How It Works

The plugin registers exactly **one** message hook (`priority=9999`). No LLM tools, no reply formatting, no touching AstrBot internals.

The core is a **batch token**:

```python
# First message: create the batch, hold token_A
batch = _Batch(token=object(), chain=chain, count=1)
my_token = batch.token

await asyncio.sleep(listen_seconds)   # sleep through the window

# On wake: is the batch still there? Is the token still mine?
current = self._batches.get(key)
if current is not batch or current.token is not my_token:
    event.stop_event()                # someone took over, yield
    return

self._batches.pop(key, None)
self._emit(event, batch)              # only the last message gets here
```

Each new message inside the window will:

1. Merge its chain and text into the existing batch;
2. Rotate `token = object()`, invalidating every earlier event's token;
3. Sleep while holding the newest token.

So **earlier events wake up, find their token stale, and yield; only the last message proceeds** — and its batch already carries everything.

When `max_merge_count` is reached, it skips the sleep and `_emit`s immediately with all content.

`_emit` rewrites three fields so downstream stages see the merged message:

```python
event.message_obj.message = list(batch.chain)
event.message_obj.message_str = batch.plain
event.message_str = batch.plain
```

---

## Tuning Tips

| Scenario | Suggestion |
|----------|------------|
| Typical group chat | Defaults (`2.0s` / `3`) work fine |
| Want faster response | `listen_seconds: 1.0`~`1.5`, at the cost of fewer merges |
| Users type slowly, in fragments | `listen_seconds: 2.5`~`3.0`, `max_merge_count: 5` |
| Chatty groups | Keep `max_merge_count: 3` so one reply never swallows a dozen lines |
| Group only | Keep `scope: group` |
| Merge private chats too | `scope: all` |

---

## Caveats

- **Fixed latency is inherent**: messages in the window always wait `listen_seconds`. That is the cost of real debouncing, not a bug.
- **Hot reload drops batches**: `terminate()` clears pending batches. The first message after a reload may go unanswered — expected behavior.
- **`max_merge_count` floor is 2**: a value of `1` is corrected to `2` (merging one message is meaningless).
- **Does not alter replies**: this plugin only decides how many messages count as one turn. Reply shaping belongs to output-pipeline plugins.
- **Merged text uses newlines**: the model sees multiple lines and can still tell sentences apart.

---

## Interaction With Other Plugins

`priority=9999` is deliberate:

| Plugin | Priority | Relationship |
|--------|----------|--------------|
| tattle (moderation) | 10000 | Higher. Banned users are filtered before reaching a batch |
| **This plugin** | **9999** | Merges at the message entry point |
| active_reply_ctl | 0 | Lower. Sees only the merged message, so bursts don't cause repeated proactive replies |
| iris (chat memory) | 0 | Lower. Records the merged content, not three separate lines |

If you run other message-entry plugins and see swallowed messages or double replies, check priority ordering first.

---

## Testing

Offline unit tests ship with the repo. **No AstrBot runtime needed** (a minimal `astrbot` mock is included):

```bash
python tests/test_debounce.py
```

Covered cases:

- Single message: passes through, content unchanged, batch cleaned up
- Two consecutive: the first yields, the second carries everything
- Cap reached: emits immediately without waiting
- @-mention: instant, no delay
- Command: instant, no delay
- Private chat: skipped under `scope=group`
- Different users: independent batches
- Disabled: passes through immediately

---

## Changelog

### v1.0.0

First public release. Burst merging, batch token, count cap, @ / command / GIF exemptions, configurable scope.

See [CHANGELOG.md](CHANGELOG.md) for details.

---

## License

[MIT](LICENSE)
