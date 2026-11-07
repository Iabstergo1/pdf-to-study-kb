import test from "node:test";
import assert from "node:assert/strict";
import { crc32, inflateSync } from "node:zlib";
import { randomCode, renderCodePng } from "../src/png.ts";

function chunks(png: Buffer) {
  const out: { type: string; data: Buffer; crc: number }[] = [];
  let o = 8;
  while (o < png.length) {
    const len = png.readUInt32BE(o);
    out.push({
      type: png.toString("ascii", o + 4, o + 8),
      data: png.subarray(o + 8, o + 8 + len),
      crc: png.readUInt32BE(o + 8 + len),
    });
    o += 12 + len;
  }
  return out;
}

test("regular-font PNG: signature, CRCs, RGB raster and antialiasing", () => {
  const png = renderCodePng("4821");
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const cs = chunks(png);
  assert.equal(cs[0].type, "IHDR");
  assert.equal(cs.at(-1)?.type, "IEND");
  for (const c of cs) {
    assert.equal(crc32(Buffer.concat([Buffer.from(c.type, "ascii"), c.data])) >>> 0, c.crc, c.type);
  }
  const w = cs[0].data.readUInt32BE(0);
  const h = cs[0].data.readUInt32BE(4);
  assert.equal(cs[0].data[8], 8, "bit depth");
  assert.equal(cs[0].data[9], 2, "RGB");
  const raster = inflateSync(Buffer.concat(cs.filter((c) => c.type === "IDAT").map((c) => c.data)));
  assert.equal(raster.length, h * (1 + w * 3));
  assert.ok(new Set(raster).size > 16, "regular text must have antialiased edges, not a binary pixel font");
});

test("deterministic per code, different across codes", () => {
  assert.ok(renderCodePng("1234").equals(renderCodePng("1234")));
  assert.ok(!renderCodePng("1234").equals(renderCodePng("1235")));
});

test("randomCode: four digits, never starts with 0", () => {
  for (let i = 0; i < 300; i++) assert.match(randomCode(), /^[1-9][0-9]{3}$/);
});

test("non-digit code is rejected", () => {
  assert.throws(() => renderCodePng("12a4"));
});
