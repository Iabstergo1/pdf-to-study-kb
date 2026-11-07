import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { REPO_ROOT } from "../src/config.ts";
import { renderCodePng } from "../src/png.ts";
import { createIngestSession } from "../src/session.ts";

async function setup() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "faux-"));
  const ws = path.join(base, "ws");
  const creds = path.join(base, "creds");
  fs.mkdirSync(path.join(ws, "wiki"), { recursive: true });
  fs.mkdirSync(path.join(ws, "pipeline-workspace", "staging", "s1"), { recursive: true });
  fs.mkdirSync(creds, { recursive: true });
  fs.writeFileSync(path.join(creds, "auth.json"), "TOP-SECRET-KEY");
  fs.writeFileSync(path.join(ws, "wiki", "check.png"), renderCodePng("4821"));
  const fake = path.join(base, "fake-pipeline.js");
  fs.writeFileSync(fake, "console.log('FAKE-PIPELINE ' + process.argv.slice(2).join(' '));");

  const rt = await ModelRuntime.create({ authPath: path.join(creds, "pi-auth.json"), modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
  const faux = fauxProvider();
  rt.registerNativeProvider(faux.provider);
  const { session, loader } = await createIngestSession({
    modelRuntime: rt,
    model: faux.getModel(),
    workspaceRoot: ws,
    repoRoot: REPO_ROOT,
    credentialsDir: creds,
    python: process.execPath,
    pipelineScript: fake,
    agentDir: path.join(base, "agentdir"),
  });
  const results: { toolName: string; isError: boolean; result: any }[] = [];
  session.subscribe((e: any) => {
    if (e.type === "tool_execution_end") results.push({ toolName: e.toolName, isError: e.isError, result: e.result });
  });
  return { base, ws, creds, faux, session, loader, results };
}

const textOf = (r: any): string => JSON.stringify(r?.content ?? r);

test("active tools are exactly read/write/edit/pipeline (no bash)", async () => {
  const { session } = await setup();
  assert.deepEqual([...session.getActiveToolNames()].sort(), ["edit", "pipeline", "read", "write"]);
  session.dispose();
});

test("only the ingest skill is discovered, from .agents/skills", async () => {
  const { loader, session } = await setup();
  assert.deepEqual(loader.getSkills().skills.map((s: any) => s.name), ["ingest"]);
  session.dispose();
});

test("system prompt carries the web notes and never mentions bash as an available tool", async () => {
  const { session } = await setup();
  assert.match(session.systemPrompt, /Environment notes \(web console\)/);
  assert.match(session.systemPrompt, /pipeline/);
  assert.doesNotMatch(session.systemPrompt, /- bash:/);
  session.dispose();
});

test("an image returned by the read tool is delivered to the model", async () => {
  const { faux, session } = await setup();
  const seen = { image: false };
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall("read", { path: "wiki/check.png" })], { stopReason: "toolUse" }),
    (context: any) => {
      const last = context.messages.at(-1);
      seen.image = last?.role === "toolResult" && last.content.some((b: any) => b.type === "image");
      return fauxAssistantMessage([fauxText("4821")]);
    },
  ]);
  await session.prompt("read the picture");
  assert.equal(seen.image, true, "tool result with an image block must reach the model");
  session.dispose();
});

test("guard: credentials, outside paths and .git cannot be read; the secret never reaches the model", async () => {
  const { base, creds, faux, session, results } = await setup();
  const leaked: string[] = [];
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall("read", { path: path.join(creds, "auth.json") })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxToolCall("read", { path: "../outside.txt" })], { stopReason: "toolUse" }),
    (context: any) => {
      leaked.push(JSON.stringify(context.messages));
      return fauxAssistantMessage([fauxText("done")]);
    },
  ]);
  await session.prompt("try to read secrets");
  assert.equal(results.length, 2);
  assert.ok(results.every((r) => r.isError && /not allowed/.test(textOf(r.result))), JSON.stringify(results));
  assert.ok(!leaked.join("").includes("TOP-SECRET-KEY"));
  fs.writeFileSync(path.join(base, "marker"), "");
  session.dispose();
});

test("guard: writes only under wiki/; a write outside creates no file", async () => {
  const { base, ws, faux, session, results } = await setup();
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall("write", { path: "wiki/ok.md", content: "hello" })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxToolCall("write", { path: "scripts/evil.py", content: "x" })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxToolCall("write", { path: "../escape.txt", content: "x" })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxText("done")]),
  ]);
  await session.prompt("write some files");
  assert.equal(fs.readFileSync(path.join(ws, "wiki", "ok.md"), "utf8"), "hello");
  assert.equal(fs.existsSync(path.join(ws, "scripts", "evil.py")), false);
  assert.equal(fs.existsSync(path.join(base, "escape.txt")), false);
  assert.deepEqual(results.map((r) => r.isError), [false, true, true]);
  session.dispose();
});

test("bash is not callable and has no effect", async () => {
  const { base, faux, session, results } = await setup();
  const marker = path.join(base, "pwned");
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall("bash", { command: `node -e "require('fs').writeFileSync(process.argv[1],'x')" ${JSON.stringify(marker)}` })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxText("done")]),
  ]);
  await session.prompt("run a shell command");
  assert.equal(fs.existsSync(marker), false);
  assert.ok(results.every((r) => r.isError));
  session.dispose();
});

test("pipeline: allowed command runs through the tool, destructive command is blocked", async () => {
  const { faux, session, results } = await setup();
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall("pipeline", { args: ["status"] })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxToolCall("pipeline", { args: ["retract-source", "--source", "s1"] })], { stopReason: "toolUse" }),
    fauxAssistantMessage([fauxText("done")]),
  ]);
  await session.prompt("use the pipeline");
  assert.equal(results[0].isError, false);
  assert.match(textOf(results[0].result), /FAKE-PIPELINE status/);
  assert.equal(results[1].isError, true);
  assert.match(textOf(results[1].result), /not allowed/);
  session.dispose();
});
