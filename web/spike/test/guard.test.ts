import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { INGEST_PIPELINE_ALLOW, checkPath, checkPipelineArgs, defaultGuardOptions } from "../src/guard.ts";

function setup() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "guard-"));
  const ws = path.join(base, "workspace");
  const repo = path.join(base, "repo");
  const creds = path.join(base, "creds");
  const outside = path.join(base, "outside");
  for (const d of [
    path.join(ws, "wiki", "sub"),
    path.join(ws, "pipeline-workspace", "staging", "s1"),
    path.join(ws, "docs"),
    path.join(repo, "docs"),
    path.join(ws, ".git"),
    creds,
    outside,
  ]) fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(creds, "auth.json"), "TOP-SECRET");
  fs.writeFileSync(path.join(outside, "x.txt"), "x");
  fs.writeFileSync(path.join(repo, "docs", "schema.md"), "schema");
  fs.writeFileSync(path.join(ws, ".git", "config"), "git");
  const o = defaultGuardOptions({ workspaceRoot: ws, repoRoot: repo, credentialsDir: creds });
  return { base, ws, repo, creds, outside, o };
}

test("read: workspace and repo are allowed", () => {
  const { ws, repo, o } = setup();
  assert.equal(checkPath("read", "wiki/a.md", o).ok, true);
  assert.equal(checkPath("read", path.join(repo, "docs", "schema.md"), o).ok, true);
  assert.equal(checkPath("read", path.join(ws, "pipeline-workspace", "staging", "s1", "w0000.png"), o).ok, true);
});

test("read: credentials, outside, traversal, .git, ~, empty, non-string are denied", () => {
  const { creds, outside, o } = setup();
  assert.equal(checkPath("read", path.join(creds, "auth.json"), o).ok, false);
  assert.equal(checkPath("read", path.join(outside, "x.txt"), o).ok, false);
  assert.equal(checkPath("read", "../outside/x.txt", o).ok, false);
  assert.equal(checkPath("read", ".git/config", o).ok, false);
  assert.equal(checkPath("read", "~/secrets.txt", o).ok, false);
  assert.equal(checkPath("read", "", o).ok, false);
  assert.equal(checkPath("read", 5, o).ok, false);
  assert.equal(checkPath("read", undefined, o).ok, false);
  assert.equal(checkPath("read", "a\0b", o).ok, false);
});

test("read: a junction/symlink pointing outside is denied (realpath)", (t) => {
  const { ws, outside, o } = setup();
  const link = path.join(ws, "link");
  try {
    fs.symlinkSync(outside, link, "junction");
  } catch {
    t.skip("cannot create a junction/symlink in this environment");
    return;
  }
  assert.equal(checkPath("read", "link/x.txt", o).ok, false);
});

test("write: only wiki/**, the staging digest.md and the arbitration decisions.json", () => {
  const { o } = setup();
  assert.equal(checkPath("write", "wiki/a.md", o).ok, true);
  assert.equal(checkPath("write", "wiki/sub/b.md", o).ok, true);
  assert.equal(checkPath("write", "pipeline-workspace/staging/s1/digest.md", o).ok, true);
  // A.5 仲裁（PDF）：agent 要写 decisions.json；队列等其他产物不许写
  assert.equal(checkPath("write", "pipeline-workspace/staging/s1/arbitration/decisions.json", o).ok, true);
  assert.equal(checkPath("write", "pipeline-workspace/staging/s1/arbitration/queue.json", o).ok, false);
  assert.equal(checkPath("write", "pipeline-workspace/staging/s1/sub/digest.md", o).ok, false, "digest.md only at depth 2");
  assert.equal(checkPath("write", "pipeline-workspace/staging/s1/other.json", o).ok, false);
  assert.equal(checkPath("write", "pipeline-workspace/state/study-kb.sqlite", o).ok, false);
  assert.equal(checkPath("write", "docs/x.md", o).ok, false);
  assert.equal(checkPath("write", "../outside.md", o).ok, false);
  assert.equal(checkPath("write", "scripts/pipeline.py", o).ok, false);
});

test("pipeline: ingest commands allowed, destructive and unknown denied", () => {
  for (const cmd of ["status", "lint", "show-window", "window-done", "ingest-start", "resolve-concept"]) {
    assert.equal(checkPipelineArgs([cmd, "--source", "s1"], INGEST_PIPELINE_ALLOW).ok, true, cmd);
  }
  for (const cmd of ["retract-source", "reset-source", "staging-clean", "adopt-vault", "reuse-source", "skill-adopt", "reopen"]) {
    assert.equal(checkPipelineArgs([cmd, "--source", "s1"], INGEST_PIPELINE_ALLOW).ok, false, cmd);
  }
});

test("pipeline: malformed args and dangerous flags are denied", () => {
  const a = INGEST_PIPELINE_ALLOW;
  assert.equal(checkPipelineArgs([], a).ok, false);
  assert.equal(checkPipelineArgs("lint", a).ok, false);
  assert.equal(checkPipelineArgs(["lint", 5], a).ok, false);
  assert.equal(checkPipelineArgs(["lint", "--source", "a\nb"], a).ok, false);
  assert.equal(checkPipelineArgs(["windows", "--source", "s", "--dev-bypass"], a).ok, false);
  assert.equal(checkPipelineArgs(["source-convert", "--source", "s", "--force"], a).ok, false);
});
