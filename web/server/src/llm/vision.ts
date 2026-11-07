import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomInt } from 'node:crypto';
import type { ModelThinkingLevel } from '@earendil-works/pi-ai';
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import type { AppConfig } from '../config.ts';
import type { Models } from './models.ts';
import { runProcess } from '../engine/process.ts';

export async function vision(c: AppConfig, models: Models, provider: string, id: string, signal: AbortSignal, log: (s: string) => void, progress: (value: object) => void = () => {}, requestedThinking?: ModelThinkingLevel) {
  models.mark(provider, id, false);
  const { rt, model } = await models.model(provider, id, true, { log, signal });
  const thinkingLevel = await models.thinking(provider, id, requestedThinking);
  progress({ provider, model: id, thinkingLevel });
  const tokens = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 };
  for (let round = 1; round <= 3; round++) {
    const code = String(randomInt(1000, 10000));
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'study-kb-vision-'));
    const file = path.join(dir, 'check.png');
    const script = 'import fitz,sys; d=fitz.open(); p=d.new_page(width=520,height=200); p.insert_text((40,130),sys.argv[1],fontname="helv",fontsize=110); p.get_pixmap(dpi=96).save(sys.argv[2])';
    await runProcess(c.python, ['-c', script, code, file], { cwd: dir, signal });
    const agentDir = path.join(c.config, 'pi-agent');
    const loader = new DefaultResourceLoader({ cwd: dir, agentDir, noExtensions: true, noSkills: true, noContextFiles: true, noPromptTemplates: true, noThemes: true,
      extensionFactories: [pi => { pi.on('tool_call', event => {
        if (event.toolName !== 'read' || path.resolve(dir, String(event.input.path)) !== file) return { block: true, reason: 'Read only check.png' };
      }); }], systemPrompt: 'Use the read tool to inspect images carefully.' });
    await loader.reload();
    const { session } = await createAgentSession({ cwd: dir, agentDir, model, modelRuntime: rt, resourceLoader: loader, tools: ['read'], thinkingLevel, sessionManager: SessionManager.inMemory(dir), settingsManager: SettingsManager.inMemory({ retry: { enabled: false } }) });
    let imageSeen = false; let timeout = false;
    session.subscribe((e: any) => {
      if (e.type === 'tool_execution_end' && e.toolName === 'read' && !e.isError && e.result.content?.some((b: any) => b.type === 'image')) imageSeen = true;
      if (e.type === 'agent_end') {
        const current = session.getSessionStats().tokens;
        progress({ phase: 'vision', round, tokens: Object.fromEntries(Object.entries(tokens).map(([key, value]) => [key, value + (current[key as keyof typeof current] || 0)])) });
      }
    });
    const abort = () => { void session.abort(); }; signal.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(() => { timeout = true; abort(); }, 180000);
    const restoreNetwork = models.handleNetwork(rt, log);
    try {
      signal.throwIfAborted();
      await session.prompt('Use read to open check.png. Reply with exactly the four digits in the image and nothing else.');
      signal.throwIfAborted();
      if (timeout) throw new Error('Image test timed out');
      if (!imageSeen || session.getLastAssistantText()?.trim() !== code) throw new Error(`Image test failed at round ${round}/3`);
      const current = session.getSessionStats().tokens;
      for (const key of Object.keys(tokens) as (keyof typeof tokens)[]) tokens[key] += current[key] || 0;
      progress({ phase: 'vision', round, tokens: { ...tokens } });
      log(`Image read matched: ${round}/3\n`);
    } finally {
      clearTimeout(timer); signal.removeEventListener('abort', abort); session.dispose();
      restoreNetwork();
      fs.unlinkSync(file); fs.rmdirSync(dir);
    }
  }
  models.mark(provider, id, true);
}
