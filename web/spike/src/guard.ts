import fs from "node:fs";
import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

export type Verdict = { ok: true } | { ok: false; reason: string };

export interface GuardOptions {
  /** 工作区根：agent 相对路径的锚点。 */
  cwd: string;
  /** 允许读取的根（真实路径比较）。 */
  readRoots: string[];
  /** 无论如何不许碰的根（凭据目录）。 */
  denyRoots: string[];
  /** 写入白名单：参数是已解析的真实绝对路径。 */
  writeAllowed: (absPath: string) => boolean;
  /** `pipeline` 工具允许的子命令。 */
  pipelineAllow: ReadonlySet<string>;
}

/** 入库会话允许的 pipeline 子命令（不含 retract/reset/staging-clean/adopt/reuse/reseal/revise/reopen/skill-*）。 */
export const INGEST_PIPELINE_ALLOW: ReadonlySet<string> = new Set([
  "add-source", "profile", "source-convert", "source-audit", "arbitration-status", "arbitration-apply",
  "windows", "workorder", "preflight-eval", "ingest-start", "ingest-done", "show-window",
  "window-start", "window-done", "window-fail", "resolve-concept", "check-write", "snapshot-page",
  "lint", "next", "status", "ingest-stats", "sync-assets", "sync-overview-sources", "check-session",
]);

const DENY_FLAGS = new Set(["--dev-bypass", "--force"]);

/** 取最近一个已存在祖先的真实路径再拼回尾部：既能处理还不存在的新文件，又能拆穿 junction / symlink。 */
function realResolve(abs: string): string {
  let cur = abs;
  const tail: string[] = [];
  while (!fs.existsSync(cur)) {
    const parent = path.dirname(cur);
    if (parent === cur) break;
    tail.unshift(path.basename(cur));
    cur = parent;
  }
  const real = fs.existsSync(cur) ? fs.realpathSync.native(cur) : cur;
  return path.join(real, ...tail);
}

export function inside(root: string, p: string): boolean {
  const rel = path.relative(realResolve(root), p);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

const deny = (reason: string): Verdict => ({ ok: false, reason });

export function checkPath(kind: "read" | "write", rawPath: unknown, o: GuardOptions): Verdict {
  if (typeof rawPath !== "string" || rawPath === "") return deny("a path is required");
  if (rawPath.includes("\0")) return deny("invalid path");
  if (rawPath.startsWith("~") || rawPath.startsWith("@")) return deny("use a plain relative path inside the workspace");
  const abs = realResolve(path.resolve(o.cwd, rawPath));
  if (o.denyRoots.some((d) => inside(d, abs))) return deny("that location is not accessible");
  if (abs.split(path.sep).includes(".git")) return deny("the .git directory is not accessible");
  if (kind === "read") {
    return o.readRoots.some((r) => inside(r, abs)) ? { ok: true } : deny("outside the readable area");
  }
  return o.writeAllowed(abs) ? { ok: true } : deny("writes are only allowed under wiki/, the staging digest.md and the arbitration decisions.json");
}

export function checkPipelineArgs(args: unknown, allow: ReadonlySet<string>): Verdict {
  if (!Array.isArray(args) || args.length === 0 || args.some((a) => typeof a !== "string")) {
    return deny("args must be a non-empty array of strings");
  }
  const [cmd, ...rest] = args as string[];
  if (!allow.has(cmd)) return deny(`subcommand "${cmd}" is not allowed`);
  if (rest.some((a) => a.includes("\0") || a.includes("\n") || a.includes("\r"))) return deny("invalid argument");
  const bad = rest.find((a) => DENY_FLAGS.has(a));
  if (bad) return deny(`flag ${bad} is not allowed`);
  return { ok: true };
}

export function defaultGuardOptions(o: { workspaceRoot: string; repoRoot: string; credentialsDir: string }): GuardOptions {
  const ws = o.workspaceRoot;
  return {
    cwd: ws,
    readRoots: [ws, o.repoRoot],
    denyRoots: [o.credentialsDir],
    writeAllowed: (abs) => {
      if (inside(path.join(ws, "wiki"), abs)) return true;
      // staging 里只放行两个 agent 必须写的文件：<src>/digest.md、<src>/arbitration/decisions.json
      const rel = path.relative(realResolve(path.join(ws, "pipeline-workspace", "staging")), abs);
      if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) return false;
      const parts = rel.split(path.sep);
      return (parts.length === 2 && parts[1] === "digest.md") ||
        (parts.length === 3 && parts[1] === "arbitration" && parts[2] === "decisions.json");
    },
    pipelineAllow: INGEST_PIPELINE_ALLOW,
  };
}

/** pi 扩展：拦截每一次工具调用；除 read/write/edit/pipeline 外一律阻止；处理器出错时 pi 也会阻止（fail-safe）。 */
export function guardExtension(o: GuardOptions): (pi: ExtensionAPI) => void {
  return (pi) => {
    pi.on("tool_call", async (event) => {
      let v: Verdict;
      if (isToolCallEventType("read", event)) v = checkPath("read", event.input.path, o);
      else if (isToolCallEventType("write", event)) v = checkPath("write", event.input.path, o);
      else if (isToolCallEventType("edit", event)) v = checkPath("write", event.input.path, o);
      else if (event.toolName === "pipeline") v = checkPipelineArgs((event.input as { args?: unknown }).args, o.pipelineAllow);
      else v = deny(`tool "${event.toolName}" is not available`);
      return v.ok ? undefined : { block: true, reason: `not allowed: ${v.reason}` };
    });
  };
}
