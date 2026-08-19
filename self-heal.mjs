#!/usr/bin/env node
/**
 * seekmaid-pet self-heal — DSH 升级 / 重装 node_modules 后的幂等自愈。
 *
 * 背景(2026-08-20 事故):DSH 升级时重生成 node_modules,把本地插件
 * seekmaid-pet 一起清掉;此时 web profile 的 package.json 仍把
 * `seekmaid-pet` 列为 bundle,DSH 启动即报:
 *   cannot resolve profile bundle "seekmaid-pet" ... run 'dsh plugin
 *   --profile web install'
 * 桌宠随之失联——只能人工重新装回。
 *
 * 本脚本把"检查 + 重打"固化成启动自愈(与 dsh-aux / dsh-thinking-zh 同款模式):
 *   1. 重建部署 node_modules 下的 seekmaid-pet:
 *        缺失 → 从本仓库建 symlink;
 *        之前是 npm file: 安装留下的实体目录 → 转成 symlink(跟随仓库,升级不再丢);
 *   2. 确保 DSH profile(默认 web)的 package.json:
 *        dependencies["seekmaid-pet"]         = file:<repo>
 *        dsh.profile.bundles 包含 "seekmaid-pet"
 *      (DSH 升级重写 profile 时可能清掉这两处,这里幂等补回);
 *   3. 不写 cordis.patch.yml —— seekmaid-pet 走 bundle 机制加载,手动写
 *      patch 会造成 loader 重复行(之前踩过坑)。
 *
 * 用法:
 *   node self-heal.mjs            # 实际自愈(写盘)
 *   node self-heal.mjs --dry-run  # 只报告会做什么,不写盘
 *
 * 被 ~/dsh/start-dsh.sh 在启动 DSH 前调用(与 aux/thinking 同列);失败不致命。
 */
import {
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url)); // <repo> 根
const REPO = resolve(HERE);
const DRY = process.argv.includes("--dry-run");

function log(msg) {
  console.log(`[seekmaid-pet-self-heal] ${msg}`);
}

/** 探测 DSH 部署根(与 dsh-aux / dsh-thinking-zh 一致)。 */
function detectDshRoot() {
  if (process.env.DSH_ROOT) return process.env.DSH_ROOT;
  const home = process.env.HOME;
  const candidates = [join(home, "dsh"), join(home, ".local/share/dsh"), "/opt/dsh"];
  for (const c of candidates) {
    if (existsSync(join(c, "node_modules/@deepseek-ai"))) return c;
  }
  return null;
}

/** 需要自愈的 DSH profile 目录列表(web 优先,其它引用过 seekmaid 的也一并处理)。 */
function detectProfiles() {
  const home = process.env.HOME;
  const results = [];
  const seen = new Set();
  const add = (dir) => {
    const r = resolve(dir);
    if (!seen.has(r)) {
      seen.add(r);
      results.push(r);
    }
  };

  const profilesRoot = join(home, ".dsh", "profiles");
  const web = join(profilesRoot, "web");
  if (existsSync(join(web, "package.json"))) add(web);

  if (existsSync(profilesRoot)) {
    for (const name of readdirSync(profilesRoot)) {
      const dir = join(profilesRoot, name);
      const pj = join(dir, "package.json");
      if (!existsSync(pj)) continue;
      try {
        if (readFileSync(pj, "utf8").includes("seekmaid-pet")) add(dir);
      } catch {
        // 解析失败不影响其它 profile
      }
    }
  }
  return results;
}

/** 保证部署 node_modules 下的 seekmaid-pet 指向本仓库。 */
function ensurePackageSymlink(root) {
  const target = join(root, "node_modules", "seekmaid-pet");
  if (existsSync(target)) {
    let st;
    try {
      st = lstatSync(target);
    } catch {
      st = null;
    }
    if (st && st.isSymbolicLink()) {
      try {
        if (realpathSync(target) === REPO) {
          log(`symlink 已就位,跳过: ${target}`);
          return;
        }
        log(`symlink 指向他处(${realpathSync(target)}),保留用户配置(不覆盖)`);
        return;
      } catch {
        log(`symlink 读取失败,跳过: ${target}`);
        return;
      }
    }
    if (st && st.isDirectory()) {
      // npm file: 安装留下的实体目录 —— 转成 symlink 才能跟着仓库走。
      log(`检测到实体目录(npm file: 安装拷贝),转为 symlink: ${target}`);
      if (DRY) {
        log(`[dry-run] 将替换为 symlink -> ${REPO}`);
        return;
      }
      rmSync(target, { recursive: true, force: true });
      mkdirSync(dirname(target), { recursive: true });
      symlinkSync(REPO, target, "dir");
      log(`已转为 symlink: ${target} -> ${REPO}`);
      return;
    }
    log(`未知类型,跳过: ${target}`);
    return;
  }
  if (DRY) {
    log(`[dry-run] 将创建 symlink: ${target} -> ${REPO}`);
    return;
  }
  mkdirSync(dirname(target), { recursive: true });
  symlinkSync(REPO, target, "dir");
  log(`已创建 symlink: ${target} -> ${REPO}`);
}

/** 保证一个 DSH profile 的 package.json 里 seekmaid-pet 依赖与 bundle 都在。 */
function ensureProfileJson(profileDir) {
  const pkgPath = join(profileDir, "package.json");
  if (!existsSync(pkgPath)) {
    log(`profile 无 package.json,跳过: ${profileDir}`);
    return;
  }
  let pkg;
  try {
    pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
  } catch (error) {
    log(`profile package.json 解析失败,跳过(${profileDir}): ${error?.message ?? error}`);
    return;
  }

  let changed = false;

  // 1) dependencies 里的 file: 依赖
  if (!pkg.dependencies || typeof pkg.dependencies !== "object") {
    if (!DRY) pkg.dependencies = {};
    changed = true;
  }
  if (!pkg.dependencies || pkg.dependencies["seekmaid-pet"] !== `file:${REPO}`) {
    if (DRY) {
      log(`[dry-run] 将写入 dependencies["seekmaid-pet"] = file:${REPO}`);
    } else {
      pkg.dependencies["seekmaid-pet"] = `file:${REPO}`;
      log(`已写入 dependencies["seekmaid-pet"] = file:${REPO}`);
    }
    changed = true;
  }

  // 2) dsh.profile.bundles 里的 seekmaid-pet
  if (!pkg.dsh || typeof pkg.dsh !== "object") {
    if (!DRY) pkg.dsh = {};
    changed = true;
  }
  if (!pkg.dsh.profile || typeof pkg.dsh.profile !== "object") {
    if (!DRY) pkg.dsh.profile = {};
    changed = true;
  }
  if (!Array.isArray(pkg.dsh.profile.bundles)) {
    if (!DRY) pkg.dsh.profile.bundles = [];
    changed = true;
  }
  if (!Array.isArray(pkg.dsh.profile.bundles) || !pkg.dsh.profile.bundles.includes("seekmaid-pet")) {
    if (DRY) {
      log(`[dry-run] 将向 dsh.profile.bundles 追加 "seekmaid-pet"`);
    } else {
      pkg.dsh.profile.bundles.push("seekmaid-pet");
      log(`已向 dsh.profile.bundles 追加 "seekmaid-pet"`);
    }
    changed = true;
  }

  if (!changed) {
    log(`profile 已就绪,无需修改: ${profileDir}`);
    return;
  }
  if (DRY) {
    log(`[dry-run] 将备份并写回 ${pkgPath}`);
    return;
  }
  const bak = `${pkgPath}.bak-seekmaid-${Date.now()}`;
  copyFileSync(pkgPath, bak);
  writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`, "utf8");
  log(`已写回 ${pkgPath}(备份 ${bak})`);
}

function main() {
  const root = detectDshRoot();
  if (!root) {
    log("未找到 DSH 部署根(跳过自愈)");
    return;
  }
  log(`DSH 根: ${root} (${DRY ? "dry-run" : "实际修复"})`);

  const step = (name, fn) => {
    try {
      fn();
    } catch (error) {
      log(`${name} 失败(继续): ${error?.message ?? error}`);
    }
  };

  step("node_modules symlink", () => ensurePackageSymlink(root));

  const profiles = detectProfiles();
  if (profiles.length === 0) {
    log("未找到需要自愈的 DSH profile(dry-run 也如此报告)");
  }
  for (const profile of profiles) {
    step(`profile(${profile})`, () => ensureProfileJson(profile));
  }

  log(DRY ? "dry-run 完成(未写盘)" : "自愈完成。若本次有修复,请重启 DSH 生效。");
}

main();
