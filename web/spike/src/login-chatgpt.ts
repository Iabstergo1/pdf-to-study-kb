// 用法：node src/login-chatgpt.ts
// 做什么：用 pi 的 `openai` 厂商 OAuth（Sign in with ChatGPT）登录；凭据写进 configDir()/auth-chatgpt.json。
// 登录链接会在默认浏览器里自动打开；授权后 pi 的本地回调服务接住，无需粘贴。
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.ts";
import { createRuntime } from "./models.ts";

/** Sign in with ChatGPT 要求每个安装有一个稳定的设备 ID（UUID）：第一次使用时生成，之后必须一直返回同一个。 */
function deviceId(): string {
  const file = path.join(configDir(), "device-id");
  if (fs.existsSync(file)) return fs.readFileSync(file, "utf8").trim();
  const id = randomUUID();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, id);
  return id;
}

function openBrowser(url: string): void {
  if (process.platform === "win32") spawn("rundll32", ["url.dll,FileProtocolHandler", url], { stdio: "ignore", detached: true }).unref();
  else spawn(process.platform === "darwin" ? "open" : "xdg-open", [url], { stdio: "ignore", detached: true }).unref();
}

const rt = await createRuntime("chatgpt");
const cred = await rt.login("openai", "oauth", {
  prompt: async (p) => {
    console.log(`[prompt:${p.type}] ${p.message}`);
    // manual_code 与本地回调服务赛跑：回调赢了 pi 会 abort 这个提示，所以这里只等待中止。
    return await new Promise<string>((_, reject) => p.signal?.addEventListener("abort", () => reject(new Error("prompt aborted")), { once: true }));
  },
  notify: (ev: any) => {
    if (ev.type === "auth_url") { console.log(`[auth_url] ${ev.url}`); openBrowser(ev.url); }
    else if (ev.type === "device_code") console.log(`[device_code] ${ev.userCode} at ${ev.verificationUri}`);
    else console.log(`[${ev.type}] ${ev.message ?? ""}`);
  },
}, { getDeviceId: deviceId });
console.log("login ok; credential type:", (cred as { type?: string }).type);
const models = (await rt.getAvailable("openai")).map((m) => `${m.id}${m.input.includes("image") ? " (vision)" : ""}`);
console.log("available openai models:", models.join(", "));
