#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Background monitor that watches DSH sessions and emits Qt signals.

Two cooperating pieces:

- ``DshMonitor`` polls ``session.list`` on a timer to detect session start /
  finish / title changes (cheap and reliable).
- ``DshEventStream`` keeps a live WebSocket connection to ``/api/events.mux``
  and emits real-time session events (messages, tool calls, todos) without the
  heavy ``session.history`` replay.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from dsh_client import DshClient, extract_event_text


class DshMonitor(QThread):
    """Poll DSH session.list to track coarse session state changes."""

    # session_id, title
    session_started = Signal(str, str)
    session_finished = Signal(str, str)
    session_title_changed = Signal(str, str, str)

    # session_id, human readable status
    status_changed = Signal(str, str)

    # emitted after a successful session.list poll
    connected = Signal()

    # error text
    error = Signal(str)

    def __init__(self, client: DshClient, poll_interval: float = 3.0) -> None:
        super().__init__()
        self.client = client
        self.poll_interval = max(1.0, float(poll_interval))
        self._stop = threading.Event()
        self._sessions: dict[str, dict[str, Any]] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # Give the UI a moment to appear before the first network call.
        while not self._stop.wait(1.0):
            try:
                self._poll_once()
            except Exception as e:  # noqa: BLE001 - monitor must survive errors
                self.error.emit(str(e))
            deadline = time.monotonic() + self.poll_interval
            while not self._stop.wait(0.2):
                if time.monotonic() >= deadline:
                    break

    def _poll_once(self) -> None:
        items = self.client.list_sessions()
        self.connected.emit()
        now = {s.get("sessionId", ""): s for s in items if s.get("sessionId")}

        for sid in list(self._sessions):
            if sid not in now:
                self._sessions.pop(sid, None)

        for sid, s in now.items():
            old = self._sessions.get(sid)
            snapshot = self._snapshot(s)
            running = bool(s.get("running"))
            title = snapshot["title"]

            if old is None:
                self._sessions[sid] = snapshot
                if not s.get("blank"):
                    self.status_changed.emit(sid, f"新会话: {title or '未命名'}")
                continue

            old_running = old.get("running", False)
            old_title = old.get("title", "")

            if running and not old_running:
                self.session_started.emit(sid, title or "未命名")
            elif not running and old_running:
                self.session_finished.emit(sid, title or "未命名")

            if title and old_title != title:
                self.session_title_changed.emit(sid, old_title or "", title)

            self._sessions[sid] = snapshot

    @staticmethod
    def _snapshot(s: dict[str, Any]) -> dict[str, Any]:
        proj = s.get("projections") or {}
        title = ""
        try:
            title = (proj.get("values") or {}).get("title") or ""
        except AttributeError:
            title = ""
        return {
            "running": bool(s.get("running")),
            "title": title or "",
            "updatedAt": s.get("updatedAt", 0),
        }


class DshEventStream(QObject):
    """Live WebSocket reader for DSH mux events (browser-compatible path)."""

    # session_id, event dict
    event_received = Signal(str, dict)

    # session_id, tool name, reason
    approval_requested = Signal(str, str, str)

    # session_id, list of question dicts
    question_requested = Signal(str, list)

    # human readable connection notice
    connection_notice = Signal(str)

    def __init__(self, client: DshClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._stopping = False
        self._socket = QWebSocket()
        self._socket.textMessageReceived.connect(self._on_text_message)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_error)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._open)

    def start(self) -> None:
        self._stopping = False
        self._open()

    def stop(self) -> None:
        self._stopping = True
        self._reconnect_timer.stop()
        if self._socket.state() != QAbstractSocket.UnconnectedState:
            self._socket.close()

    def _ws_url(self) -> QUrl:
        base = self.client.base_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        else:
            base = "ws://" + base
        return QUrl(base + "/api/events.mux")

    def _open(self) -> None:
        if self._stopping:
            return
        self._socket.open(self._ws_url())

    def _on_connected(self) -> None:
        self.connection_notice.emit("已连接 DSH 实时事件流")

    def _on_text_message(self, message: str) -> None:
        try:
            full = json.loads(message)
        except Exception:
            return
        if full.get("type") != "server-request":
            return
        frame = full.get("payload")
        if not isinstance(frame, dict):
            return
        ftype = frame.get("type")
        sid = frame.get("sessionId", "")
        if ftype == "session/event":
            event = frame.get("event") or {}
            if sid and isinstance(event, dict):
                self.event_received.emit(sid, event)
        elif ftype == "approval/requested":
            self.approval_requested.emit(
                sid,
                frame.get("toolName") or "工具",
                frame.get("reason") or "",
            )
        elif ftype == "question/requested":
            self.question_requested.emit(sid, frame.get("questions") or [])

    def _on_disconnected(self) -> None:
        if self._stopping:
            return
        self.connection_notice.emit("DSH 实时连接断开，准备重连…")
        self._reconnect_timer.start(1500)

    def _on_error(self, error) -> None:
        if self._stopping:
            return
        self.connection_notice.emit(f"DSH 实时连接错误：{error}")
        if self._socket.state() == QAbstractSocket.UnconnectedState:
            self._reconnect_timer.start(1500)


def process_live_event(event: dict[str, Any]) -> tuple[str, str] | None:
    """Map one live DSH event to a (kind, text) pair for the pet UI.

    Returns None for events the pet should ignore.
    Kinds: user, assistant, tool, todo, status, error.
    """
    etype = event.get("type", "")
    data = event.get("data") or {}

    if etype == "user/message":
        text = extract_event_text(event)
        return ("user", text) if text else None
    if etype == "assistant/message":
        text = extract_event_text(event)
        return ("assistant", text) if text else None
    if etype == "tool/call":
        return ("tool", data.get("name") or "工具")
    if etype == "todo/write":
        todos = data.get("todos") or []
        return ("todo", json_dumps_todos(todos)) if todos else None
    if etype == "turn/start":
        return ("status", "开始处理任务…")
    if etype == "turn/end":
        return ("status", "本轮任务完成")
    if etype == "agent/error":
        return ("error", "DSH 出现错误，请查看终端")
    return None


def json_dumps_todos(todos: list) -> str:
    """Turn a todo/write payload into a short human string."""
    active = [t for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"]
    if active:
        return "📌 " + str(active[0].get("content", ""))
    done = [t for t in todos if isinstance(t, dict) and t.get("status") == "completed"]
    return f"📋 任务清单更新（已完成 {len(done)} 项）"
