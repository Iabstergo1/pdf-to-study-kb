import os from "node:os";
import path from "node:path";

/** 仓库根：web/spike/src → 上三级。 */
export const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");

/** 用户配置目录：凭据放这里，不在仓库里，也在 agent 的可读范围之外。 */
export function configDir(): string {
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"), "study-kb");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "study-kb");
  }
  return path.join(process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), "study-kb");
}

/** 真实验证结果（gitignored 的 pipeline-workspace 下）。 */
export const RESULTS_FILE = path.join(REPO_ROOT, "pipeline-workspace", "reports", "c0-spike-results.jsonl");
