# AI 部署指南：SeekMaid 女仆（seekmaid-pet）

> 本文档面向 **AI 代理**（Claude / Cursor / OpenCode / DeepSeek 等），
> 目标是让 AI 能直接帮用户在 DSH 环境中安装、配置、验证 SeekMaid 女仆桌宠插件。

## 0. 你需要知道

- **SeekMaid 女仆** 是一个 DSH 插件 + Windows 原生桌宠。
- DSH 通常跑在 **WSL/Linux** 里，桌宠跑在 **Windows 11 原生侧**。
- Windows 侧通过 `http://localhost:3080` 访问 WSL 里的 DSH。
- 插件包名：`seekmaid-pet`。

## 1. 环境检测

先判断用户环境：

```sh
# DSH 是否在 WSL/Linux？
uname -a | grep -i microsoft   # 有输出 => WSL

# DSH 是否在运行？
curl -s -m 3 -X POST http://localhost:3080/api/session.list \
  -H 'Content-Type: application/json' \
  -d '{"type":"client-request","rpcId":"ai-check","method":"session.list","payload":{}}' | head -c 100

# Windows 侧是否有 Python？
powershell.exe -NoProfile -Command "python --version" 2>/dev/null

# Windows 侧是否已有项目目录？
ls -d /mnt/c/Users/*/seekmaid-pet 2>/dev/null
```

## 2. 安装步骤

### 2.1 Windows 侧准备

**零配置模式**：从 0.2.0 开始，插件在 WSL 的 DSH 中启动时会自动检测 Windows 侧项目：

- 如果 `C:\Users\<user>\seekmaid-pet` 不存在，会自动从 WSL 仓库复制过去；
- 如果 `.venv` 不存在，会自动创建并安装 PySide6；
- 安装完成后自动启动 Windows 桌宠。

不需要手动复制或运行 `setup_windows.bat`。

如果希望手动准备，也可以：

1. 把本仓库复制到 Windows，例如：
   ```powershell
   Copy-Item -Recurse "\\wsl.localhost\Ubuntu\home\<user>\...\seekmaid-pet" "C:\Users\<user>\seekmaid-pet"
   ```
2. 初始化 Windows 环境：
   ```powershell
   cd C:\Users\<user>\seekmaid-pet
   .\setup_windows.bat
   ```
3. 手动测试桌宠：
   ```powershell
   .\run_pet.bat
   ```
   应出现窗口标题 `SeekMaid 女仆`。

### 2.2 安装 DSH 插件（WSL 侧）

在 DSH 所在环境执行：

```sh
dsh plugin --profile web add file:/path/to/seekmaid-pet
```

如果 `dsh plugin` 不可用，手动接线：

```sh
ln -sfn /path/to/seekmaid-pet "$DSH_HOME/../dsh/node_modules/seekmaid-pet"
```

然后在 `~/.dsh/profiles/web/cordis.patch.yml` 追加：

```yaml
- insert:
    - id: seekmaid
      name: 'seekmaid-pet'
```

> 注意：如果插件已经通过 bundle 层自动加载，**不要重复添加同一 id**，否则会报 `duplicate loader entry id`。

### 2.3 配置 Windows 项目路径

如果插件默认探测不到 Windows 路径，在 DSH 插件配置里显式指定：

```yaml
- id: seekmaid
  name: 'seekmaid-pet'
  config:
    windowsProject: C:\Users\<user>\seekmaid-pet
```

### 2.4 重启 DSH

```sh
# 重启 DSH web
# 用户重启或 AI 按环境重启
```

重启后插件会自动拉起 Windows 原生桌宠。

## 3. 验证

```sh
# 1) 插件可加载
node --input-type=module -e "const m = await import('seekmaid-pet'); console.log(m.name, typeof m.apply)"

# 2) DSH 配置包含 seekmaid
dsh --profile web --dump-config 2>/dev/null | grep -A1 'id: seekmaid' | head -5

# 3) Windows 桌宠进程存在
powershell.exe -NoProfile -Command "Get-Process | Where-Object { \$_.MainWindowTitle -like '*SeekMaid*' } | Select-Object Id,ProcessName,MainWindowTitle"
```

期望：

```text
name = seekmaid-pet
apply = function
窗口标题 = SeekMaid 女仆
```

## 4. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `duplicate loader entry id: seekmaid` | bundle 已自动加载 + 手动 patch 重复 | 删除手动 patch 里的 seekmaid 块 |
| Windows 桌宠没启动 | `windowsProject` 路径不对 | 配置 `windowsProject` 指向实际路径 |
| `pythonw` 找不到 | Windows 未安装 Python 或未初始化 venv | 运行 `setup_windows.bat` |
| 连不上 DSH | DSH 不在 WSL 或端口不同 | 检查 `dsh_url`，WSL2 下用 `localhost` |
| 任务栏图标是 Python | 未使用 `.ico` 或图标缓存 | 确认 `assets/deepseek_girl.ico` 存在并重启桌宠 |
| 桌宠没有置顶 | 旧 WSLg 版本问题 | 确保使用 Windows 原生版，而不是 WSLg 启动 |

## 5. 维护建议

- 每次修改代码后，在仓库内执行：
  ```sh
  python3 -m py_compile deepseek_pet.py dsh_monitor.py dsh_client.py
  node --check src/index.js
  ```
- 保持 `README.md` 和 `AI.md` 同步更新。
- 发布前运行一次完整验证流程。
