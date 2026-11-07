// 诊断：让模型描述图片，区分「完全没收到图」「收到但很糊」「收到但数字读不准」。
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";
import { renderCodePng } from "./png.ts";

const [label, spec] = process.argv.slice(2) as [AuthLabel, string];
const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
const data = renderCodePng("3978").toString("base64");
const ask = async (q: string) => {
  const msg = await rt.completeSimple(model, {
    systemPrompt: "You are a careful assistant.",
    messages: [{ role: "user", content: [{ type: "text", text: q }, { type: "image", data, mimeType: "image/png" }], timestamp: Date.now() }],
  } as never, {} as never);
  const text = msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("").trim();
  console.log(`Q: ${q}\nA: ${text}${msg.errorMessage ? "  [err " + msg.errorMessage + "]" : ""}\n`);
};
await ask("Describe this image in two sentences: size, background color, and what is drawn.");
await ask("How many separate dark glyphs (digits) are drawn, and what does the first glyph look like (describe its shape)?");
await ask("Read the digits from left to right, one at a time, and say how sure you are.");
