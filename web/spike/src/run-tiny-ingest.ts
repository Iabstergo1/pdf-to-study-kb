// 用法：node src/run-tiny-ingest.ts <apikey|chatgpt|custom> <provider/model-id> [工作区目录]
// 做什么：在一次性工作区里预处理小源，再让 agent 按 ingest skill 完整入库，检查是否 published。
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { REPO_ROOT, RESULTS_FILE, configDir } from "./config.ts";
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";
import { DOMAIN, SOURCE_ID, prepareTinyWorkspace } from "./prepare-tiny.ts";
import { ingestUserPrompt } from "./prompt.ts";
import { createIngestSession } from "./session.ts";

const [label, spec, rootArg] = process.argv.slice(2) as [AuthLabel, string, string | undefined];
if (!label || !spec) throw new Error("usage: run-tiny-ingest.ts <apikey|chatgpt|custom> <provider/model-id> [workspace]");
const python = process.env.STUDY_KB_PYTHON ?? "python";
const root = rootArg ?? fs.mkdtempSync(path.join(os.tmpdir(), "kb-tiny-"));

const { sourcePath } = prepareTinyWorkspace(root, python);
const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
const { session } = await createIngestSession({
  modelRuntime: rt, model, workspaceRoot: root, repoRoot: REPO_ROOT, credentialsDir: configDir(), python,
  thinkingLevel: (process.env.KB_THINKING as never) ?? "off",
});

const calls: Record<string, number> = {};
let blocked = 0;
const transcript = path.join(root, "c0-transcript.log");
session.subscribe((e: any) => {
  if (e.type === "tool_execution_start") {
    calls[e.toolName] = (calls[e.toolName] ?? 0) + 1;
    fs.appendFileSync(transcript, `${new Date().toISOString()} ${e.toolName} ${JSON.stringify(e.args).slice(0, 300)}\n`);
  }
  if (e.type === "tool_execution_end" && e.isError && /not allowed/.test(JSON.stringify(e.result))) blocked++;
});

const t0 = Date.now();
let error: string | undefined;
const timer = setTimeout(() => {
  error = "timeout after 25 min";
  void session.abort();
}, 25 * 60_000);
try {
  await session.prompt(ingestUserPrompt({ sourcePath, domain: DOMAIN, sourceId: SOURCE_ID }));
} catch (e) {
  error = String(e);
} finally {
  clearTimeout(timer);
}

let published = false;
let statusText = "";
try {
  statusText = execFileSync(python, [path.join(REPO_ROOT, "scripts", "pipeline.py"), "status", "--json"], {
    cwd: REPO_ROOT, env: { ...process.env, PYTHONUTF8: "1", STUDY_KB_ROOT: root }, encoding: "utf8",
  });
  const s = JSON.parse(statusText).sources.find((x: any) => x.source_id === SOURCE_ID);
  published = s?.current_stage === "lint" && s?.current_status === "published";
} catch (e) {
  statusText = String(e);
}
let stats: unknown;
try { stats = session.getSessionStats(); } catch { /* 可选 */ }
const result = {
  at: new Date().toISOString(), kind: "tiny-ingest", path: label, model: spec, pass: published && !error, published, error,
  ms: Date.now() - t0, toolCalls: calls, blockedByGuard: blocked, workspace: root, stats, lastAssistant: (session.getLastAssistantText() ?? "").slice(0, 400),
};
fs.mkdirSync(path.dirname(RESULTS_FILE), { recursive: true });
fs.appendFileSync(RESULTS_FILE, JSON.stringify(result) + "\n");
console.log(JSON.stringify(result, null, 2));
session.dispose();
