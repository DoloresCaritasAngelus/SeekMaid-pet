#!/usr/bin/env bash
# 安装 dsh-pet 插件到 DSH web profile
set -e
cd "$(dirname "$0")"
dsh plugin --profile web add "file:$(pwd)"
echo "安装成功。请重启 DSH 后桌宠会自动启动。"
