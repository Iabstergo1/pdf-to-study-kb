import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { INGEST_PIPELINE_ALLOW } from "../src/guard.ts";
import { createPipelineTool } from "../src/pipeline-tool.ts";

function fixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ptool-"));
  const script = path.join(dir, "fake-pipeline.js");
  fs.writeFileSync(
    script,
    [
      "const [cmd, ...rest] = process.argv.slice(2);",
      "if (cmd === 'lint') { console.log('lint report line'); process.exit(3); }",
      "if (cmd === 'show-window') { console.log('x'.repeat(5000)); process.exit(0); }",
      "console.log(JSON.stringify({ cmd, rest, root: process.env.STUDY_KB_ROOT, utf8: process.env.PYTHONUTF8, cwd: process.cwd() }));",
    ].join("\n"),
  );
  const tool = createPipelineTool({
    python: process.execPath,
    script,
    workspaceRoot: path.join(dir, "ws"),
    repoRoot: dir,
    allow: INGEST_PIPELINE_ALLOW,
    maxOutputChars: 1000,
  });
  return { dir, tool };
}

const run = (tool: ReturnType<typeof fixture>["tool"], args: unknown) =>
  tool.execute("t1", { args } as never, undefined, undefined, {} as never);

test("success: returns the command output and sets STUDY_KB_ROOT / UTF-8 env, cwd = repo root", async () => {
  const { dir, tool } = fixture();
  const r = await run(tool, ["status", "--source", "s1"]);
  const text = (r.content[0] as { text: string }).text;
  const out = JSON.parse(text.trim());
  assert.deepEqual(out.rest, ["--source", "s1"]);
  assert.equal(out.root, path.join(dir, "ws"));
  assert.equal(out.utf8, "1");
  assert.equal(fs.realpathSync(out.cwd), fs.realpathSync(dir));
});

test("non-zero exit throws with the exit code and the output", async () => {
  const { tool } = fixture();
  await assert.rejects(run(tool, ["lint", "--source", "s1"]), /exit code 3[\s\S]*lint report line/);
});

test("subcommand outside the allowlist is refused without spawning", async () => {
  const { tool } = fixture();
  await assert.rejects(run(tool, ["retract-source", "--source", "s1"]), /not allowed/);
});

test("long output is truncated with a marker", async () => {
  const { tool } = fixture();
  const r = await run(tool, ["show-window", "--source", "s1", "--window", "w0"]);
  const text = (r.content[0] as { text: string }).text;
  assert.ok(text.length < 1300, `length ${text.length}`);
  assert.match(text, /truncated/);
});
