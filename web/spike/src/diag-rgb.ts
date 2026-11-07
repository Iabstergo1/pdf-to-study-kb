// 诊断：同样的数字图，分别用「灰度 PNG」「RGB PNG」「RGBA PNG」发给模型，看哪种能被读对。
import { crc32, deflateSync, inflateSync } from "node:zlib";
import { createRuntime, resolveModel, type AuthLabel } from "./models.ts";
import { randomCode, renderPixelCodePng as renderCodePng } from "./png.ts";

const [label, spec] = process.argv.slice(2) as [AuthLabel, string];
if (!label || !spec) throw new Error("usage: diag-rgb.ts <apikey|chatgpt|custom> <provider/model-id>");

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body) >>> 0);
  return Buffer.concat([len, body, crc]);
}

/** 把灰度 PNG 展开成 RGB（colorType 2）或 RGBA（colorType 6）。 */
function expand(gray: Buffer, colorType: 2 | 6): Buffer {
  let o = 8, w = 0, h = 0; let idat = Buffer.alloc(0);
  while (o < gray.length) {
    const len = gray.readUInt32BE(o); const type = gray.toString("ascii", o + 4, o + 8); const data = gray.subarray(o + 8, o + 8 + len);
    if (type === "IHDR") { w = data.readUInt32BE(0); h = data.readUInt32BE(4); }
    if (type === "IDAT") idat = data;
    o += 12 + len;
  }
  const raw = inflateSync(idat); const ch = colorType === 2 ? 3 : 4;
  const out = Buffer.alloc(h * (1 + w * ch));
  for (let y = 0; y < h; y++) {
    out[y * (1 + w * ch)] = 0;
    for (let x = 0; x < w; x++) {
      const v = raw[y * (1 + w) + 1 + x]; const p = y * (1 + w * ch) + 1 + x * ch;
      out[p] = v; out[p + 1] = v; out[p + 2] = v; if (ch === 4) out[p + 3] = 255;
    }
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = colorType;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(out)), chunk("IEND", Buffer.alloc(0))]);
}

const rt = await createRuntime(label);
const model = resolveModel(rt, spec);
for (const [name, make] of [
  ["gray ", (c: string) => renderCodePng(c)],
  ["rgb  ", (c: string) => expand(renderCodePng(c), 2)],
  ["rgba ", (c: string) => expand(renderCodePng(c), 6)],
] as const) {
  let ok = 0; const answers: string[] = [];
  for (let i = 0; i < 3; i++) {
    const code = randomCode();
    const msg = await rt.completeSimple(model, {
      systemPrompt: "You are a careful assistant.",
      messages: [{ role: "user", content: [{ type: "text", text: "The picture shows a 4-digit number. Reply with ONLY those 4 digits." }, { type: "image", data: make(code).toString("base64"), mimeType: "image/png" }], timestamp: Date.now() }],
    } as never, {} as never);
    const text = msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("").trim();
    const good = text.replace(/\D/g, "") === code; if (good) ok++;
    answers.push(`${code}->${text.slice(0, 12)}${good ? "✓" : "✗"}`);
  }
  console.log(`${name} ${ok}/3   ${answers.join("  ")}`);
}
