// 离线列出某些厂商的模型（能看图的标 V）：node scripts/list-models.mjs openai anthropic ...
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-list-"));
const rt = await ModelRuntime.create({ authPath: path.join(dir, "auth.json"), modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
for (const id of process.argv.slice(2)) {
  const ms = rt.getModels(id);
  console.log(`\n${id} (${ms.length} models)`);
  for (const m of ms) {
    console.log(`  ${m.input.includes("image") ? "V" : "-"} ${m.id.padEnd(34)} ctx=${String(m.contextWindow).padStart(8)} reasoning=${m.reasoning ? "Y" : "-"}`);
  }
}
