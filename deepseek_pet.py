#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 娘桌面宠物 — DSH 终端联动版.

功能:
- 使用 DEEPSEEK 娘图片作为桌宠,支持透明背景、待机浮动/眨眼动画。
- 常驻系统托盘,Windows 11 下可开机自启、最小化到托盘长期运行。
- 监控 DSH (DeepSeek Harness) 本地 Web API,任务开始/结束/新消息时用气泡提示。
- 可与 DSH 会话双向通信:从桌宠输入框向当前会话发送 prompt,
  DSH 的新回复会由桌宠气泡展示。
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import webbrowser
from collections import deque
from typing import Any

from PySide6.QtCore import (
    QLockFile,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from dsh_client import DshClient
from dsh_monitor import DshEventStream, DshMonitor, process_live_event

APP_NAME = "SeekMaidPet"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_PATH = os.path.join(BASE_DIR, "assets", "deepseek_girl.png")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

PERSONA_LINES: dict[str, list[str]] = {
    "task_start": [
        "收到任务！我会好好盯着 DSH 的～",
        "有新任务啦，主人加油，我也加油！",
        "唔…开始干活了，我会守着你的！",
    ],
    "task_finish": [
        "搞定啦！辛苦主人了～",
        "任务完成！可以休息一下啦～",
        "耶！DSH 把任务做完啦！",
    ],
    "approval": [
        "主人，这里需要你点个头哦～",
        "有个地方需要主人授权一下呢～",
        "需要你确认一下，我在旁边等你～",
    ],
    "question": [
        "主人主人，DSH 问你问题啦！",
        "有提问来了，主人看一下嘛～",
        "DSH 在等你回答哦～",
    ],
    "error": [
        "呜…好像出错了，要看看吗？",
        "有点不对劲，主人去看看 DSH 吧～",
        "抱歉…好像遇到问题了。",
    ],
    "idle": [
        "主人，我在这里哦～",
        "今天也要加油鸭！",
        "需要我盯着 DSH 吗？我随时待命～",
    ],
}

DEFAULT_CONFIG: dict[str, Any] = {
    "dsh_url": "",
    "session_id": "",
    "poll_interval": 3,
    "scale": 1.0,
    "notify_on_start": True,
    "notify_on_finish": True,
    "notify_on_message": True,
    "notify_on_tool": False,
    "notify_on_todos": True,
    "notify_on_status": False,
    "notify_sound": True,
    "notify_duration_user_ms": 8000,
    "notify_duration_assistant_ms": 15000,
    "notify_duration_start_ms": 8000,
    "notify_duration_finish_ms": 12000,
    "startup_timeout_minutes": 3,
    "position": {"x": 120, "y": 120},
}


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update(user)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    if not cfg.get("dsh_url"):
        cfg["dsh_url"] = os.environ.get("DSH_WEB_URL", "http://127.0.0.1:3080")
    timeout_env = os.environ.get("DSH_PET_STARTUP_TIMEOUT_MINUTES")
    if timeout_env:
        try:
            cfg["startup_timeout_minutes"] = float(timeout_env)
        except ValueError:
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# image helpers
# ---------------------------------------------------------------------------

def make_transparent_bg(image: QImage, tolerance: int = 30) -> QImage:
    """Flood-fill the connected background (from image borders) to transparent.

    This keeps interior white parts of the character intact and turns the usual
    white/light illustration background into a real transparent desktop-pet PNG.
    """
    image = image.convertToFormat(QImage.Format_ARGB32)
    w, h = image.width(), image.height()
    if w == 0 or h == 0:
        return image

    base = image.pixelColor(0, 0)
    tol = max(1, tolerance)
    visited = bytearray(w * h)
    dq: deque[tuple[int, int]] = deque()

    def similar(c: QColor) -> bool:
        return (
            abs(c.red() - base.red()) <= tol
            and abs(c.green() - base.green()) <= tol
            and abs(c.blue() - base.blue()) <= tol
        )

    def push(x: int, y: int) -> None:
        if 0 <= x < w and 0 <= y < h and not visited[y * w + x]:
            c = image.pixelColor(x, y)
            if similar(c):
                visited[y * w + x] = 1
                dq.append((x, y))

    for x in range(w):
        push(x, 0)
        push(x, h - 1)
    for y in range(h):
        push(0, y)
        push(w - 1, y)

    transparent = QColor(0, 0, 0, 0)
    while dq:
        x, y = dq.popleft()
        image.setPixelColor(x, y, transparent)
        push(x + 1, y)
        push(x - 1, y)
        push(x, y + 1)
        push(x, y - 1)

    return image


def load_character_pixmap(path: str = ASSET_PATH, base_width: int = 240) -> QPixmap:
    img = QImage(path)
    if img.isNull():
        # Fallback: draw a simple cute circle so the pet still appears.
        pix = QPixmap(base_width, base_width)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setBrush(QColor("#4D6BFE"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, base_width, base_width)
        p.end()
        return pix

    # Cache the transparent version next to the original so startup stays fast.
    cache_path = os.path.join(os.path.dirname(path), "deepseek_girl_transparent.png")
    try:
        orig_mtime = os.path.getmtime(path)
        cache_mtime = os.path.getmtime(cache_path)
        use_cache = cache_mtime >= orig_mtime
    except OSError:
        use_cache = False

    if use_cache:
        cached = QImage(cache_path)
        if not cached.isNull():
            img = cached
    # 如果图片本身已经带 alpha（用户提供的是透明 PNG），就不再做白底移除。
    if not img.hasAlphaChannel():
        img = make_transparent_bg(img)
    try:
        img.save(cache_path)
    except Exception:
        pass

    pix = QPixmap.fromImage(img)
    if pix.width() > base_width:
        pix = pix.scaledToWidth(
            base_width, Qt.SmoothTransformation
        )
    return pix


def truncate(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# prompt worker (non-blocking send)
# ---------------------------------------------------------------------------

class PromptWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, client: DshClient, session_id: str, text: str) -> None:
        super().__init__()
        self.client = client
        self.session_id = session_id
        self.text = text

    def run(self) -> None:
        try:
            ok = self.client.send_prompt(self.session_id, self.text)
            self.done.emit(ok, "已发送到 DSH" if ok else "DSH 未接受该消息")
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


# ---------------------------------------------------------------------------
# notification bubble
# ---------------------------------------------------------------------------

class NotificationBubble(QWidget):
    """美化的通知气泡：圆角渐变卡片 + 小尾巴，按通知类型切换配色。"""

    KINDS = {
        "info":     {"icon": "💬", "accent": "#4D6BFE", "bg": "#EEF2FF"},
        "approval": {"icon": "🔐", "accent": "#FF8C42", "bg": "#FFF4E8"},
        "question": {"icon": "❓", "accent": "#9B6DFF", "bg": "#F3EDFF"},
        "success":  {"icon": "✅", "accent": "#2FB86E", "bg": "#E8F9F0"},
        "error":    {"icon": "⚠️", "accent": "#FF5A5A", "bg": "#FFEDED"},
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(280)
        self.setMinimumHeight(64)

        self._kind = "info"
        self._title = ""
        self._message = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(4)

        self._title_label = QLabel(self)
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; color: #1F2430; background: transparent;"
        )

        self._msg_label = QLabel(self)
        self._msg_label.setWordWrap(True)
        self._msg_label.setTextFormat(Qt.PlainText)
        self._msg_label.setStyleSheet(
            "font-size: 12px; color: #3A3F4B; background: transparent;"
        )

        layout.addWidget(self._title_label)
        layout.addWidget(self._msg_label)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

    def set_content(self, title: str, message: str, kind: str = "info") -> None:
        self._kind = kind if kind in self.KINDS else "info"
        self._title = title
        self._message = message
        meta = self.KINDS[self._kind]
        self._title_label.setText(f"{meta['icon']}  {title}")
        self._msg_label.setText(message)
        self._msg_label.adjustSize()
        self._title_label.adjustSize()
        self.adjustSize()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        meta = self.KINDS[self._kind]
        accent = QColor(meta["accent"])
        bg = QColor(meta["bg"])

        w = self.width()
        h = self.height()
        body = QRectF(1, 1, w - 2, h - 16)
        tail_w = 18.0
        tail_h = 14.0
        cx = w / 2.0

        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        tail = QPolygonF([
            QPointF(cx - tail_w / 2, body.bottom() - 1),
            QPointF(cx + tail_w / 2, body.bottom() - 1),
            QPointF(cx, body.bottom() + tail_h),
        ])
        path.addPolygon(tail)

        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0, bg.lighter(103))
        gradient.setColorAt(1, bg)
        painter.fillPath(path, gradient)

        painter.setPen(QPen(accent, 1.6))
        painter.drawPath(path)
        painter.end()


# ---------------------------------------------------------------------------
# pet window
# ---------------------------------------------------------------------------

class PetWindow(QWidget):
    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__()
        self.cfg = cfg
        self.pixmap = load_character_pixmap(base_width=int(220 * float(cfg.get("scale", 1.0))))
        self._dragging = False
        self._drag_offset = QPoint()
        self._phase = 0.0
        self._bob = 0.0
        self._tilt = 0.0
        self._scale_y = 1.0
        self._is_busy = False
        self._pin_counter = 0
        self._dsh_ok = False
        self._bubble_timer: QTimer | None = None
        self._worker: PromptWorker | None = None

        self._init_window()
        self._init_tray()

        # DSH client / monitor
        self.client = DshClient(
            base_url=cfg.get("dsh_url", "http://127.0.0.1:3080"),
            session_id=cfg.get("session_id", ""),
            timeout=5,
        )
        self.session_id = cfg.get("session_id", "") or ""
        self._resolve_session()

        self.monitor = DshMonitor(
            client=self.client,
            poll_interval=float(cfg.get("poll_interval", 3)),
        )
        self.event_stream = DshEventStream(client=self.client)
        self._connect_monitor()
        self.monitor.start()
        self.event_stream.start()

        # Watchdog: if DSH is not reachable within N minutes, close the pet.
        timeout_min = float(cfg.get("startup_timeout_minutes", 3))
        if timeout_min > 0:
            self._watchdog = QTimer(self)
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._watchdog_timeout)
            self._watchdog.start(int(timeout_min * 60 * 1000))

        # Animation
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(33)

        # 保持最前：WSLg 可能不严格遵守 WindowStaysOnTopHint，定时 raise 一下。
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._keep_on_top)
        self._top_timer.start(1200)

        # Apply saved position
        pos = cfg.get("position") or {}
        if isinstance(pos, dict) and pos.get("x") is not None:
            self.move(int(pos["x"]), int(pos["y"]))

        self.show_message("嗨~ 我是 DeepSeek 娘 💙", 4000)

    # -- UI setup ----------------------------------------------------------

    def _app_icon(self) -> QIcon:
        ico = os.path.join(BASE_DIR, "assets", "deepseek_girl.ico")
        if os.path.exists(ico):
            return QIcon(ico)
        return QIcon(self.pixmap)

    def _init_window(self) -> None:
        # 桌宠模式：无边框 + 透明背景 + 总在最前。
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("SeekMaid 女仆")
        self.setWindowIcon(self._app_icon())
        # 窗口本身透明，只显示角色和气泡。
        self.setStyleSheet("QWidget { background: transparent; }")

        w = int(self.pixmap.width() * 1.35)
        h = int(self.pixmap.height() * 1.25)
        self.setFixedSize(max(200, w), max(240, h))

        self.bubble = NotificationBubble(self)
        self.bubble.setMaximumWidth(int(self.width() * 0.95))
        self.bubble.setMinimumWidth(200)
        self.bubble.hide()

        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self.bubble.hide)

        self._status_label = QLabel(self)
        self._status_label.setStyleSheet(
            "color: #4D6BFE; font-size: 11px; background: transparent;"
        )
        self._status_label.hide()

    def _init_tray(self) -> None:
        icon = self._app_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("SeekMaid 女仆")

        menu = QMenu()

        act_send = QAction("💬 发送消息给 DSH", self)
        act_send.triggered.connect(self._ask_send_message)
        menu.addAction(act_send)

        act_test_approval = QAction("🔐 测试授权提示", self)
        act_test_approval.triggered.connect(self._test_approval)
        menu.addAction(act_test_approval)

        act_open = QAction("🌐 打开 DSH 网页", self)
        act_open.triggered.connect(self._open_dsh_web)
        menu.addAction(act_open)

        act_show = QAction("👀 显示/隐藏桌宠", self)
        act_show.triggered.connect(self._toggle_visible)
        menu.addAction(act_show)

        menu.addSeparator()

        act_autostart = QAction("🖥 开机自启", self)
        act_autostart.setCheckable(True)
        act_autostart.setChecked(self._autostart_enabled())
        act_autostart.triggered.connect(self._toggle_autostart)
        menu.addAction(act_autostart)

        menu.addSeparator()

        act_quit = QAction("🚪 退出", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # -- monitor connections ----------------------------------------------

    def _connect_monitor(self) -> None:
        m = self.monitor
        m.session_started.connect(self._on_session_started)
        m.session_finished.connect(self._on_session_finished)
        m.session_title_changed.connect(self._on_session_title_changed)
        m.status_changed.connect(self._on_status_changed)
        m.connected.connect(self._on_dsh_connected)
        m.error.connect(self._on_monitor_error)

        es = self.event_stream
        es.event_received.connect(self._on_event_received)
        es.approval_requested.connect(self._on_approval_requested)
        es.question_requested.connect(self._on_question_requested)
        es.connection_notice.connect(self._on_event_stream_notice)

    # -- DSH session resolution -------------------------------------------

    def _resolve_session(self) -> None:
        """Pick a session if none is configured: newest non-blank, running preferred."""
        if self.session_id:
            return
        try:
            items = self.client.list_sessions()
        except Exception:
            return
        self._dsh_ok = True
        candidates = [s for s in items if not s.get("blank")]
        if not candidates:
            return
        candidates.sort(
            key=lambda s: (
                bool(s.get("running")),
                int(s.get("updatedAt", 0) or 0),
            ),
            reverse=True,
        )
        sid = candidates[0].get("sessionId", "")
        if sid:
            self.session_id = sid
            self.client.session_id = sid
            self.cfg["session_id"] = sid
            save_config(self.cfg)

    # -- slots --------------------------------------------------------------

    def _on_session_started(self, sid: str, title: str) -> None:
        if self.cfg.get("notify_on_start", True):
            self.show_message(
                f"{self._persona('task_start')}\n《{title}》",
                int(self.cfg.get("notify_duration_start_ms", 8000)),
                kind="success",
                title="任务开始",
                sound=True,
            )
        if sid == self.session_id:
            self._set_busy(True)

    def _on_session_finished(self, sid: str, title: str) -> None:
        if self.cfg.get("notify_on_finish", True):
            self.show_message(
                f"{self._persona('task_finish')}\n《{title}》",
                int(self.cfg.get("notify_duration_finish_ms", 12000)),
                kind="success",
                title="任务完成",
                sound=True,
            )
        if sid == self.session_id:
            self._set_busy(False)

    def _on_session_title_changed(self, sid: str, old: str, new: str) -> None:
        if sid == self.session_id and self.cfg.get("notify_on_status", False):
            self.show_message(f"《{new}》", 4000, kind="info", title="标题更新")

    def _on_user_message(self, sid: str, text: str) -> None:
        if sid != self.session_id:
            return
        if self.cfg.get("notify_on_message", True):
            self.show_message(
                truncate(text, 60),
                int(self.cfg.get("notify_duration_user_ms", 8000)),
                kind="info",
                title="你发送了消息",
            )
        self._set_busy(True)

    def _on_assistant_message(self, sid: str, text: str) -> None:
        if sid != self.session_id:
            return
        if self.cfg.get("notify_on_message", True):
            self.show_message(
                truncate(text, 80),
                int(self.cfg.get("notify_duration_assistant_ms", 15000)),
                kind="info",
                title="DeepSeek 回复",
            )
        self._set_busy(False)

    def _on_tool_call(self, sid: str, name: str) -> None:
        if sid != self.session_id:
            return
        if self.cfg.get("notify_on_tool", False):
            self.show_message(name, 3000, kind="info", title="正在使用工具")
        self._set_busy(True)

    def _on_todos_changed(self, sid: str, todos: list) -> None:
        if sid != self.session_id:
            return
        if not self.cfg.get("notify_on_todos", True):
            return
        active = [t for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"]
        if active:
            item = active[0].get("content", "")
            self.show_message(truncate(item, 100), 5000, kind="info", title="任务进行中")
        else:
            self.show_message("任务清单已更新", 3000, kind="info", title="任务清单")

    def _on_status_changed(self, sid: str, status: str) -> None:
        if sid != self.session_id:
            return
        if self.cfg.get("notify_on_status", False) or "错误" in status:
            self.show_message(status, 4000, kind="info", title="状态")

    def _on_dsh_connected(self) -> None:
        self._dsh_ok = True

    def _on_monitor_error(self, err: str) -> None:
        # Only show once in a while; a disconnected DSH should not spam.
        self._status_label.setText("DSH 未连接")
        self._status_label.adjustSize()
        self._status_label.move(8, 8)
        self._status_label.show()

    def _on_event_received(self, sid: str, event: dict) -> None:
        if sid != self.session_id:
            return
        mapped = process_live_event(event)
        if mapped is None:
            return
        kind, text = mapped
        if kind == "user":
            self._on_user_message(sid, text)
        elif kind == "assistant":
            self._on_assistant_message(sid, text)
        elif kind == "tool":
            self._on_tool_call(sid, text)
        elif kind == "todo":
            if self.cfg.get("notify_on_todos", True):
                self.show_message(text, 5000, kind="info", title="任务进行中")
        elif kind == "status":
            if text == "开始处理任务…":
                self._set_busy(True)
            elif text == "本轮任务完成":
                self._set_busy(False)
            if self.cfg.get("notify_on_status", False):
                self.show_message(text, 4000, kind="info", title="状态")
        elif kind == "error":
            self._set_busy(False)
            self.show_message(
                f"{self._persona('error')}\n{truncate(text, 120)}",
                8000,
                kind="error",
                title="出错了",
                sound=True,
            )

    def _on_approval_requested(self, sid: str, tool: str, reason: str) -> None:
        # 只提示“哪个工具需要授权”，不把完整授权理由刷到气泡里，避免信息过载。
        self.show_message(
            self._persona("approval"),
            12000,
            kind="approval",
            title=f"需要授权：{tool}",
            sound=True,
        )
        self._set_busy(True)

    def _on_question_requested(self, sid: str, questions: list) -> None:
        if not questions:
            return
        first = questions[0]
        q = first.get("question") or first.get("header") or "DSH 需要你回答"
        self.show_message(
            f"{self._persona('question')}\n{truncate(q, 200)}",
            15000,
            kind="question",
            title="DSH 提问",
            sound=True,
        )
        self._set_busy(True)

    def _on_event_stream_notice(self, notice: str) -> None:
        # Keep connection issues quiet but visible in the corner label.
        if "已连接" in notice:
            self._dsh_ok = True
            self._status_label.hide()
            return
        self._status_label.setText("DSH 连接异常")
        self._status_label.adjustSize()
        self._status_label.move(8, 8)
        self._status_label.show()

    # -- persona ---------------------------------------------------------------

    def _persona(self, kind: str) -> str:
        lines = PERSONA_LINES.get(kind) or PERSONA_LINES["idle"]
        return random.choice(lines)

    # -- keep on top ---------------------------------------------------------

    def _keep_on_top(self) -> None:
        if self.isVisible():
            self.raise_()
            # Windows 原生下 Qt 的 WindowStaysOnTopHint 已足够，不需要 PowerShell 置顶。
            if sys.platform == "win32":
                return
            self._pin_counter += 1
            # 每 3 次（约 3.6 秒）调一次 Windows 侧置顶，确保 WSLg 窗口保持 TOPMOST。
            if self._pin_counter % 3 == 0:
                self._pin_on_windows()

    def _pin_on_windows(self) -> None:
        """通过 WSL interop 调用 Windows PowerShell，把桌宠窗口设为 TOPMOST。"""
        ps = r'''
Add-Type -TypeDefinition 'using System; using System.Text; using System.Runtime.InteropServices; public class W { [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l); public delegate bool EnumWindowsProc(IntPtr h, IntPtr l); [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n); [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h); [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f); }';
$script:h = [IntPtr]::Zero;
$cb = [W+EnumWindowsProc]{ param($h,$l) $sb = New-Object System.Text.StringBuilder 256; [W]::GetWindowText($h,$sb,256) | Out-Null; if([W]::IsWindowVisible($h) -and $sb.ToString() -like '*SeekMaid*'){ $script:h = $h; return $false }; return $true };
[W]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null;
if($script:h -ne [IntPtr]::Zero){ [W]::SetWindowPos($script:h,[IntPtr](-1),0,0,0,0,0x0001 -bor 0x0002) | Out-Null }
'''
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            # 非 WSL/Windows 环境或调用失败时忽略
            pass

    def _bring_to_front_windows(self) -> None:
        """启动时把桌宠窗口带到 Windows 前台一次，避免重启后藏在后面。"""
        ps = r'''
Add-Type -TypeDefinition 'using System; using System.Text; using System.Runtime.InteropServices; public class W { [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l); public delegate bool EnumWindowsProc(IntPtr h, IntPtr l); [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n); [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h); }';
$script:h = [IntPtr]::Zero;
$cb = [W+EnumWindowsProc]{ param($h,$l) $sb = New-Object System.Text.StringBuilder 256; [W]::GetWindowText($h,$sb,256) | Out-Null; if([W]::IsWindowVisible($h) -and $sb.ToString() -like '*SeekMaid*'){ $script:h = $h; return $false }; return $true };
[W]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null;
if($script:h -ne [IntPtr]::Zero){ [W]::BringWindowToTop($script:h) | Out-Null; [W]::SetForegroundWindow($script:h) | Out-Null }
'''
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _initial_windows_setup(self) -> None:
        if sys.platform == "win32":
            return
        self._pin_on_windows()
        self._bring_to_front_windows()

    # -- animations ---------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        if busy:
            self._status_label.setText("工作中…")
        else:
            self._status_label.setText("")
        self._status_label.adjustSize()
        self._status_label.move(8, 8)
        self._status_label.setVisible(busy)

    def _animate(self) -> None:
        self._phase += 1.0
        speed = 1.4 if self._is_busy else 1.0
        self._bob = math.sin(self._phase / 28.0 * speed) * (9 if self._is_busy else 6)
        self._tilt = math.sin(self._phase / 40.0 * speed) * (4 if self._is_busy else 2.5)

        # Blink every ~4 seconds for ~120ms.
        period = 4000
        if self._phase % period < 120:
            self._scale_y = 0.92
        else:
            self._scale_y = 1.0
        self.update()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)

        # Pet is anchored at the bottom center.
        px = self.pixmap.width() / 2.0
        py = self.pixmap.height()
        cx = self.width() / 2.0
        cy = self.height() - 25 + self._bob

        painter.translate(cx, cy)
        painter.rotate(self._tilt)
        painter.scale(1.0, self._scale_y)
        painter.drawPixmap(
            int(-px), int(-py), self.pixmap
        )
        painter.end()

    # -- bubble -------------------------------------------------------------

    def show_message(
        self,
        text: str,
        duration_ms: int = 5000,
        kind: str = "info",
        title: str = "",
        sound: bool = False,
    ) -> None:
        if not title:
            title = {
                "info": "DeepSeek 娘",
                "approval": "需要授权",
                "question": "DSH 提问",
                "success": "任务完成",
                "error": "提示",
            }.get(kind, "DeepSeek 娘")
        self.bubble.set_content(title, text, kind)
        self.bubble.updateGeometry()
        # Keep bubble inside the window.
        bw = min(self.bubble.sizeHint().width(), self.width() - 16)
        bw = max(200, bw)
        self.bubble.setFixedWidth(bw)
        self.bubble.setFixedHeight(self.bubble.sizeHint().height())
        self.bubble.move(
            max(8, (self.width() - self.bubble.width()) // 2),
            10,
        )
        self.bubble.show()
        self.bubble.raise_()

        if sound:
            self._play_notify_sound()

        self._bubble_timer.stop()
        self._bubble_timer.start(int(duration_ms))

    def _play_notify_sound(self) -> None:
        """通过 Windows 侧播放自定义音乐提示音（WSL 内无需音频库）。"""
        if not self.cfg.get("notify_sound", True):
            return
        wav = os.path.join(BASE_DIR, "assets", "notify.wav")
        if sys.platform == "win32":
            # Windows 原生直接用 winsound 播放，不需要 PowerShell。
            try:
                import winsound
                winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except Exception:
                pass
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", wav], text=True, timeout=3
            ).strip()
            ps = (
                "$p='" + win_path.replace("'", "''") + "'; "
                "(New-Object System.Media.SoundPlayer $p).PlaySync()"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return
        except Exception:
            pass
        # 兜底：如果自定义音乐不可用，用系统提示音。
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[System.Media.SystemSounds]::Asterisk.Play()",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            # Wayland 下不能直接 move()，需要用系统级拖动。
            handle = self.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                handle.startSystemMove()
                event.accept()
                return
            self._dragging = True
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._ask_send_message()
            event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        send = menu.addAction("💬 发送消息给 DSH")
        send.triggered.connect(self._ask_send_message)
        test_approval = menu.addAction("🔐 测试授权提示")
        test_approval.triggered.connect(self._test_approval)
        open_web = menu.addAction("🌐 打开 DSH 网页")
        open_web.triggered.connect(self._open_dsh_web)
        menu.addSeparator()
        quit_act = menu.addAction("🚪 退出")
        quit_act.triggered.connect(self._quit)
        menu.exec(event.globalPos())

    # -- actions --------------------------------------------------------------

    def _test_approval(self) -> None:
        self._on_approval_requested("test-session", "bash", "这是测试授权理由")

    def _ask_send_message(self) -> None:
        if not self.session_id:
            QMessageBox.information(self, "DeepSeek 娘", "还没有可用的 DSH 会话，请先在 DSH 中创建一个会话。")
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "发送给 DSH", "给 DeepSeek 发消息：", ""
        )
        if not ok or not text.strip():
            return
        self._send_prompt(text.strip())

    def _send_prompt(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "DeepSeek 娘", "上一条消息还在发送中，请稍候。")
            return
        self.show_message("正在发送…", 2000, kind="info", title="发送中")
        self._worker = PromptWorker(self.client, self.session_id, text)
        self._worker.done.connect(self._on_prompt_done)
        self._worker.start()

    def _on_prompt_done(self, ok: bool, msg: str) -> None:
        if ok:
            self._set_busy(True)
            self.show_message("已发送，等 DSH 回复中…", 4000, kind="info", title="已发送")
        else:
            self.show_message(msg, 5000, kind="error", title="发送失败", sound=True)

    def _open_dsh_web(self) -> None:
        url = self.cfg.get("dsh_url", "http://127.0.0.1:3080").rstrip("/")
        webbrowser.open(url)

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_visible()

    def _watchdog_timeout(self) -> None:
        if not self._dsh_ok:
            self._quit()

    def _quit(self) -> None:
        self.monitor.stop()
        self.event_stream.stop()
        self.monitor.wait(3000)
        self.cfg["position"] = {"x": self.x(), "y": self.y()}
        save_config(self.cfg)
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing the frameless window hides to tray instead of quitting.
        event.ignore()
        self.hide()

    # -- autostart -----------------------------------------------------------

    @staticmethod
    def _autostart_enabled() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            finally:
                winreg.CloseKey(key)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _toggle_autostart(self, checked: bool) -> None:
        if sys.platform != "win32":
            QMessageBox.information(self, "DeepSeek 娘", "开机自启仅支持 Windows。")
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            try:
                if checked:
                    exe = sys.executable
                    script = os.path.abspath(__file__)
                    cmd = f'"{exe}" "{script}"'
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
            self.show_message("✅ 已开启开机自启" if checked else "已关闭开机自启", 3000)
        except Exception as e:
            QMessageBox.warning(self, "DeepSeek 娘", f"设置开机自启失败：{e}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setDesktopFileName("seekmaid-pet")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SeekMaidPet")
        except Exception:
            pass
        ico = os.path.join(BASE_DIR, "assets", "deepseek_girl.ico")
        if os.path.exists(ico):
            app.setWindowIcon(QIcon(ico))

    # Single instance
    lock = QLockFile(os.path.join(tempfile.gettempdir(), f"{APP_NAME}.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "DeepSeek 娘", "桌宠已经在运行了。")
        return 0

    cfg = load_config()
    pet = PetWindow(cfg)
    pet.show()
    pet.raise_()

    # 放到屏幕中央附近，确保 WSLg 下一定能看到
    screen = app.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        pet.move(
            avail.x() + (avail.width() - pet.width()) // 2,
            avail.y() + (avail.height() - pet.height()) // 2,
        )
    pet.activateWindow()
    pet.raise_()

    print(
        f"[dsh-pet] window visible={pet.isVisible()} geometry={pet.geometry().getRect()}",
        file=sys.stderr,
        flush=True,
    )

    # 启动后稍等片刻，让 WSLg 窗口在 Windows 侧注册完成，再设为 TOPMOST。
    QTimer.singleShot(1500, pet._initial_windows_setup)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
