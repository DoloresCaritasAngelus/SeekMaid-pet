# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-08-20

### Added

- `self-heal.mjs`：DSH 升级 / 重装 node_modules 后的幂等自愈脚本。
  - 部署 node_modules 下 `seekmaid-pet` 缺失或为实体拷贝时，自动重建/转为仓库 symlink；
  - 确保 DSH profile 的 `package.json` 里 `seekmaid-pet` 依赖与 `dsh.profile.bundles` 声明都在；
  - 支持 `--dry-run`（只报告不写盘），与 dsh-aux / dsh-thinking-zh 的自愈脚本同款模式。
- `start-dsh.sh` 接线：DSH 启动前自动运行 seekmaid-pet self-heal，升级后不再出现
  `cannot resolve profile bundle "seekmaid-pet"` 崩溃。

### Changed

- 版本号升至 0.3.0。

## [0.2.0] - 2026-08-16

### Added

- 真正零配置自动部署：插件自动复制项目到 Windows、自动创建 venv、自动安装 PySide6、自动启动桌宠。
- `setup_windows.bat`：Windows 侧一键初始化。
- `AI.md`：面向 AI 代理的部署指南。
- 项目结构说明、兼容性说明。
- 预览截图 / GIF（`docs/screenshots/`）。
- MIT License。

### Changed

- 插件更名为 `seekmaid-pet`。
- Windows 项目路径改为自动探测，不再写死用户目录。
- 动画定时器从 33ms 优化到 50ms，降低 CPU 占用。
- `dsh_url` 默认改为 `http://localhost:3080`。

### Removed

- 移除未使用的 `deepseek_sprite.png`。

## [0.1.0] - 2026-08-15

### Added

- 初版：DeepSeek 娘桌宠，支持 DSH 任务监控、授权/QA 提示、自定义提示音、人设对话。
- Windows 原生桌宠 + DSH 插件自动启动。
