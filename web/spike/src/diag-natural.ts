// 诊断：用「自然」的抗锯齿数字图（由 make-natural.py 生成）测试模型读数。
// 用法：node src/diag-natural.ts <apikey|chatgpt|custom> <provider/model-id> <图片目录>
import fs from "node:fs";
import path from "node:path";
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";

const [label, spec, dir] = process.argv.slice(2) as [AuthLabel, string, string];
const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
let ok = 0;
const files = fs.readdirSync(dir).filter((f) => /^natural-\d+\.png$/.test(f));
for (const f of files) {
  const code = f.match(/(\d+)/)![1];
  const msg = await rt.completeSimple(model, {
    systemPrompt: "You are a careful assistant.",
    messages: [{ role: "user", content: [{ type: "text", text: "The picture shows a 4-digit number. Reply with ONLY those 4 digits." }, { type: "image", data: fs.readFileSync(path.join(dir, f)).toString("base64"), mimeType: "image/png" }], timestamp: Date.now() }],
  } as never, {} as never);
  const text = msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("").trim();
  const good = text.replace(/\D/g, "") === code; if (good) ok++;
  console.log(`${code} -> ${JSON.stringify(text)} ${good ? "OK" : "WRONG"}${msg.errorMessage ? " err=" + msg.errorMessage : ""}`);
}
console.log(`natural images: ${ok}/${files.length}`);
