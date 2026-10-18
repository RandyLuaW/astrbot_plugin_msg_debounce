"""消息防抖：同一个人短时间连发的多条消息合并成一条再处理。

设计要点
--------
1. 只用一个最高优先级的消息钩子，不注册工具、不改回复格式、不碰 AstrBot 内部 runner。
2. 批次 token 机制：窗口内每来一条新消息就换一次 token，只有持有最新 token 的事件
   负责处理，先到的事件在醒来后主动 stop_event 放弃——因此不会重复触发 LLM。
3. 达到 max_merge_count 时立即带全部内容发出，不再等待，避免长串消息被无限延迟。
4. @ 机器人、指令、GIF 默认豁免，保证点名响应速度。

handler 优先级取 9999：低于 tattle 的封禁门(10000)，让被封禁用户的消息先被拦掉；
高于 active_reply_ctl(0) 与 iris 记录，保证它们只看到合并后的那一条。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from astrbot import logger
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_msg_debounce"
HANDLER_PRIORITY = 9999

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "listen_seconds": 2.0,
    "max_merge_count": 3,
    "scope": "group",
    "exempt_at": True,
    "exempt_command": True,
    "command_prefixes": ["/"],
    "exempt_gif": True,
    "debug": False,
}


@dataclass(slots=True)
class _Batch:
    """一次防抖批次。token 变化即代表有新消息接管。"""

    token: object
    chain: list
    plain: str
    count: int
    created_at: float
    updated_at: float


class MsgDebouncePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self._batches: dict[tuple[str, str], _Batch] = {}
        self._apply_config()

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _cfg(self, key: str):
        raw = self.config.get(key, DEFAULT_CONFIG[key])
        return raw if raw is not None else DEFAULT_CONFIG[key]

    def _apply_config(self) -> None:
        self.enabled = bool(self._cfg("enabled"))
        try:
            self.listen = float(self._cfg("listen_seconds"))
        except (TypeError, ValueError):
            self.listen = float(DEFAULT_CONFIG["listen_seconds"])
        if self.listen < 0:
            self.listen = 0.0
        try:
            self.max_merge = int(self._cfg("max_merge_count"))
        except (TypeError, ValueError):
            self.max_merge = int(DEFAULT_CONFIG["max_merge_count"])
        self.max_merge = max(2, self.max_merge) if self.max_merge > 0 else 0
        self.scope = str(self._cfg("scope") or "group").strip().lower()
        self.exempt_at = bool(self._cfg("exempt_at"))
        self.exempt_command = bool(self._cfg("exempt_command"))
        prefixes = self._cfg("command_prefixes")
        self.command_prefixes = (
            [str(p) for p in prefixes if str(p)]
            if isinstance(prefixes, (list, tuple))
            else ["/"]
        )
        self.exempt_gif = bool(self._cfg("exempt_gif"))
        self.debug = bool(self._cfg("debug"))
        logger.info(
            f"[{PLUGIN_NAME}] 配置已应用｜启用={self.enabled}｜窗口={self.listen}s｜"
            f"上限={self.max_merge or '不限'}｜范围={self.scope}｜"
            f"@豁免={self.exempt_at}｜指令豁免={self.exempt_command}"
        )

    # ------------------------------------------------------------------
    # 主钩子
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=HANDLER_PRIORITY)
    async def on_message(self, event: AstrMessageEvent):
        if not self.enabled or self.listen <= 0:
            return

        umo = event.unified_msg_origin
        uid = str(event.get_sender_id() or "").strip()
        if not umo or not uid:
            return
        if uid == str(event.get_self_id() or "").strip():
            return
        if self.scope == "group" and not event.get_group_id():
            return
        if self.exempt_command and self._is_command(event):
            return
        if self.exempt_at and self._is_at_me(event):
            return

        chain = list(event.get_messages())
        if not chain:
            return
        if self.exempt_gif and self._contains_gif(chain):
            return

        plain = (event.message_str or "").strip()
        key = (umo, uid)
        now = time.monotonic()

        batch = self._batches.get(key)
        if batch is None:
            batch = _Batch(
                token=object(),
                chain=chain,
                plain=plain,
                count=1,
                created_at=now,
                updated_at=now,
            )
            self._batches[key] = batch
        else:
            batch.chain = self._merge_chain(batch.chain, chain)
            batch.plain = self._merge_text(batch.plain, plain)
            batch.count += 1
            batch.token = object()
            batch.updated_at = now
            # 达到条数上限：立即带全部内容发出，不再等待
            if self.max_merge > 0 and batch.count >= self.max_merge:
                self._batches.pop(key, None)
                self._emit(event, batch)
                return

        my_token = batch.token
        await asyncio.sleep(self.listen)

        current = self._batches.get(key)
        if current is not batch or current.token is not my_token:
            # 窗口内已有更新的消息接管，本事件主动让位
            if self.debug:
                logger.debug(
                    f"[{PLUGIN_NAME}] 让位：umo={umo} uid={uid} "
                    f"count={batch.count}（由更新消息接管）"
                )
            event.stop_event()
            return

        self._batches.pop(key, None)
        self._gc()
        self._emit(event, batch)

    # ------------------------------------------------------------------
    # 事件改写
    # ------------------------------------------------------------------

    def _emit(self, event: AstrMessageEvent, batch: _Batch) -> None:
        """把合并后的内容写回事件，交给后续 stage 正常处理。"""
        if batch.count > 1:
            try:
                event.message_obj.message = list(batch.chain)
                event.message_obj.message_str = batch.plain
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 改写 message_obj 失败: {exc}")
            try:
                event.message_str = batch.plain
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 改写 message_str 失败: {exc}")
            logger.info(
                f"[{PLUGIN_NAME}] 已合并 {batch.count} 条消息｜"
                f"umo={event.unified_msg_origin}｜"
                f"内容={batch.plain[:60]!r}"
            )
        elif self.debug:
            logger.debug(
                f"[{PLUGIN_NAME}] 单条消息放行｜umo={event.unified_msg_origin}"
            )

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def _is_at_me(self, event: AstrMessageEvent) -> bool:
        me = str(event.get_self_id() or "").strip()
        if not me:
            return False
        for seg in event.get_messages():
            if isinstance(seg, At) and str(getattr(seg, "qq", "")).strip() == me:
                return True
        return False

    def _is_command(self, event: AstrMessageEvent) -> bool:
        text = (event.message_str or "").lstrip()
        if not text:
            return False
        return any(text.startswith(p) for p in self.command_prefixes)

    @classmethod
    def _contains_gif(cls, chain: list) -> bool:
        for seg in chain:
            if not isinstance(seg, Image):
                continue
            for ref in (
                getattr(seg, "path", None),
                getattr(seg, "url", None),
                getattr(seg, "file", None),
            ):
                if cls._is_gif_ref(ref):
                    return True
        return False

    @staticmethod
    def _is_gif_ref(ref) -> bool:
        if not ref or not isinstance(ref, str):
            return False
        lowered = ref.lower()
        target = ref
        if lowered.startswith(("http://", "https://")):
            target = urlparse(ref).path
        elif lowered.startswith("file:///"):
            target = ref[8:]
        return Path(target).suffix.lower() == ".gif"

    # ------------------------------------------------------------------
    # 合并
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_chain(previous: list, current: list) -> list:
        merged = list(previous)
        if merged and current:
            merged.append(Plain("\n", convert=False))
        merged.extend(current)
        return merged

    @staticmethod
    def _merge_text(previous: str, current: str) -> str:
        parts = [p.strip() for p in (previous, current) if p and p.strip()]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def _gc(self) -> None:
        if len(self._batches) < 64:
            return
        now = time.monotonic()
        ttl = max(60.0, self.listen * 20)
        for key, batch in list(self._batches.items()):
            if now - batch.updated_at > ttl:
                self._batches.pop(key, None)

    async def terminate(self) -> None:
        self._batches.clear()
