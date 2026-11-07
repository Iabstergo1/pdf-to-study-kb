import { crc32, deflateSync } from "node:zlib";
import { execFileSync } from "node:child_process";
import path from "node:path";

// 5x7 点阵字体：只用于生成「读数字」的测试图，不依赖任何图像库。
const FONT: Record<string, readonly string[]> = {
  "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
  "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
  "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
  "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
  "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
};

/** 随机数字串，首位非 0（避免模型把 0 开头的当成少一位）。 */
export function randomCode(len = 4): string {
  let s = String(1 + Math.floor(Math.random() * 9));
  for (let i = 1; i < len; i++) s += String(Math.floor(Math.random() * 10));
  return s;
}

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body) >>> 0);
  return Buffer.concat([len, body, crc]);
}

/** 白底黑字、8 位灰度 PNG；每个点阵格放大 `scale` 倍，四周留 `pad` 格白边。 */
export function renderPixelCodePng(code: string, scale = 32, pad = 3): Buffer {
  if (!/^[0-9]+$/.test(code)) throw new Error("code must consist of digits only");
  const cols = code.length * 5 + (code.length - 1);
  const w = (cols + pad * 2) * scale;
  const h = (7 + pad * 2) * scale;
  const raw = Buffer.alloc(h * (1 + w), 255);
  for (let y = 0; y < h; y++) raw[y * (1 + w)] = 0; // 每行第一个字节是滤镜类型 0
  code.split("").forEach((ch, i) => {
    const glyph = FONT[ch];
    for (let gy = 0; gy < 7; gy++) {
      for (let gx = 0; gx < 5; gx++) {
        if (glyph[gy][gx] !== "1") continue;
        const cx = (pad + i * 6 + gx) * scale;
        const cy = (pad + gy) * scale;
        for (let dy = 0; dy < scale; dy++) {
          const rowStart = (cy + dy) * (1 + w) + 1;
          raw.fill(0, rowStart + cx, rowStart + cx + scale);
        }
      }
    }
  });
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 0; // grayscale
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** 使用项目已有的 PyMuPDF 和普通字体，模拟实际页面的抗锯齿文字。 */
export function renderCodePng(code: string): Buffer {
  if (!/^[0-9]+$/.test(code)) throw new Error("code must consist of digits only");
  return execFileSync(process.env.STUDY_KB_PYTHON ?? "python", [
    path.join(import.meta.dirname, "..", "scripts", "make-natural.py"), "--stdout", code,
  ], { env: { ...process.env, PYTHONUTF8: "1" } });
}
