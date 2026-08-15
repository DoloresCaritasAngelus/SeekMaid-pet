/**
 * seekmaid-pet: DeepSeek 娘桌面宠物 — DSH 启动联动插件。
 *
 * 这是一个纯 host 端 DSH 插件：DSH 启动时自动拉起 Python 桌宠进程，
 * DSH 停止/插件卸载时自动关闭桌宠进程。
 *
 * 桌宠本身会：
 * - 连接 DSH 本地 Web API / WebSocket，监控任务状态并气泡提示；
 * - 如果启动后 N 分钟内发现 DSH 未启动/不可达，自动退出（默认 3 分钟）。
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const name = "seekmaid-pet";
export const inject = [];

function commandExists(cmd) {
  const probe = spawnSync(process.platform === "win32" ? "where" : "which", [cmd], {
    stdio: "ignore",
    windowsHide: true,
  });
  return probe.status === 0;
}

function resolvePython(packageRoot) {
  if (process.env.DSH_PET_PYTHONW) return process.env.DSH_PET_PYTHONW;
  if (process.env.DSH_PET_PYTHON) return process.env.DSH_PET_PYTHON;

  // Prefer the project-local virtualenv if it exists (contains PySide6).
  const venvPython = process.platform === "win32"
    ? path.join(packageRoot, ".venv", "Scripts", "pythonw.exe")
    : path.join(packageRoot, ".venv", "bin", "python");
  if (existsSync(venvPython)) return venvPython;

  if (process.platform === "win32") {
    // pythonw: 无控制台窗口，适合桌宠长期运行
    if (commandExists("pythonw")) return "pythonw";
    if (commandExists("python")) return "python";
    return "py";
  }
  if (commandExists("python3")) return "python3";
  return "python";
}

function defaultWindowsProject() {
  if (process.env.USERPROFILE) {
    return process.env.USERPROFILE + "\\seekmaid-pet";
  }
  try {
    const out = spawnSync("cmd.exe", ["/c", "echo %USERPROFILE%"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (out.status === 0 && out.stdout) {
      const profile = out.stdout.trim().split(/\r?\n/)[0];
      if (profile) return profile + "\\seekmaid-pet";
    }
  } catch {
    // ignore
  }
  const user = process.env.USER || "user";
  return `C:\\Users\\${user}\\seekmaid-pet`;
}

function wslPathOfWindowsProject(winProject) {
  // C:\Users\<user>\seekmaid-pet -> /mnt/c/Users/<user>/seekmaid-pet
  return "/mnt/c/" + winProject.replace(/\\/g, "/").replace(/^[A-Z]:\//, "");
}

function autoSetupWindows(ctx, winProject, packageRoot) {
  // 把 WSL 项目路径转成 Windows 可访问的 UNC 路径
  let srcWin = "";
  try {
    const out = spawnSync("wslpath", ["-w", packageRoot], { encoding: "utf8" });
    if (out.status === 0 && out.stdout) srcWin = out.stdout.trim();
  } catch {
    // ignore
  }
  if (!srcWin) {
    ctx.logger.warn(`[seekmaid-pet] cannot resolve WSL source path for auto setup`);
    return;
  }

  const ps = `
$ErrorActionPreference = 'Continue'
$src = '${srcWin.replace(/'/g, "''")}'
$dst = '${winProject.replace(/'/g, "''")}'
New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /E /XD .venv .git wslg-xcb-libs __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
Set-Location $dst
if (!(Test-Path '.venv\\Scripts\\python.exe')) {
  if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
& '.venv\\Scripts\\python.exe' -m pip install --upgrade pip
& '.venv\\Scripts\\python.exe' -m pip install -r requirements.txt
Start-Process -FilePath "$dst\\.venv\\Scripts\\pythonw.exe" -ArgumentList "$dst\\deepseek_pet.py" -WorkingDirectory $dst
`;
  ctx.logger.info(`[seekmaid-pet] auto-setting up Windows pet at ${winProject} ...`);
  const child = spawn("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], {
    stdio: "ignore",
    windowsHide: true,
  });
  child.on("error", (err) => {
    ctx.logger.warn(`[seekmaid-pet] auto setup failed to start: ${err.message}`);
  });
}

function launchWindowsPet(ctx, config, packageRoot) {
  const winProject = config.windowsProject || defaultWindowsProject();
  const wslProbe = wslPathOfWindowsProject(winProject);
  const projectReady = existsSync(wslProbe + "/deepseek_pet.py");
  const venvReady = existsSync(wslProbe + "/.venv/Scripts/pythonw.exe");

  if (!projectReady || !venvReady) {
    autoSetupWindows(ctx, winProject, packageRoot);
    return { setup: true };
  }

  const pythonw = winProject + "\\.venv\\Scripts\\pythonw.exe";
  const script = winProject + "\\deepseek_pet.py";
  const ps = `Start-Process -FilePath '${pythonw}' -ArgumentList '${script}' -WorkingDirectory '${winProject}'`;

  const child = spawn("powershell.exe", ["-NoProfile", "-Command", ps], {
    stdio: "ignore",
    windowsHide: true,
  });
  child.on("error", (err) => {
    ctx.logger.warn(`[seekmaid-pet] failed to start Windows pet: ${err.message}`);
  });
  return child;
}

function killWindowsPet() {
  const ps = "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*deepseek_pet.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }";
  try {
    spawn("powershell.exe", ["-NoProfile", "-Command", ps], {
      stdio: "ignore",
      windowsHide: true,
    });
  } catch {
    // ignore
  }
}

export function apply(ctx, config = {}) {
  if (config.enabled === false) return;

  const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const script = config.script || path.join(packageRoot, "deepseek_pet.py");

  // 优先启动 Windows 原生桌宠（WSLg 显示/置顶问题多）。
  if (process.platform === "linux") {
    const winChild = launchWindowsPet(ctx, config, packageRoot);
    if (winChild) {
      ctx.effect(() => () => killWindowsPet());
      return;
    }
    ctx.logger.info("[seekmaid-pet] Windows pet not found, fallback to WSL pet");
  }

  if (!existsSync(script)) {
    ctx.logger.warn(`[seekmaid-pet] pet script not found: ${script}`);
    return;
  }

  const python = config.python || resolvePython(packageRoot);
  const env = {
    ...process.env,
    DSH_PET_STARTED_BY_DSH: "1",
    DSH_PET_STARTUP_TIMEOUT_MINUTES: String(config.startupTimeoutMinutes ?? 3),
    DSH_WEB_URL: process.env.DSH_WEB_URL || "http://127.0.0.1:3080",
  };

  // WSLg 下优先使用 X11 后端，置顶行为更可靠；本地已附带所需 xcb 库。
  if (process.platform === "linux") {
    const xcbLibDir = path.join(packageRoot, "wslg-xcb-libs");
    if (existsSync(xcbLibDir)) {
      env.QT_QPA_PLATFORM = env.QT_QPA_PLATFORM || "xcb";
      env.LD_LIBRARY_PATH = xcbLibDir + (env.LD_LIBRARY_PATH ? ":" + env.LD_LIBRARY_PATH : "");
    }
  }

  const child = spawn(python, [script], {
    cwd: packageRoot,
    stdio: "ignore",
    windowsHide: true,
    env,
  });

  child.on("error", (err) => {
    ctx.logger.warn(`[seekmaid-pet] failed to start pet: ${err.message}`);
  });

  child.on("exit", (code, signal) => {
    ctx.logger.info(`[seekmaid-pet] pet exited (code=${code}, signal=${signal})`);
  });

  // DSH 停止/插件卸载时杀掉桌宠子进程
  ctx.effect(() => () => {
    try {
      child.kill();
    } catch {
      // already exited
    }
  });
}
