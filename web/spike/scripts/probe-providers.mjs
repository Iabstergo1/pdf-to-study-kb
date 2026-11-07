// 探测：pi 的 provider 列表、认证方式、能看图的模型数（离线，不花额度）
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-probe-"));
const rt = await ModelRuntime.create({
  authPath: path.join(dir, "auth.json"),
  modelsPath: null,
  refreshOnCreate: false,
  allowModelNetwork: false,
});
const rows = [];
for (const p of rt.getProviders()) {
  const models = rt.getModels(p.id);
  const vision = models.filter((m) => m.input && m.input.includes("image")).length;
  const auth = p.auth ?? {};
  rows.push({
    id: p.id,
    name: p.name,
    apiKey: !!auth.apiKey,
    oauth: auth.oauth ? (auth.oauth.isSubscription ? "oauth(subscription)" : "oauth") : "-",
    models: models.length,
    vision,
  });
}
rows.sort((a, b) => a.id.localeCompare(b.id));
console.log("providers:", rows.length);
for (const r of rows) {
  console.log(
    `${r.id.padEnd(28)} key=${r.apiKey ? "Y" : "-"} ${String(r.oauth).padEnd(20)} models=${String(r.models).padStart(3)} vision=${String(r.vision).padStart(3)}  ${r.name ?? ""}`,
  );
}
