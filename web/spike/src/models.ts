import fs from "node:fs";
import path from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { configDir } from "./config.ts";

export type AuthLabel = "apikey" | "chatgpt" | "custom";

interface CustomProvider {
  id: string;
  name: string;
  api: string;
  baseUrl: string;
  apiKey: string;
  models: Array<{ id: string; name?: string; input: ("text" | "image")[]; reasoning?: boolean; contextWindow?: number; maxTokens?: number }>;
}
interface SpikeKeys {
  keys?: Record<string, string>;
  custom?: CustomProvider[];
}

function loadKeys(): SpikeKeys {
  const file = path.join(configDir(), "spike-keys.json");
  return fs.existsSync(file) ? (JSON.parse(fs.readFileSync(file, "utf8")) as SpikeKeys) : {};
}

/**
 * 不同认证路径用不同的认证文件，避免 ChatGPT 的 OAuth 凭据与 OpenAI API key 互相覆盖：
 * - chatgpt: auth-chatgpt.json（login-chatgpt.ts 写入，pi 自动刷新令牌）
 * - apikey / custom: auth-<label>.json 保持为空，key 只在内存里（setRuntimeApiKey），不落盘。
 */
export async function createRuntime(label: AuthLabel): Promise<ModelRuntime> {
  const dir = configDir();
  fs.mkdirSync(dir, { recursive: true });
  const rt = await ModelRuntime.create({
    authPath: path.join(dir, `auth-${label}.json`),
    modelsPath: null,
    refreshOnCreate: false,
    allowModelNetwork: false,
  });
  const k = loadKeys();
  if (label === "apikey") {
    for (const [provider, key] of Object.entries(k.keys ?? {})) await rt.setRuntimeApiKey(provider, key);
  }
  if (label === "custom") {
    for (const c of k.custom ?? []) {
      rt.registerProvider(c.id, {
        name: c.name,
        baseUrl: c.baseUrl,
        apiKey: c.apiKey,
        api: c.api as never,
        models: c.models.map((m) => ({
          id: m.id,
          name: m.name ?? m.id,
          api: c.api as never,
          input: m.input,
          reasoning: m.reasoning ?? false,
          contextWindow: m.contextWindow ?? 128_000,
          maxTokens: m.maxTokens ?? 8192,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        })),
      });
    }
  }
  return rt;
}

export function resolveModel(rt: ModelRuntime, spec: string) {
  const i = spec.indexOf("/");
  if (i < 0) throw new Error('model spec must look like "provider/model-id"');
  const model = rt.getModel(spec.slice(0, i), spec.slice(i + 1));
  if (!model) throw new Error(`unknown model: ${spec}`);
  return model;
}
