#!/usr/bin/env bash
# WSLg 下用 X11 后端启动桌宠，置顶行为更可靠
cd "$(dirname "$0")"
export QT_QPA_PLATFORM=xcb
export LD_LIBRARY_PATH="$(pwd)/wslg-xcb-libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec ./.venv/bin/python deepseek_pet.py
