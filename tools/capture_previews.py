#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate preview screenshots/GIF for the SeekMaid pet README.

Run on Windows side with the project venv:
    .venv\\Scripts\\python.exe tools\\capture_previews.py

Output goes to docs/screenshots/.
"""

import os
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

# Make sure the project root is importable.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import deepseek_pet
from deepseek_pet import PetWindow


def make_background(w: int = 800, h: int = 600) -> QPixmap:
    pix = QPixmap(w, h)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0, QColor("#1B2A4A"))
    grad.setColorAt(1, QColor("#0F1626"))
    p.fillRect(0, 0, w, h, grad)
    p.setPen(QColor(255, 255, 255, 45))
    p.setFont(QFont("Microsoft YaHei", 14))
    p.drawText(30, h - 30, "SeekMaid 女仆 · DeepSeek Harness 桌宠")
    p.end()
    return pix


def composite(pet: PetWindow, path: str, bg_w: int = 800, bg_h: int = 600) -> None:
    bg = make_background(bg_w, bg_h)
    p = QPainter(bg)
    pet_pix = pet.grab()
    x = (bg_w - pet_pix.width()) // 2
    y = bg_h - pet_pix.height() - 60
    p.drawPixmap(x, y, pet_pix)
    p.end()
    bg.save(path)
    print("saved", path)


def main() -> None:
    app = QApplication(sys.argv)

    cfg = deepseek_pet.load_config()
    cfg["notify_sound"] = False
    cfg["position"] = {"x": 100, "y": 100}

    pet = PetWindow(cfg)
    pet.show()
    app.processEvents()
    time.sleep(1.0)

    os.makedirs("docs/screenshots", exist_ok=True)

    # 待机
    composite(pet, "docs/screenshots/idle.png")

    # 任务开始
    pet._on_session_started("preview", "开发 SeekMaid 女仆")
    app.processEvents()
    time.sleep(0.6)
    composite(pet, "docs/screenshots/task_start.png")

    # 任务完成
    pet._on_session_finished("preview", "开发 SeekMaid 女仆")
    app.processEvents()
    time.sleep(0.6)
    composite(pet, "docs/screenshots/task_finish.png")

    # 需要授权
    pet._on_approval_requested("preview", "bash", "需要确认授权")
    app.processEvents()
    time.sleep(0.6)
    composite(pet, "docs/screenshots/approval.png")

    # DSH 提问
    pet._on_question_requested("preview", [{"question": "是否继续执行？"}])
    app.processEvents()
    time.sleep(0.6)
    composite(pet, "docs/screenshots/question.png")

    # 出错
    pet._on_event_received("preview", {"type": "agent/error", "data": {}})
    app.processEvents()
    time.sleep(0.6)
    composite(pet, "docs/screenshots/error.png")

    # 待机 GIF
    try:
        from PIL import Image
        frames = []
        tmp = os.path.join(ROOT, "docs", "screenshots", "_tmp_frames")
        os.makedirs(tmp, exist_ok=True)
        for i in range(24):
            app.processEvents()
            pet.update()
            frame_path = os.path.join(tmp, f"frame_{i:02d}.png")
            pet.grab().save(frame_path)
            frames.append(Image.open(frame_path).convert("RGBA"))
            time.sleep(0.05)
        gif_path = os.path.join(ROOT, "docs", "screenshots", "idle.gif")
        frames[0].save(
            gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=50,
            loop=0,
            disposal=2,
        )
        print("saved", gif_path)
        # cleanup temp frames
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    except Exception as e:  # noqa: BLE001
        print("GIF generation failed:", e)

    pet._quit()


if __name__ == "__main__":
    main()
