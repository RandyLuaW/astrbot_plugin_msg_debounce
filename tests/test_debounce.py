"""消息防抖插件离线单测。

不依赖 AstrBot 运行时：内置 astrbot 模块的最小 mock，直接加载 ../main.py。

跑法：
    python tests/test_debounce.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parent.parent / "main.py"


# ----------------------------------------------------------------------
# astrbot 最小 mock
# ----------------------------------------------------------------------


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _install_astrbot_mock() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    astrbot.logger = _Logger()

    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object

    class _EventMessageType:
        ALL = "ALL"

    class _Filter:
        EventMessageType = _EventMessageType

        @staticmethod
        def event_message_type(*args, **kwargs):
            def deco(fn):
                return fn

            return deco

    event.filter = _Filter

    components = types.ModuleType("astrbot.api.message_components")

    class At:
        def __init__(self, qq=None, name=None):
            self.qq = qq
            self.name = name

    class Image:
        def __init__(self, path=None, url=None, file=None):
            self.path = path
            self.url = url
            self.file = file

    class Plain:
        def __init__(self, text="", convert=True):
            self.text = text
            self.convert = convert

    components.At = At
    components.Image = Image
    components.Plain = Plain

    star = types.ModuleType("astrbot.api.star")

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    star.Context = Context
    star.Star = Star

    api.event = event
    api.message_components = components
    api.star = star

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.message_components"] = components
    sys.modules["astrbot.api.star"] = star


_install_astrbot_mock()

spec = importlib.util.spec_from_file_location("msg_debounce_main", PLUGIN_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["msg_debounce_main"] = mod
spec.loader.exec_module(mod)

At = mod.At
Image = mod.Image
Plain = mod.Plain
MsgDebouncePlugin = mod.MsgDebouncePlugin


# ----------------------------------------------------------------------
# 假事件
# ----------------------------------------------------------------------


class FakeMessageObj:
    def __init__(self, message, message_str):
        self.message = message
        self.message_str = message_str


class FakeEvent:
    def __init__(self, text="hi", uid="111", gid="222", self_id="999", chain=None):
        self.unified_msg_origin = (
            f"napcat:GroupMessage:{gid}" if gid else f"napcat:FriendMessage:{uid}"
        )
        self.message_str = text
        self._uid = uid
        self._gid = gid
        self._self_id = self_id
        self._chain = chain if chain is not None else [Plain(text)]
        self.message_obj = FakeMessageObj(self._chain, text)
        self.stopped = False

    def get_sender_id(self):
        return self._uid

    def get_self_id(self):
        return self._self_id

    def get_group_id(self):
        return self._gid

    def get_messages(self):
        return self._chain

    def stop_event(self):
        self.stopped = True


def make_plugin(**overrides):
    plugin = MsgDebouncePlugin.__new__(MsgDebouncePlugin)
    plugin._batches = {}
    cfg = dict(mod.DEFAULT_CONFIG)
    cfg.update({"listen_seconds": 0.2, "debug": True})
    cfg.update(overrides)
    plugin.config = cfg
    plugin._apply_config()
    return plugin


def texts(event):
    return [seg.text for seg in event.message_obj.message if isinstance(seg, Plain)]


async def elapsed(coro):
    loop = asyncio.get_running_loop()
    start = loop.time()
    await coro
    return loop.time() - start


# ----------------------------------------------------------------------
# 用例
# ----------------------------------------------------------------------


async def case_single():
    p = make_plugin()
    e = FakeEvent("只有一句")
    await p.on_message(e)
    assert not e.stopped, "单条消息不应被拦"
    assert e.message_str == "只有一句"
    assert p._batches == {}, "批次应已清理"
    print("  [OK] 单条消息：放行、内容不变、批次已清理")


async def case_two_merged():
    p = make_plugin()
    e1 = FakeEvent("第一句")
    e2 = FakeEvent("第二句")
    t1 = asyncio.create_task(p.on_message(e1))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(p.on_message(e2))
    await asyncio.gather(t1, t2)

    assert e1.stopped, "先到的事件应主动让位"
    assert not e2.stopped, "后到的消息应负责处理"
    assert e2.message_str == "第一句\n第二句", f"合并文本不对: {e2.message_str!r}"
    assert texts(e2) == ["第一句", "\n", "第二句"], f"合并链不对: {texts(e2)!r}"
    assert p._batches == {}
    print("  [OK] 两条连续：先到的让位，后到的携带全部内容")


async def case_three_reaches_limit():
    p = make_plugin(max_merge_count=3)
    e1, e2, e3 = FakeEvent("A"), FakeEvent("B"), FakeEvent("C")
    t1 = asyncio.create_task(p.on_message(e1))
    await asyncio.sleep(0.03)
    t2 = asyncio.create_task(p.on_message(e2))
    await asyncio.sleep(0.03)
    t3 = asyncio.create_task(p.on_message(e3))
    await asyncio.gather(t1, t2, t3)

    assert e1.stopped and e2.stopped, "前两条都应让位"
    assert not e3.stopped, "第三条应负责处理"
    assert e3.message_str == "A\nB\nC", f"合并文本不对: {e3.message_str!r}"
    print("  [OK] 达到上限：立即带全部内容发出，不再等待")


async def case_exempt_at():
    p = make_plugin()
    e = FakeEvent("在吗", chain=[At(qq="999"), Plain("在吗")])
    cost = await elapsed(p.on_message(e))
    assert not e.stopped
    assert cost < 0.1, f"@ 消息不应等待，实际耗时 {cost:.3f}s"
    print("  [OK] @ 机器人：立即放行，不等待")


async def case_exempt_command():
    p = make_plugin()
    e = FakeEvent("/help")
    cost = await elapsed(p.on_message(e))
    assert cost < 0.1, f"指令不应等待，实际耗时 {cost:.3f}s"
    print("  [OK] 指令消息：立即放行，不等待")


async def case_exempt_gif():
    p = make_plugin()
    e = FakeEvent("[图片]", chain=[Image(url="https://example.com/a.gif")])
    cost = await elapsed(p.on_message(e))
    assert cost < 0.1, f"GIF 不应等待，实际耗时 {cost:.3f}s"
    print("  [OK] 含 GIF：立即放行，不合并")


async def case_private_skipped():
    p = make_plugin(scope="group")
    e = FakeEvent("私聊", gid=None)
    cost = await elapsed(p.on_message(e))
    assert cost < 0.1, "私聊在 group 模式下不应等待"
    print("  [OK] 私聊：group 模式下不参与防抖")


async def case_different_users():
    p = make_plugin()
    a = FakeEvent("A说", uid="111")
    b = FakeEvent("B说", uid="222")
    ta = asyncio.create_task(p.on_message(a))
    await asyncio.sleep(0.03)
    tb = asyncio.create_task(p.on_message(b))
    await asyncio.gather(ta, tb)
    assert not a.stopped and not b.stopped, "不同用户互不影响"
    assert a.message_str == "A说" and b.message_str == "B说"
    print("  [OK] 不同用户：批次互不干扰")


async def case_self_message():
    p = make_plugin()
    e = FakeEvent("自己说的", uid="999", self_id="999")
    cost = await elapsed(p.on_message(e))
    assert cost < 0.1, "机器人自己的消息应直接跳过"
    print("  [OK] 机器人自身消息：直接跳过")


async def case_disabled():
    p = make_plugin(enabled=False)
    e = FakeEvent("x")
    cost = await elapsed(p.on_message(e))
    assert cost < 0.1
    print("  [OK] 关闭时：直接放行")


async def main():
    print("消息防抖插件单测")
    cases = (
        case_single,
        case_two_merged,
        case_three_reaches_limit,
        case_exempt_at,
        case_exempt_command,
        case_exempt_gif,
        case_private_skipped,
        case_different_users,
        case_self_message,
        case_disabled,
    )
    for fn in cases:
        await fn()
    print(f"\n全部通过（{len(cases)} 个用例）")


if __name__ == "__main__":
    asyncio.run(main())
