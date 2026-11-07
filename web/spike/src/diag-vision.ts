// 诊断：同一张图「放在用户消息里」vs「通过 read 工具结果」，哪条路能被模型读对。
// 用法：node src/diag-vision.ts <apikey|chatgpt|custom> <provider/model-id>
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";
import { randomCode, renderCodePng } from "./png.ts";

const [label, spec] = process.argv.slice(2) as [AuthLabel, string];
if (!label || !spec) throw new Error("usage: diag-vision.ts <apikey|chatgpt|custom> <provider/model-id>");
const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
console.log("model.input =", JSON.stringify(model.input), " api =", model.api);

for (let i = 0; i < 3; i++) {
  const code = randomCode();
  const data = renderCodePng(code).toString("base64");
  const msg = await rt.completeSimple(model, {
    systemPrompt: "You are a careful assistant.",
    messages: [{
      role: "user",
      content: [
        { type: "text", text: "The picture shows a 4-digit number. Reply with ONLY those 4 digits." },
        { type: "image", data, mimeType: "image/png" },
      ],
      timestamp: Date.now(),
    }],
  } as never, {} as never);
  const text = msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("").trim();
  console.log(`user-message image: code=${code} answer=${JSON.stringify(text)} ${text.replace(/\D/g, "") === code ? "OK" : "WRONG"}  stop=${msg.stopReason}${msg.errorMessage ? " err=" + msg.errorMessage : ""}`);
}
