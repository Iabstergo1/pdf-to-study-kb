/** 追加到系统提示的环境说明：不改 skill 文件（双树字节对等守卫），只在这里说明网页环境与 skill 文本的差异。 */
export const WEB_ENV_ADDENDUM = `## Environment notes (web console)
- You cannot run shell commands. Run every \`scripts/pipeline.py\` subcommand with the \`pipeline\` tool: pass the arguments as an array WITHOUT "python" or the script path (for example args: ["show-window", "--source", "<src>", "--window", "<id>"]). Ignore PowerShell, \`$env:\` and interpreter instructions in the skill: the tool already sets them.
- Read files (text and images) with the \`read\` tool. Page images listed by \`show-window\` MUST be opened with \`read\` and actually looked at before you write from them. If you cannot see an image, stop and say so instead of writing from the text alone.
- Pass JSON arguments such as \`--writes '["wiki/…"]'\` inline as ONE string element of \`args\`; there is no shell quoting problem here, so never write temporary files for them (\`--writes-file\` is not needed).
- Create and edit files only with the \`write\` and \`edit\` tools, only under \`wiki/\` (plus the digest at \`pipeline-workspace/staging/<src>/digest.md\` and, for PDF arbitration, \`pipeline-workspace/staging/<src>/arbitration/decisions.json\`).
- The user has already confirmed source_id and domain, and preprocessing (add-source through workorder) is finished: start at \`ingest-start\`.
- Never declare the content accepted. When you finish, report: "published, pending content acceptance".`;

export function ingestUserPrompt(p: { sourcePath: string; domain: string; sourceId: string }): string {
  return `把 ${p.sourcePath} 加进知识库，领域 ${p.domain}，source_id ${p.sourceId}。预处理已完成，请按 ingest skill 从 ingest-start 开始，写完并通过 lint。`;
}
