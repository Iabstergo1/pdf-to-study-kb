// 用法：node src/run-vision.ts <apikey|chatgpt|custom> <provider/model-id>   （环境变量 KB_TRIALS 默认 3）
// 做什么：每轮生成含随机 4 位数字的 PNG → 新开一个会话，让模型通过 read 工具读出来（即入库实际走的「工具返回图片」路径）。
// 判定：每一轮都必须「调用了 read、工具结果里带图片、答案与数字完全一致」；全部轮次通过才算 pass。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { RESULTS_FILE, configDir } from "./config.ts";
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";
import { randomCode, renderCodePng } from "./png.ts";

const [label, spec] = process.argv.slice(2) as [AuthLabel, string];
if (!label || !spec) throw new Error("usage: run-vision.ts <apikey|chatgpt|custom> <provider/model-id>");
const trials = Number(process.env.KB_TRIALS ?? 3);

const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
const agentDir = path.join(configDir(), "pi-agent");

async function trial() {
  const code = randomCode();
  const png = renderCodePng(code);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kb-vision-"));
  fs.writeFileSync(path.join(dir, "check.png"), png);
  const loader = new DefaultResourceLoader({
    cwd: dir, agentDir, noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    systemPrompt: "You are a careful assistant. Use the tools you are given when asked.",
  });
  await loader.reload();
  const { session } = await createAgentSession({
    cwd: dir, agentDir, model, thinkingLevel: "off", modelRuntime: rt, resourceLoader: loader, tools: ["read"],
    sessionManager: SessionManager.inMemory(dir),
    settingsManager: SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: true, maxRetries: 1 } }),
  });
  let readCalled = false;
  let toolImage = false;
  let imageInfo: { mimeType?: string; base64Bytes: number } | undefined;
  session.subscribe((e: any) => {
    if (e.type === "tool_execution_end" && e.toolName === "read") {
      readCalled = true;
      const img = Array.isArray(e.result?.content) ? e.result.content.find((b: any) => b.type === "image") : undefined;
      toolImage = !!img;
      if (img) imageInfo = { mimeType: img.mimeType, base64Bytes: String(img.data ?? "").length };
    }
  });
  const t0 = Date.now();
  let error: string | undefined;
  const timer = setTimeout(() => {
    error = "timeout after 180s";
    void session.abort();
  }, 180_000);
  try {
    await session.prompt("Use the read tool to open check.png. It shows a 4-digit number. Reply with ONLY those 4 digits and nothing else.");
  } catch (e) {
    error = String(e);
  } finally {
    clearTimeout(timer);
  }
  const answer = (session.getLastAssistantText() ?? "").trim();
  let stats: any;
  try { stats = session.getSessionStats(); } catch { /* 可选 */ }
  session.dispose();
  return {
    pass: !error && readCalled && toolImage && answer === code,
    readCalled, toolImage, code, answer, error, ms: Date.now() - t0, sourcePngBytes: png.length, imageInfo,
    tokens: stats?.tokens?.total, cost: stats?.cost,
  };
}

const rounds = [];
for (let i = 0; i < trials; i++) rounds.push(await trial());
const result = {
  at: new Date().toISOString(), kind: "vision", path: label, model: spec, fixture: "regular-font",
  pass: rounds.every((r) => r.pass), passed: rounds.filter((r) => r.pass).length, trials, rounds,
};
fs.mkdirSync(path.dirname(RESULTS_FILE), { recursive: true });
fs.appendFileSync(RESULTS_FILE, JSON.stringify(result) + "\n");
console.log(JSON.stringify(result, null, 2));
