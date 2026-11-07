// 用法：node src/prepare-tiny.ts <工作区目录>    （环境变量 STUDY_KB_PYTHON 指向 Python）
// 做什么：在一次性工作区里跑确定性预处理（不花 AI 额度），产出可入库的 workorder。
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./config.ts";

export const SOURCE_ID = "tiny-demo";
export const DOMAIN = "demo";

export function prepareTinyWorkspace(root: string, python: string): { sourcePath: string } {
  fs.mkdirSync(root, { recursive: true });
  const env = { ...process.env, PYTHONUTF8: "1", STUDY_KB_ROOT: root };
  const cli = (...args: string[]) => {
    console.log(`> pipeline.py ${args.join(" ")}`);
    return execFileSync(python, [path.join(REPO_ROOT, "scripts", "pipeline.py"), ...args], { cwd: REPO_ROOT, env, encoding: "utf8" });
  };
  // agent 要读 skill 引用的 docs/ 与 templates/：用目录联接（junction）指回仓库，真实应用里工作区根就是仓库根。
  for (const d of ["docs", "templates"]) {
    const link = path.join(root, d);
    if (!fs.existsSync(link)) fs.symlinkSync(path.join(REPO_ROOT, d), link, "junction");
  }
  const dest = path.join(root, "books", DOMAIN, "input", "tiny.md");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(path.join(import.meta.dirname, "..", "fixtures", "tiny.md"), dest);
  cli("init-vault");
  cli("add-source", "--source", SOURCE_ID, "--domain", DOMAIN, "--path", dest, "--fmt", "md");
  cli("profile", "--source", SOURCE_ID);
  cli("source-convert", "--source", SOURCE_ID);
  cli("windows", "--source", SOURCE_ID);
  cli("workorder", "--source", SOURCE_ID);
  console.log(cli("status"));
  return { sourcePath: path.join("books", DOMAIN, "input", "tiny.md") };
}

if (import.meta.url === `file:///${process.argv[1]?.replace(/\\/g, "/")}`) {
  const root = process.argv[2];
  if (!root) throw new Error("usage: prepare-tiny.ts <workspace dir>");
  prepareTinyWorkspace(root, process.env.STUDY_KB_PYTHON ?? "python");
}
