#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH (DeepSeek Harness) local API client.

The DSH web app exposes a small JSON-RPC-like HTTP API on the same origin as
its web UI (default http://127.0.0.1:3080).  This module wraps the few calls
the desktop pet needs:

- session.list     -> list sessions and their running/title state
- session.history  -> read recent session events (messages, tools, todos)
- session.prompt   -> send a user prompt into a session
"""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from typing import Any


class DshError(RuntimeError):
    """Raised when DSH cannot be reached or returns a business error."""


class DshClient:
    def __init__(self, base_url: str = "http://127.0.0.1:3080",
                 session_id: str = "", timeout: float = 5.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.session_id = session_id
        self.timeout = timeout

    # -- low level ---------------------------------------------------------

    def _rpc(self, method: str, payload: dict[str, Any]) -> Any:
        if not self.base_url:
            raise DshError("未配置 DSH 地址 (dsh_url)")
        body = {
            "type": "client-request",
            "rpcId": uuid.uuid4().hex,
            "method": method,
            "payload": payload,
        }
        req = urllib.request.Request(
            self.base_url + "/api/" + method,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise DshError(f"DSH HTTP {e.code}: {e.reason}") from e
        except Exception as e:  # socket timeout / refused / JSON error
            raise DshError(f"无法连接 DSH ({self.base_url}): {e}") from e

        result = data.get("result") or {}
        if not result.get("ok"):
            err = result.get("error") or {}
            raise DshError(err.get("message") or "DSH RPC 调用失败")
        return result.get("value")

    # -- high level --------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        value = self._rpc("session.list", {}) or {}
        return value.get("items", []) or []

    def history(self, session_id: str, max_messages: int = 200) -> list[dict[str, Any]]:
        value = self._rpc("session.history", {
            "sessionId": session_id,
            "maxMessages": max_messages,
        }) or {}
        return value.get("events", []) or []

    def send_prompt(self, session_id: str, text: str,
                    mode: str = "queue") -> bool:
        if not text.strip():
            return False
        value = self._rpc("session.prompt", {
            "sessionId": session_id,
            "mode": mode,
            "content": [{"type": "text", "text": text}],
        }) or {}
        return bool(value.get("accepted"))


# Small helpers shared by monitor / UI --------------------------------------

def extract_text_from_blocks(blocks: Any) -> str:
    """Pull human-readable text out of DSH content-block arrays."""
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"].strip())
        # reasoning is intentionally not shown in pet notifications
    return "\n".join(p for p in parts if p)


def extract_event_text(event: dict[str, Any]) -> str:
    """Extract the main text from a user/message or assistant/message event."""
    data = event.get("data") or {}
    blocks = data.get("content")
    if blocks is None:
        blocks = (data.get("message") or {}).get("content")
    return extract_text_from_blocks(blocks)
