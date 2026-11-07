import path from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
  SettingsManager,
  type ModelRuntime,
} from "@earendil-works/pi-coding-agent";
import { configDir } from "./config.ts";
import { defaultGuardOptions, guardExtension } from "./guard.ts";
import { createPipelineTool } from "./pipeline-tool.ts";
import { WEB_ENV_ADDENDUM } from "./prompt.ts";

export interface IngestSessionOptions {
  modelRuntime: ModelRuntime;
  model: any;
  /** 工作区根（STUDY_KB_ROOT）；真实应用里等于仓库根，验证时是临时目录。 */
  workspaceRoot: string;
  repoRoot: string;
  credentialsDir: string;
  python: string;
  /** 默认 <repoRoot>/scripts/pipeline.py。 */
  pipelineScript?: string;
  /** 要加载的 skill 目录，默认只有 ingest。 */
  skillDirs?: string[];
  /** pi 的 agentDir：给一个空目录，避免读到用户自己的 ~/.pi 配置、扩展、skills。 */
  agentDir?: string;
  thinkingLevel?: "off" | "minimal" | "low" | "medium" | "high";
}

export async function createIngestSession(o: IngestSessionOptions) {
  const agentDir = o.agentDir ?? path.join(configDir(), "pi-agent");
  const guard = defaultGuardOptions({ workspaceRoot: o.workspaceRoot, repoRoot: o.repoRoot, credentialsDir: o.credentialsDir });
  const pipeline = createPipelineTool({
    python: o.python,
    script: o.pipelineScript ?? path.join(o.repoRoot, "scripts", "pipeline.py"),
    workspaceRoot: o.workspaceRoot,
    repoRoot: o.repoRoot,
    allow: guard.pipelineAllow,
  });
  const loader = new DefaultResourceLoader({
    cwd: o.workspaceRoot,
    agentDir,
    noSkills: true,
    additionalSkillPaths: o.skillDirs ?? [path.join(o.repoRoot, ".agents", "skills", "ingest")],
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    extensionFactories: [guardExtension(guard)],
    appendSystemPrompt: [WEB_ENV_ADDENDUM],
  });
  await loader.reload();
  const { session } = await createAgentSession({
    cwd: o.workspaceRoot,
    agentDir,
    model: o.model,
    thinkingLevel: o.thinkingLevel ?? "off",
    modelRuntime: o.modelRuntime,
    resourceLoader: loader,
    tools: ["read", "write", "edit", "pipeline"],
    customTools: [pipeline],
    sessionManager: SessionManager.inMemory(o.workspaceRoot),
    settingsManager: SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: true, maxRetries: 2 } }),
  });
  return { session, loader };
}
