"""Where the agent meets people. Real people reply from their own phones (Telegram), or the operator types their
messages in the console. Either way the agent only sees what a person actually said.

    SIM_CHANNEL=console      (default) messages print here; type `@Name: text` to deliver a reply from Name
    SIM_CHANNEL=telegram     TELEGRAM_BOT_TOKEN + chat ids in sim/config.json; the bot sits in a real group and DMs
                             each member (each member must open the bot and press Start once, so it may DM them)
"""
from __future__ import annotations
import os
from typing import Optional
import httpx
from .world import paint, C_SAY


class Channel:
    name = "console"

    def __init__(self, members: dict[str, dict], group_title: str):
        self.members, self.group_title = members, group_title

    def send_group(self, text: str) -> str:
        print(paint(C_SAY, f"→ GROUP «{self.group_title}»\n") + _indent(text))
        return "delivered"

    def send_dm(self, member_id: str, text: str) -> str:
        print(paint(C_SAY, f"→ DM {self.members[member_id]['name']}\n") + _indent(text))
        return "delivered"

    def poll(self) -> list[dict]:
        """Inbound messages since the last poll: [{member_id, where: 'group'|'dm', text}]."""
        return []


class TelegramChannel(Channel):
    """Telegram Bot API: sendMessage and getUpdates (https://core.telegram.org/bots/api)."""
    name = "telegram"

    def __init__(self, members: dict[str, dict], group_title: str, group_chat_id: int | str):
        super().__init__(members, group_title)
        self.token = os.environ["TELEGRAM_BOT_TOKEN"]
        self.group_chat_id = group_chat_id
        self.http = httpx.Client(timeout=30)
        self.offset: Optional[int] = None
        self.by_tg = {str(m["telegram_user_id"]): mid for mid, m in members.items() if m.get("telegram_user_id")}

    def _api(self, method: str, **params) -> dict:
        r = self.http.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body}")
        return body["result"]

    def send_group(self, text: str) -> str:
        super().send_group(text)
        self._api("sendMessage", chat_id=self.group_chat_id, text=text)
        return "delivered"

    def send_dm(self, member_id: str, text: str) -> str:
        super().send_dm(member_id, text)
        tg = self.members[member_id].get("telegram_user_id")
        if not tg:
            return "not delivered: this member has not started a chat with the bot"
        self._api("sendMessage", chat_id=tg, text=text)
        return "delivered"

    def poll(self) -> list[dict]:
        params = {"timeout": 0, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        out = []
        for u in self._api("getUpdates", **params):
            self.offset = u["update_id"] + 1
            msg = u.get("message") or {}
            sender = str((msg.get("from") or {}).get("id", ""))
            text = msg.get("text")
            if not text or sender not in self.by_tg:
                continue
            where = "dm" if (msg.get("chat") or {}).get("type") == "private" else "group"
            out.append({"member_id": self.by_tg[sender], "where": where, "text": text})
        return out


def build_channel(cfg: dict) -> Channel:
    members = {m["id"]: m for m in cfg["members"]}
    if os.environ.get("SIM_CHANNEL", cfg.get("channel", "console")) == "telegram":
        return TelegramChannel(members, cfg["group_title"], cfg["telegram_group_chat_id"])
    return Channel(members, cfg["group_title"])


def _indent(text: str) -> str:
    return "\n".join("   " + line for line in text.splitlines())
