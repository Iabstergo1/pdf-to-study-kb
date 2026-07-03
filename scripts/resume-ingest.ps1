#!/usr/bin/env pwsh
# 无人值守续跑（跨订阅限额复位窗口）。不是"一次跑完"，而是对持久化状态的收敛重试：
# OS 任务计划程序/cron 按「> 5h 复位窗口」的间隔调本脚本——仅当存在进行中 ingest 时才唤起
# 所选 agent 的 headless 模式续跑。每次都是无记忆新会话，但 ingest 进度落在磁盘上
# （ingest_progress + proposed 页 + digest），新会话靠 `pipeline.py next` + digest RESUME 块
# 重新定位到下一个未完成 window。落在冻结期的那次空转/失败退出，下一次（已复位）成功，单调收敛。
# 第三方 API key（按 token 计费、无 5h 窗口）同样适用，只是不遇冻结。
#
# 两个 agent 各用自身正确的 headless + 非交互权限方式（-Agent 选其一；同一 vault 同刻只许一个
# ingest，别同时给 claude 和 codex 各注册一个指向同库的任务）：
#   · claude → claude -p "<prompt>" --dangerously-skip-permissions
#             （或 --permission-mode acceptEdits + 在 permissions.allow 放行 Bash(python scripts/pipeline.py:*)）
#   · codex  → codex exec --sandbox workspace-write "<prompt>"
#             （无人值守默认：最小权限、仅写 workspace；该机沙箱挡路写不动库时加 -Bypass 改用
#              codex exec --dangerously-bypass-approvals-and-sandbox "<prompt>"）
#
# 关键前提（缺一则"自动"会断）：所选 agent 已登录且在 PATH；机器在 fire 时醒着（睡眠需唤醒定时器、
# 笔记本需允许电池下运行——下面注册命令已带这些设置）。
#
# 注册（Windows 任务计划程序，每 6 小时一次，含唤醒/补跑/电池设置；-Agent 改 codex 即注册 Codex 版）：
#   $a = New-ScheduledTaskAction    -Execute "pwsh" -Argument "-NoProfile -File `"$PWD\scripts\resume-ingest.ps1`" -Agent claude"
#   $t = New-ScheduledTaskTrigger   -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6)
#   $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
#   Register-ScheduledTask -TaskName "study-kb-resume" -Action $a -Trigger $t -Settings $s -Description "续跑中断的 study-kb ingest"
#   # 取消： Unregister-ScheduledTask -TaskName "study-kb-resume" -Confirm:$false
#   # 睡眠唤醒还需系统电源选项允许"唤醒定时器"（合盖睡眠默认可能不唤醒）。
#
# 注册（Unix cron，每 6 小时一次，需已装 pwsh）：
#   0 */6 * * *  cd /path/to/pdf-to-study-kb && pwsh scripts/resume-ingest.ps1 -Agent claude
[CmdletBinding()]
param(
    [ValidateSet("claude", "codex")][string]$Agent = "claude",
    [string]$Python = $env:STUDY_KB_PYTHON,    # 留空则用 PATH 上的 python（须能跑 pipeline.py）
    [int]$MaxWindows = 4,                       # 本次触发有界处理的 window 上限（注入 prompt；避免单次长会话整体失败）
    [switch]$Bypass,                           # codex: 沙箱写不动库时的逃生开关 → --dangerously-bypass-approvals-and-sandbox
    [switch]$Sandbox                           # 兼容旧注册命令；Codex 无人值守默认已是 --sandbox workspace-write
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot        # 仓库根 = scripts/ 的上一级
Set-Location $Root
if (-not $Python) { $Python = "python" }

$log = Join-Path $Root "tmp/resume.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log([string]$m) { "$([DateTime]::Now.ToString('s'))  [$Agent] $m" | Add-Content -Path $log -Encoding utf8 }

# 0) 所选 agent 可用？不可用直接记录退出（否则唤起即失败、无从排查）。
if (-not (Get-Command $Agent -ErrorAction SilentlyContinue)) { Log "$Agent 不在 PATH，跳过"; exit 1 }

# 1) 有没有进行中的 ingest？没有就别白唤起 agent。
$status = (& $Python "scripts/pipeline.py" status 2>$null) -join "`n"
if ($status -notmatch "ingesting") { exit 0 }

# 2) 防重入：上一次续跑还在跑就跳过（pipeline 的 source_locks 也会兜底拒绝并发 ingest）。
$lock = Join-Path $env:TEMP "study-kb-resume.lock"
if (Test-Path $lock) {
    if (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalHours -lt 12) { Log "上一次续跑仍活跃，跳过"; exit 0 }
}
New-Item -ItemType File -Path $lock -Force | Out-Null

# 3) 唤起所选 agent 的 headless 续跑。被限额时本次非零退出、空转，下一次（复位后）成功。
$prompt = "继续未完成的 ingest：读 pipeline-workspace/staging 下对应来源 digest.md 顶部的 `"## ⏩ RESUME`" 块、并跑 python scripts/pipeline.py next，从下一个未完成 window 接着逐窗跑。本次最多处理 $MaxWindows 个 window（处理完这几窗后干净退出，剩余留给下次触发）。若本次触发内已把最后一个 window 跑完，则必须先做阶段 E 综合层（更新 overview + 按需建 topic/comparison/synthesis，并进某窗 --writes），再 ingest-done → lint——漏做 lint 会 L7-synthesis-missing 阻断。先设环境变量 PYTHONUTF8=1。遇问题直接修，不要等人确认。"
switch ($Agent) {
    "claude" { $exe = "claude"; $invokeArgs = @("-p", $prompt, "--dangerously-skip-permissions") }
    "codex"  { $exe = "codex"
               if ($Bypass -and $Sandbox) { throw "Codex 的 -Bypass 与 -Sandbox 不能同时使用" }
               $invokeArgs = if ($Bypass) {
                   @("exec", "--dangerously-bypass-approvals-and-sandbox", $prompt)
               } else {
                   @("exec", "--sandbox", "workspace-write", $prompt)
               } }
}
Log "唤起 $exe $($invokeArgs[0]) …"
try {
    & $exe @invokeArgs
    Log "$exe 退出码=$LASTEXITCODE（非 0 多半是被限额，下次复位后重试）"
} finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
