import path from 'node:path';
import fs from 'node:fs';
import { Type, type ModelThinkingLevel } from '@earendil-works/pi-ai';
import { createAgentSession, defineTool, DefaultResourceLoader, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { checkArgs, checkPath, guard, ingestFiles } from './guard.ts';
import { pipeline } from '../engine/process.ts';
import type { AppConfig } from '../config.ts';
import { recoverableTransportError, type Models } from '../llm/models.ts';
import { setTimeout as delay } from 'node:timers/promises';
import type { Job } from '../engine/jobs.ts';

export async function ingest(c: AppConfig, models: Models, input: { provider: string; model: string; thinkingLevel?: ModelThinkingLevel; source: string; mode: 'start' | 'resume' | 'repair' | 'arbitrate' }, job: Job, signal: AbortSignal, log: (s: string) => void, update = (value: object) => { job.progress = { ...job.progress, ...value }; }) {
  if (!models.isVerified(input.provider, input.model)) throw new Error('Pass the image test before ingest');
  const { rt, model } = await models.model(input.provider, input.model, true, { log, signal });
  const thinkingLevel = await models.thinking(input.provider, input.model, input.thinkingLevel);
  update({ model: input.model, provider: input.provider, thinkingLevel });
  log(`[model] ${input.provider}/${input.model} · thinking=${thinkingLevel}\n`);
  let previousTokens = job.progress?.tokens || {};
  update({ phase: input.mode === 'arbitrate' ? 'arbitration' : input.mode === 'repair' ? 'repair' : 'initialize', waiting: null });
  let queue = Promise.resolve('');
  const run = (args: string[]) => {
    const next = queue.catch(() => '').then(() => pipeline(c, args, signal, log)); queue = next; return next;
  };
  const digestPath = path.join(c.root, 'pipeline-workspace/staging', input.source, 'digest.md');
  let finishedWindows = JSON.parse(await pipeline(c, ['ingest-stats', '--source', input.source, '--json'], signal)).windows.finished;
  let networkRecoveries = 0;
  let handoff: { window: string; digestUpdated: boolean } | undefined;
  const handoffBlocks = new Set<string>();
  let handoffPacket = ''; let handoffFailure = '';
  let digestRepair = ''; let digestRepairTurns = 0;
  const repairPrompt = () => `DIGEST_REPAIR_REQUIRED：先完成每窗 U7 摘要核对，暂不推进窗口或知识页。\n${digestRepair}\n读取 ${digestPath}，用 next --source ${input.source} --json 获取精简的下一窗与 hash，不要全量读取 windows.jsonl。核对当前会话中的已完成窗口；重启后用 show-window 重读相关窗口及所需图片，结合已有页面与执行记录补齐遗漏的阅读摘要。保留既有路由、理解和未决问题，不可仅替换窗口编号或编造阅读结论。只编辑此来源 digest.md；最后一窗完成后用 ## DONE 并记下阶段 E/F 尚待完成的工作，否则更新 ## RESUME。再调用 next --source ${input.source} --resume-packet 验证。运行器在 U7 更新且校验通过后自动交接新会话。不重启已完成窗口、不重建工单；无法解释记录时停止说明原因。read/write/edit 均可用，read 支持图片。`;
  const resumePacket = async () => {
    try {
      const packet = await run(['next', '--source', input.source, '--resume-packet']);
      digestRepair = ''; digestRepairTurns = 0; return packet;
    } catch (error) {
      const message = String(error); const problems = message.split(/\r?\n/).filter(line => line.startsWith('- '));
      // Only the CLI's sole stale-digest diagnosis is recoverable here. Never hide other contradictions.
      digestRepair = message.includes('resume-packet fail-closed') && problems.length === 1 && /^- RESUME 未提及账本判定的下一窗 w\d+/.test(problems[0]) ? message : '';
      if (digestRepair) log('[digest-repair] 核对已完成窗口并补齐阅读摘要；恢复检查通过后继续。\n');
      throw error;
    }
  };
  let resume = '';
  if (input.mode === 'resume') {
    const next = JSON.parse(await pipeline(c, ['next', '--json'], signal)).actions.find((action: any) => action.source_id === input.source);
    if (next?.current_stage === 'ingested') {
      resume = 'phase=finish\n' + JSON.stringify(next) + '\n写作已经完成。按 CLI 下一步做发布检查，不再请求写作恢复包，不重建工单、不重做窗口。';
      update({ phase: 'finish' });
    } else {
      try { resume = await resumePacket(); }
      catch (error) { if (!digestRepair) throw error; resume = repairPrompt(); }
    }
  }
  const agentDir = path.join(c.config, 'pi-agent');
  const loader = new DefaultResourceLoader({
    cwd: c.root, agentDir, noSkills: true, additionalSkillPaths: [path.join(c.repo, '.agents/skills/ingest')],
    noExtensions: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    extensionFactories: [pi => {
      pi.on('tool_call', event => {
        const params = event.input as Record<string, any>;
        if (event.toolName === 'read' && path.relative(path.join(path.dirname(digestPath), 'windows.jsonl'), path.resolve(c.root, params.path || '')) === '' && !(params.limit > 0 && params.limit <= 10)) return { block: true, reason: `请用 pipeline next --source ${input.source} --json 获取下一窗和 hash，或 show-window 读取指定窗。检查窗口元数据时必须指定 limit（最多 10 行），不要全量读取 windows.jsonl。` };
        if (!digestRepair && !handoff) return;
        if (['read', 'files'].includes(event.toolName)) return;
        if (['write', 'edit'].includes(event.toolName) && checkPath('write', params.path, c, input.source) === checkPath('write', digestPath, c, input.source)) return;
        if (event.toolName === 'pipeline' && ['next', 'status', 'ingest-stats', 'show-window'].includes(params.args?.[0])) return;
        if (handoff) handoffBlocks.add(event.toolCallId);
        return { block: true, reason: repairPrompt() };
      });
    }, guard(c, input.source, run)],
    appendSystemPrompt: [
      fs.readFileSync(path.join(c.repo, 'AGENTS.md'), 'utf8'),
      'This is the Study KB web runtime. Use only read/write/edit/pipeline/files. files lists wiki/ and this source staging; use it to verify full filenames and enumerate concepts for phase E. No shell is available. Call pipeline with an args array, excluding python and script paths. Environment and interpreter are already configured. Open every required page image with read; stop if you cannot see it. JSON --writes is one inline argument; do not use --writes-file. User confirmed this source and domain in the wizard. Only wiki/ and this source digest.md/arbitration/decisions.json are writable. Follow the ingest skill unchanged. Finish with published, pending content acceptance. A lint failure stops this attempt; repair requires the explicit repair action. Never edit the checker. Read whole overview before finishing to avoid leftover seed sections.',
    ],
  });
  await loader.reload();
  let lintFailure = ''; let lastToolError = ''; let streamed = false; let textBuffer = '';
  let session: Awaited<ReturnType<typeof createAgentSession>>['session'];
  const openSession = async () => {
    const sessionManager = SessionManager.create(c.root, path.join(c.root, 'pipeline-workspace/web-sessions', input.source));
    const cleanValue = (value: any): any => typeof value === 'string' ? models.clean(value) : Array.isArray(value) ? value.map(cleanValue) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, cleanValue(v)])) : value;
    const appendMessage = sessionManager.appendMessage.bind(sessionManager);
    sessionManager.appendMessage = message => appendMessage(models.cleanMessage(message));
    const appendCompaction = sessionManager.appendCompaction.bind(sessionManager);
    sessionManager.appendCompaction = (summary, first, tokens, details, fromHook, usage) => appendCompaction(models.clean(summary), first, tokens, cleanValue(details), fromHook, usage);
    const created = await createAgentSession({
      cwd: c.root, agentDir, model, modelRuntime: rt, resourceLoader: loader, thinkingLevel,
      tools: ['read', 'write', 'edit', 'pipeline', 'files'],
      customTools: [defineTool({ name: 'files', label: 'files', description: 'List actual files under wiki/ or this source staging. Use narrow paths. A capped listing is explicitly marked.', parameters: Type.Object({ path: Type.String() }), execute: async (_id, p) => {
        const paths = ingestFiles(c, input.source, p.path);
        return { content: [{ type: 'text', text: paths.join('\n') + (paths.length >= 10000 ? '\nTRUNCATED: narrow the directory before concluding the inventory is complete.' : '') }], details: {} };
      } }), defineTool({ name: 'pipeline', label: 'pipeline', description: 'Run a study-kb CLI command. Example: {"args":["show-window","--source","' + input.source + '","--window","w0000"]}. read/write/edit/files are separate tools, never CLI commands. To read files call read with path. Do not call help, read-source or pass Python/script paths. Supported commands: ingest-start, next, show-window, window-start, window-done, resolve-concept, check-write, snapshot-page, sync-overview-sources, ingest-done, lint, arbitration-status, arbitration-apply, status, ingest-stats, source-audit, windows, workorder, preflight-eval.',
        parameters: Type.Object({ args: Type.Array(Type.String()) }),
        executionMode: 'sequential',
        execute: async (_id, params) => {
          checkArgs(params.args, input.source, c);
          try {
            let output = params.args[0] === 'next' && params.args.includes('--resume-packet') ? await resumePacket() : await run(params.args);
            if (['window-done', 'ingest-done', 'lint'].includes(params.args[0])) {
              const stats = JSON.parse(await pipeline(c, ['ingest-stats', '--source', input.source, '--json'], signal));
              update({ windows: stats.windows, written: stats.page_inventory.total });
              if (params.args[0] === 'window-done' && stats.windows.finished > finishedWindows) {
                finishedWindows = stats.windows.finished;
                handoff = { window: params.args[params.args.indexOf('--window') + 1], digestUpdated: false };
                digestRepair = '本窗 U6 已完成；请在当前会话完成 U7 后交接。';
                log('[digest-repair] 等待本窗 U7 摘要更新与恢复校验。\n');
                output += '\n' + await run(['next', '--source', input.source, '--json']) + '\n' + repairPrompt();
              }
            }
            return { content: [{ type: 'text', text: output }], details: {} };
          } catch (error) {
            if (params.args[0] === 'lint') { lintFailure = String(error); void session.abort(); }
            throw error;
          }
        },
      })],
      sessionManager,
      settingsManager: SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: false } }),
    });
    const current = created.session;
    let batchDigestUpdated = false;
    const finishTurn = current.agent.finishTurn;
    current.agent.finishTurn = async (turn, turnSignal) => {
      const decision = await finishTurn?.(turn, turnSignal);
      // U7 may precede U6 within this batch. A prior batch's old digest does not count.
      if (handoff && batchDigestUpdated) handoff.digestUpdated = true;
      batchDigestUpdated = false;
      const toolFailed = turn.toolResults.some(result => result.isError && !handoffBlocks.has(result.toolCallId));
      handoffBlocks.clear();
      if (handoff?.digestUpdated && !turnSignal?.aborted && !toolFailed) {
        try { handoffPacket = await resumePacket(); return { action: 'end' }; }
        catch (error) { if (!digestRepair) { handoffFailure = String(error); return { action: 'end' }; } }
      }
      return decision || undefined;
    };
    const digestWrites = new Map<string, string | null>();
    current.subscribe((event: any) => {
      if (event.type === 'message_update' && event.assistantMessageEvent?.type === 'text_delta') {
        streamed = true; textBuffer += event.assistantMessageEvent.delta;
        // Redact complete lines, so a key split across streaming chunks never leaks.
        const end = textBuffer.lastIndexOf('\n');
        if (end >= 0) { log(textBuffer.slice(0, end + 1)); textBuffer = textBuffer.slice(end + 1); }
      }
      if (event.type === 'message_end' && textBuffer) { log(textBuffer); textBuffer = ''; }
      if (event.type === 'tool_execution_start') {
        if (['write', 'edit'].includes(event.toolName) && path.relative(digestPath, path.resolve(c.root, event.args.path || '')) === '') digestWrites.set(event.toolCallId, fs.existsSync(digestPath) ? fs.readFileSync(digestPath, 'utf8') : null);
        const command = event.toolName === 'pipeline' ? event.args.args?.[0] : ['write', 'edit'].includes(event.toolName) && /(?:topics|comparisons|synthesis)[\\/]/.test(event.args.path || '') ? 'synthesis' : event.toolName;
        const target = event.toolName === 'pipeline' ? (event.args.args || []).slice(1).join(' ') : event.args.path || '';
        const phase = input.mode === 'arbitrate' ? 'arbitration' : ['lint', 'ingest-done', 'export-site'].includes(command) ? 'finish' : command === 'synthesis' ? 'synthesis' : ['show-window', 'window-start', 'window-done'].includes(command) ? 'windows' : job.progress?.phase;
        const args = event.args.args || []; const index = args.indexOf('--window');
        update({ command, phase, target, tokens: usage(), updated_at: new Date().toISOString(), ...(index >= 0 ? { window: args[index + 1] } : {}) }); log(`\n[${command}] ${target}\n`);
      }
      if (event.type === 'agent_end') update({ tokens: usage(), updated_at: new Date().toISOString() });
      if (event.type === 'tool_execution_end') {
        if (digestWrites.has(event.toolCallId)) {
          if (!event.isError && fs.readFileSync(digestPath, 'utf8') !== digestWrites.get(event.toolCallId)) {
            batchDigestUpdated = true;
            if (handoff) handoff.digestUpdated = true;
          }
          digestWrites.delete(event.toolCallId);
        }
        lastToolError = event.isError ? (event.result?.content || []).filter((c: any) => c.type === 'text').map((c: any) => c.text).join('\n') : '';
        if (event.isError) log(JSON.stringify(event.result) + '\n');
      }
    });
    return current;
  };
  const usage = () => Object.fromEntries(Object.entries(session.getSessionStats().tokens).map(([key, value]) => [key, value + (previousTokens[key] || 0)]));
  session = await openSession();
  const abort = () => { void session.abort(); };
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const restoreNetwork = models.handleNetwork(rt, log);
  try {
    const reworkFile = path.join(c.root, 'pipeline-workspace/staging', input.source, 'rework-request.md');
    const rework = fs.existsSync(reworkFile) ? fs.readFileSync(reworkFile, 'utf8') : '';
    const action = input.mode === 'repair' ? '用户明确请求按 Review-Queue 中此来源的 lint 报告修复并重新发布。回滚的既有页须重新应用，记账，不要重建 workorder。'
      : input.mode === 'arbitrate' ? '仅完成 phase A.5 分歧仲裁，读取每张所需原图，写 decisions.json，运行 arbitration-apply 后停止，不开始写知识页。'
        : input.mode === 'resume' ? '按以下 CLI 包的 phase 继续：initialize 表示尚未开始读窗写作，先初始化 digest；resume 表示恢复已有进度；finish 表示窗口完成，继续阶段 E 综合整理与阶段 F 发布。不重建 workorder。\n' + resume
          : rework ? 'reopen 已完成。按 ingest 的增量返工分支读取工单和摘要，从 ingest-start 开始，仅修订人工指定的内容，记录本轮实际读取和写入的窗口；不要重建工单。'
            : `预处理与 workorder 已完成，这是首次入库。从 ingest-start 开始，读取 chapters.json、workorder.yaml 和 references/content-routing.md，先在 digest.md 建立逐章路由表与 ## RESUME（尚无已完成窗口、下一窗及 hash 取 next --source ${input.source} --json），再逐窗阅读写作和 lint。不要全量读取 windows.jsonl，不要在初始化摘要之前请求中断恢复包。`;
    const goal = (rework ? '本次目标是完成指定的增量内容返工，保留其余已发布成果，完成本轮记账、ingest-done 和 lint 发布。' : '本次目标是完成全部窗口、综合整理、ingest-done 和 lint 发布（内容验收另行进行）。') + '摘要初始化或单窗完成都只是中间步骤，继续执行，不要等待用户再说继续。若遇到真实错误、无法看图或需要人工决定，明确说明阻塞，不得绕过检查。';
    let sourceContext = '';
    const goalFile = path.join(c.root, 'pipeline-workspace/staging', input.source, 'learning-goal.md');
    if (fs.existsSync(goalFile)) sourceContext += '\n以下学习目标只适用于本来源，不能追加到全局 purpose.md，也不能要求来源没有的内容：\n' + fs.readFileSync(goalFile, 'utf8');
    if (rework) sourceContext += '\n这是人工确认的增量内容返工：按 ingest 的 reopen 分支，仅处理以下内容问题，阅读相关原文与报告；不要把完整入库首轮要求误套到增量返工，也不补写无关内容。记录本轮实际读取和写入的窗口，保留已有成果。\n' + rework;
    let prompt = `本次 source_id=${input.source}。${action}` + (input.mode === 'arbitrate' ? '' : '\n' + goal) + sourceContext;
    let previous = ''; let stalledTurns = 0;
    for (;;) {
      signal.throwIfAborted();
      streamed = false; await session.prompt(prompt);
      log((streamed ? '' : session.getLastAssistantText() || '') + '\n');
      signal.throwIfAborted();
      if (lintFailure) throw new Error('发布检查失败；本次已停止，请查看日志并选择“按报告修复”。\n' + lintFailure);
      const last = [...session.messages].reverse().find(message => message.role === 'assistant');
      if (last?.role === 'assistant' && ['error', 'aborted'].includes(last.stopReason)) {
        if (last.stopReason === 'error' && !handoffFailure && restoreNetwork.canRecover() && recoverableTransportError(last.errorMessage || '') && networkRecoveries < 2) {
          const stats = JSON.parse(await pipeline(c, ['ingest-stats', '--source', input.source, '--json'], signal));
          if (stats.source?.current_status !== 'failed') {
            networkRecoveries++;
            log(`[network-recovery] attempt=${networkRecoveries}/2 wait_ms=${networkRecoveries * 1000}；当前会话从已完成的工具结果继续。\n`);
            await delay(networkRecoveries * 1000, undefined, { signal });
            prompt = `本次 source_id=${input.source}。上轮模型响应因传输中断未完成，沿用本会话中已完成的工具结果继续；不要重放已成功的工具或重建工单。失败响应中的残缺内容不是执行结果。${input.mode === 'arbitrate' ? '仅完成 phase A.5 仲裁，不写知识页。' : goal}${digestRepair || handoff ? '\n' + repairPrompt() : ''}`;
            continue;
          }
        }
        throw new Error(last.errorMessage || 'Model request failed');
      }
      if (/(?:无法|不能)(?:实际)?(?:看图|看到|看见|查看|读取|打开)[^。！\n]{0,20}(?:图片|原图|图像)|(?:无法|不能)看图|(?:cannot|can't|unable to) (?:see|read|open|view) (?:the |these |required )?(?:images?|pictures?)/i.test(session.getLastAssistantText() || '')) throw new Error('模型报告无法读取图片，已停止；请查看执行记录。');
      if (handoffFailure) throw new Error(handoffFailure);
      if (handoffPacket) {
        previousTokens = usage(); session.dispose();
        log(`[handoff] ${handoff!.window} 的 U7 已更新且恢复校验通过；新会话继续，旧图片不进入后续请求。\n`);
        prompt = `本次 source_id=${input.source}。${goal}\n这是同一任务的自动交接，不是新的入库。先完整重读 ingest/references/write-pages.md、chapters.json 和 digest.md，保留全书路由与已完成结论。phase=resume 继续下一窗；phase=finish 继续阶段 E 综合整理，将新增写入并入已完成窗口的 --writes，再阶段 F ingest-done → lint。不能重建工单或重做已完成窗口。\n${handoffPacket}${sourceContext}`;
        handoff = undefined; handoffPacket = ''; lastToolError = ''; previous = ''; stalledTurns = 0; networkRecoveries = 0;
        session = await openSession(); continue;
      }
      if (input.mode === 'arbitrate') {
        const arbitration = JSON.parse(await pipeline(c, ['arbitration-status', '--source', input.source, '--json'], signal));
        if (arbitration.needs_human.length) { update({ waiting: 'human', arbitration }); break; }
        if (!arbitration.pending) { update({ arbitration, waiting: null }); break; }
        if (++stalledTurns >= 3) throw new Error('分歧核对尚未完成，模型连续 3 轮未完成所需裁决。请查看执行记录。');
        prompt = `继续 phase A.5，尚有 ${arbitration.pending} 页未裁决。读取对应原图并提交 decisions.json，运行 arbitration-apply。不要开始知识页写作。${lastToolError ? '\n先核对并修正上次工具错误，不要重复相同失败调用：\n' + lastToolError : ''}`; continue;
      }
      const stats = JSON.parse(await pipeline(c, ['ingest-stats', '--source', input.source, '--json'], signal));
      const state = stats.source;
      if (state?.current_stage === 'lint' && state?.current_status === 'published') {
        if (rework) fs.unlinkSync(reworkFile);
        update({ phase: 'finish', command: 'export-site' }); await run(['export-site', '--format', 'json', '--with-images']); update({ phase: 'published' }); break;
      }
      if (digestRepair || handoff) {
        if (++digestRepairTurns >= 2) throw new Error('摘要仍未完成核对，已停止以避免继续消耗额度。请查看执行记录。\n' + digestRepair);
        prompt = repairPrompt(); continue;
      }
      if (state?.current_status === 'failed') throw new Error('入库已停止，请查看执行记录。\n' + lastToolError);
      const position = `${state?.current_stage}/${state?.current_status}/${stats.windows.finished}`;
      stalledTurns = position === previous ? stalledTurns + 1 : 0; previous = position;
      if (stalledTurns >= 2) throw new Error('模型连续 3 轮回答后，完成窗口数和流水线阶段均未推进，已停止以避免继续消耗额度。请查看执行记录。');
      const progress = `已完成窗口 ${stats.windows.finished}/${stats.windows.planned}，阶段 ${state.current_stage}/${state.current_status}`;
      log(`[continue] ${progress}；尚未发布，在当前会话继续执行。\n`);
      prompt = `本次 source_id=${input.source}，${progress}。${goal}\n沿用当前会话和已保存摘要，从尚未完成的步骤继续，不重建工单，不重做已完成窗口。read/write/edit/files 是独立工具，不是 pipeline 子命令。${lastToolError ? '\n先核对并修正上次工具错误，不要重复相同失败调用，也不能改领域或工单绕过保护：\n' + lastToolError : ''}`;
    }
  } finally { signal.removeEventListener('abort', abort); session.dispose(); restoreNetwork(); }
}
