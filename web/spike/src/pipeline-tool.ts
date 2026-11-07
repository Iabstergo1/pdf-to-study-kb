import { spawn } from "node:child_process";
import { Type } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { checkPipelineArgs } from "./guard.ts";

export interface PipelineToolOptions {
  /** Python 解释器（study-kb 环境）。 */
  python: string;
  /** scripts/pipeline.py 的路径。 */
  script: string;
  /** STUDY_KB_ROOT：状态库 / wiki / staging 所在的工作区根。 */
  workspaceRoot: string;
  /** 子进程 cwd：仓库根（相对 --path 的锚点）。 */
  repoRoot: string;
  allow: ReadonlySet<string>;
  /** 返回给模型的最大字符数，默认 60000。 */
  maxOutputChars?: number;
  /** 单条命令超时，默认 30 分钟。 */
  timeoutMs?: number;
}

function run(o: PipelineToolOptions, args: string[], signal?: AbortSignal): Promise<{ code: number; text: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(o.python, [o.script, ...args], {
      cwd: o.repoRoot,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", STUDY_KB_ROOT: o.workspaceRoot },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let text = "";
    const onData = (b: Buffer) => { text += b.toString("utf8"); };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    const timer = setTimeout(() => child.kill(), o.timeoutMs ?? 30 * 60_000);
    const onAbort = () => child.kill();
    signal?.addEventListener("abort", onAbort, { once: true });
    child.on("error", (e) => { clearTimeout(timer); reject(e); });
    child.on("close", (code) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve({ code: code ?? 1, text });
    });
  });
}

export function createPipelineTool(o: PipelineToolOptions) {
  const max = o.maxOutputChars ?? 60_000;
  return defineTool({
    name: "pipeline",
    label: "pipeline",
    description:
      "Run one `scripts/pipeline.py` subcommand of the study knowledge base and return its output. " +
      "`args` is the argument list WITHOUT the python executable or the script path, e.g. " +
      '["show-window", "--source", "my-book", "--window", "w0003"]. Only the ingest-related subcommands are allowed.',
    promptSnippet: "pipeline: run a scripts/pipeline.py subcommand (args array, no shell)",
    parameters: Type.Object({
      args: Type.Array(Type.String(), { description: 'Subcommand and its arguments, e.g. ["status"]' }),
    }),
    async execute(_toolCallId, params, signal) {
      const v = checkPipelineArgs(params.args, o.allow);
      if (!v.ok) throw new Error(`Refused: not allowed: ${v.reason}`);
      const { code, text } = await run(o, params.args, signal);
      let out = text;
      if (out.length > max) out = `${out.slice(0, max)}\n[... truncated ${out.length - max} chars ...]`;
      if (code !== 0) throw new Error(`exit code ${code}\n${out}`);
      return { content: [{ type: "text", text: out }], details: { code } };
    },
  });
}
