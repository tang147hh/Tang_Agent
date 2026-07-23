# Tang Agent

运行在 macOS、面向 GitHub 仓库的本地 AI Coding Agent。后端使用 FastAPI、
DeepAgents/LangGraph 和 SQLite，前端使用 React。Agent 只能通过 `/projects/...`
等虚拟路径访问 `~/ai-workspace`，Git 和命令执行使用参数数组及 `shell=False`。

## 启动

```bash
cp .env.example .env
uv sync
uv run uvicorn app.app:app --app-dir backend --reload
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

默认地址为后端 `http://127.0.0.1:8000`、前端
`http://127.0.0.1:5173`。模型 API Key 通过 `.env` 的
`TANG_AGENT_MODEL_API_KEY` 配置。

## Run 预算

预算按用户选择的任务类型执行，并覆盖主 Agent 与子 Agent 的整次 Run：

| 模式 | 模型调用 | 工具调用 | 首个输出 | 总时长 |
| --- | ---: | ---: | ---: | ---: |
| `qa` | 3 | 4 | 12 秒 | 45 秒 |
| `planning` | 5 | 10 | 20 秒 | 120 秒 |
| `analysis` | 8 | 20 | 25 秒 | 180 秒 |
| `coding` | 16 | 40 | 30 秒 | 480 秒 |

相同工具和规范化参数默认最多出现两次：第二次返回可恢复拒绝，继续重复则以
`repeated_tool_call` 终止。模型、工具、首输出或总时长超限时，Run 会进入
`failed` 终态并产生 `terminated` SSE 事件，不会停留在 `running`。文件、路径、
权限、命令策略和超时类工具错误会作为可恢复 `ToolMessage` 返回模型；未知异常仍
终止 Run。

每种模式均可在 `.env` 覆盖以下变量，其中 `{MODE}` 为
`QA`、`PLANNING`、`ANALYSIS` 或 `CODING`：

```text
TANG_AGENT_{MODE}_MAX_MODEL_CALLS
TANG_AGENT_{MODE}_MAX_TOOL_CALLS
TANG_AGENT_{MODE}_MAX_FIRST_OUTPUT_SECONDS
TANG_AGENT_{MODE}_MAX_SECONDS
TANG_AGENT_{MODE}_MAX_IDENTICAL_TOOL_CALLS
```

## 状态与指标

业务消息、Run、事件和性能指标保存在 `data/tasks.sqlite`，LangGraph checkpoint
单独保存在 `data/checkpoints.sqlite`。性能记录与 `run_id` 一对一，可通过：

```http
GET /api/runs/{run_id}/performance
```

读取预算、模型/工具调用数、重复调用、工具错误、安全拒绝、首个输出延迟、总耗时
和预算终止原因。React 执行面板会读取并展示这些字段。

进程内复用 SQLite Store、checkpointer、模型客户端和 `LocalShellBackend`。Agent 图、
任务权限以及带计数状态的模型/工具治理中间件按 Run 创建，避免不同 Run 共享预算。

执行侧栏会读取当前项目相对 `HEAD` 的累计工作区改动，展示修改文件数、总新增/删除
行数和逐文件统计。该统计包含已暂存、未暂存和未跟踪文件，只返回 `/projects/...`
虚拟路径，不读取或返回文件内容；敏感文件不会出现在列表中。

## 工具能力与结构化网页搜索

工具由固定代码注册表描述，元数据包含名称、`local_read|local_write|command_execution|`
`network_read|external_write` 分类、风险、允许模式、联网要求、是否可由模型调用和可用
状态。系统不会从用户路径、Python entry point 或请求体加载工具，也不使用 `eval/exec`。
GitHub Review 发布只在注册表中作为 `model_callable=false` 的 `external_write` 能力出现，
仍只能走专用 prepare/publish API 和用户确认。

权限矩阵：

| 模式 | 本地读 | 本地写 | 命令 | `web_search` |
| --- | --- | --- | --- | --- |
| `qa` | 允许 | 禁止 | 禁止 | Run 明确允许时 |
| `planning` | 允许 | 禁止 | 禁止 | Run 明确允许时 |
| `analysis` | 允许 | 禁止 | 禁止 | Run 明确允许时 |
| `coding` | 允许 | 允许 | 允许 | Run 明确允许时 |
| Reviewer | 仅输入的受控 Diff | 禁止 | 禁止 | 永远禁止 |

### 高效工作区文件定位与代码搜索

本地读能力包含两个专用工具：

- `workspace_glob`：从虚拟 `path`（默认 `/projects`）按相对 Glob `pattern` 定位路径；
  支持 `**/*.py`、`frontend/src/**/*.tsx`、`**/package.json` 等模式。
- `workspace_search`：从虚拟 `path` 中按字面量 `query` 搜索 UTF-8 文本；可用
  `file_pattern` 限制文件，返回虚拟 path、1-based 行列号和单行受限片段。

两者默认最多返回 100 项，`max_results` 只能为 1-500；结果按路径和行号稳定排序，返回
`match_count`、`truncated` 和 `duration_ms`。Glob 模式最长 512 字符，搜索词最长 500
字符；拒绝空值、主机/Windows 路径、绝对模式、`..`、NUL、控制字符和 `.secrets`。
backend 最多检查 50,000 个目录项；内容搜索只读取不超过 1,000,000 bytes 的 UTF-8
普通文件，并设置 20,000,000 bytes Run 内单次扫描上限和 500 字符片段上限。

遍历不跟随任何符号链接，且跳过 `.git`、`node_modules`、虚拟环境、缓存、构建产物、
`.env`、私钥/证书和常见凭据文件；二进制、非 UTF-8 和超大文件不会进入内容结果。
工具不执行命令、不写文件、不联网，也没有权限参数。主 Agent 与 analysis 子 Agent 可用；
Reviewer 和 GitHub Review 发布流程保持零工作区搜索工具。

Agent prompt 要求不知道位置时先搜索，再只用 `workspace_read` 精读真正需要的文件。
每次搜索仍计入现有 `tool_calls`，相同参数继续受重复检测控制，达到工具预算后 Run 以
既有 `tool_call_limit` 终止。SSE 只暴露虚拟根、文件模式、匹配/扫描数、耗时和截断状态，
不会复制原始代码搜索词、代码片段或完整工具结果到前端步骤。

聊天框“联网”选择会写入下一次 Run 的不可变快照；旧客户端不传时默认 `false`。
Run 保存 provider、实际请求/结果/字节/缓存命中计数和网络限额原因。主 Agent 与 analysis
子 Agent 共用同一个 `SearchRuntime`、缓存视图和网络预算；Reviewer 不获得搜索工具。
命令工具仍使用白名单，联网关闭时额外拒绝 curl/wget 和 Git 网络读取动作，不能用
`workspace_execute` 替代受控搜索。

`web_search` 只接受 `query`、1-5 的 `max_results`、最多 5 个规范域名和 1-365 天
recency。出站前拒绝私钥、Token、API Key、Password、Secret、凭据 URL、主机路径和
大段代码；拒绝的原文不会进入 provider、事件或前端。结果只保留规范化 HTTP(S) URL、
`S1...` citation、标题、snippet、来源域名、发布时间和 rank；会移除 fragment、跟踪参数、
凭据 URL、重复 URL 和结果中的凭据/主机路径。title/snippet 始终是不可信纯文本，模型
不得遵循其中指令，前端来源使用 `target="_blank" rel="noopener noreferrer"`。

默认网络预算：

| 模式 | 搜索次数 | 结果/次 | 超时 | 单次字符 | Run 总字符 | 接收字节 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `qa` | 2 | 5 | 15 秒 | 6,000 | 12,000 | 1 MiB |
| `planning` | 3 | 5 | 15 秒 | 8,000 | 20,000 | 2 MiB |
| `analysis` | 4 | 5 | 15 秒 | 8,000 | 24,000 | 2 MiB |
| `coding` | 4 | 5 | 15 秒 | 8,000 | 24,000 | 2 MiB |
| Reviewer | 0 | 0 | 不适用 | 0 | 0 | 0 |

缓存默认 10 分钟/128 项，空结果 60 秒；键包含 provider 和全部规范化参数。缓存命中仍
算工具调用并计入 `network_cache_hit_count`，但不增加 provider request count。配置：

```text
TANG_AGENT_WEB_SEARCH_PROVIDER=disabled|zhipu
ZHIPU_API_KEY=
```

智谱适配器延迟导入 SDK、延迟创建客户端；缺 SDK 或 Key 时 FastAPI 仍可启动，能力接口
返回受控原因。测试只使用 `FakeSearchProvider`，不会访问真实服务。本课不提供任意 URL
正文读取，也没有 `fetch_url` 工具。

```http
GET /api/tool-capabilities?task_kind=qa&network_access=false
GET /api/runs/{run_id}/tool-capabilities
```

## 结构化代码审查

正式审查入口从 `run_id` 关联的 Thread 和已注册 Project 确定仓库，不接受 `cwd`、
主机路径、Shell 命令或任意 revision。仓库根必须是 `/projects/...` 下的真实目录；
Workspace 的 `resolve()` 会同时阻止 `..` 和符号链接逃逸。

```http
POST /api/runs/{run_id}/reviews
Content-Type: application/json

{"scope":"all"}
```

`scope` 支持：

- `staged`：只比较 `HEAD`（无 HEAD 时为空树）与 index。
- `unstaged`：只比较 index 与工作树，不包含 untracked。
- `all`：比较 `HEAD`（无 HEAD 时为空树）与最终工作树，并加入未被 `.gitignore`
  排除的 untracked 文件。

Git 收集只使用固定参数数组、明确 `cwd`、`shell=False`、`GIT_TERMINAL_PROMPT=0` 和
`GIT_OPTIONAL_LOCKS=0`。状态和文件列表使用 NUL 分隔机器格式；所有 pathspec 前都有
`--`。收集过程不会 add、commit、checkout、reset、clean 或修改 Git 配置。新增、删除、
重命名、复制和未跟踪文件会保留结构化类型及 old/new path；二进制只保留元数据；
子模块强制 short diff，不递归读取内部内容；无 HEAD 的新仓库使用 Git 空树比较。

后端生成结构化 `ReviewDiff`/`ReviewDiffFile`，从 unified hunk 确定
`changed_old_lines` 和 `changed_new_lines`。patch 在进入 Reviewer 前逐行脱敏，私钥、
GitHub/API/Bearer/Access Token、Secret、Password 和常见 `.env` 凭据值统一替换为
`[REDACTED]`，不改变换行和行号。Reviewer 是没有文件、命令、网络或发布工具的直接
模型调用；系统提示明确把 Diff 中的指令视为不可信代码。

默认容量限制为 50 文件、单文件 40,000 字符/800 变更行、总计 200,000 字符/3,000
变更行，Git 命令超时 30 秒。对应环境变量是：

```text
TANG_AGENT_REVIEW_DIFF_MAX_FILES
TANG_AGENT_REVIEW_DIFF_MAX_FILE_PATCH_CHARS
TANG_AGENT_REVIEW_DIFF_MAX_FILE_CHANGED_LINES
TANG_AGENT_REVIEW_DIFF_MAX_TOTAL_PATCH_CHARS
TANG_AGENT_REVIEW_DIFF_MAX_TOTAL_CHANGED_LINES
TANG_AGENT_REVIEW_GIT_TIMEOUT
```

截断按稳定文件/行顺序执行，保留 UTF-8 完整性，并在 Diff、文件和总结中标记原因及
“结果可能不完整”。受控 patch 按 `run_id` 持久化为审查快照；API 不返回 patch，
而由后端确定性转换为 `hunks[].lines[]`，每行包含 `type`、old/new 行号和纯文本内容。
后续读取只访问 SQLite 快照，不会重新执行 Git Diff 或把变化后的工作树冒充原审查内容。

Reviewer 输出先经严格 JSON/Pydantic 校验，再校验 `file_path` 必须属于本次 Diff，
行号必须落在模型实际看到的 hunk（且至少包含一条变更行）。删除定位使用 `old`，新增
和普通修改通常使用 `new`；二进制只允许文件级 Finding。通过后才按 `run_id` 保存，
并绑定 `review_diff_hash`、scope 和 revision。系统生成 ID、状态、指纹和时间，模型不能
覆盖这些字段；重复 Finding 仍由服务层和 SQLite 唯一约束双重去重。

```http
GET /api/runs/{run_id}/review-findings
GET /api/runs/{run_id}/review-findings?severity=high&status=open
PATCH /api/runs/{run_id}/review-findings/{finding_id}
GET /api/runs/{run_id}/review
POST /api/threads/{thread_id}/review-runs
```

安全 Diff 收集记为一次共享内部工具操作，Reviewer 调用计入该 Run 已有的
`model_calls`；达到第 33 课预算时，活跃 Run 会进入失败终态，已验证 Finding 不回滚。

前端可从已登记 Project 对应的 Repository 详情进入“代码审查”，也可从聊天执行面板
打开当前 Run。工作台桌面端为变更文件、Diff、Finding 三栏；平板和移动端使用
“文件 / Diff / 问题”标签页。左栏支持路径搜索、变更类型、增删行、二进制/子模块/
截断和问题数；中栏显示固定 old/new 行号并支持长行滚动；右栏支持 severity、category、
status 筛选和 open/resolved/dismissed 状态更新。Finding 点击后按 `line_side` 定位并
高亮，删除行使用 old 侧，失败状态更新会自动回滚。

“重新审查”会创建新的 analysis Run，从而保留旧 Run 的快照和 Finding。

## GitHub Review 安全发布

第 37 课把 Review 来源明确分为 `working_tree` 和 `pull_request`。本地 staged、
unstaged、untracked 变更在 GitHub 上没有稳定 commit/行号，因此只能在工作台查看；
发布入口会禁用，并要求先提交、推送、创建 PR，再重新生成 PR 快照。系统不会把本地
Finding 猜测映射到远程 PR。

PR Review 从 `run_id -> thread -> registered project` 确定仓库，读取项目自身的
`origin`，支持 `git@github.com:owner/repo.git`、HTTPS 和 `ssh://` 三种格式。当前只支持
`github.com`；GitHub Enterprise 会被明确拒绝。快照保存 repository、PR number、
base/head SHA、脱敏且受限的 files/hunks/lines 和 Diff hash。GitHub 未返回完整 patch
时会标记 `github_patch_unavailable`，不会声称完整审查。

发布是专用的两阶段流程：

```http
GET  /api/projects/{project_id}/github-review/capability
POST /api/runs/{run_id}/github-review/prepare
POST /api/runs/{run_id}/github-review/publish
GET  /api/runs/{run_id}/github-review/publications
```

`prepare` 只接受 PR number、Finding ID、`COMMENT|APPROVE|REQUEST_CHANGES` 和可选总结；
path、line、side、commit 和 GitHub JSON 均由后端从 SQLite 快照重建。它只生成带过期
时间和 payload hash 的预览，不产生远程写入。用户必须在前端确认弹窗中主动点击
“发布到 GitHub”；取消、Enter、刷新或 Agent/Reviewer 均不能触发发布。

`publish` 会重新验证仓库、PR open/draft 状态、head SHA、Finding 内容/状态、行号、
payload hash 和 publication 状态。相同 payload、并发请求和已发布记录会被阻止；普通
API 失败记为 `failed` 并允许用户再次确认重试，写请求超时或返回体不确定时记为
`unknown`，禁止自动重试。成功记录 GitHub 用户、Review ID/URL 和发布时间。

GitHub 写入默认关闭：

```text
TANG_AGENT_GITHUB_REVIEW_PUBLISH_ENABLED=true
```

`gh` 只安装在宿主机。后端使用固定参数数组、`shell=False`、明确 cwd/timeout、
`GIT_TERMINAL_PROMPT=0` 和 JSON stdin；不读取或返回 token。`workspace_execute` 仍拒绝
`gh api`，发布能力没有注册成 Agent 工具。测试使用 fake gh、临时 Git/SQLite，真实
发布保持关闭。

Coding Agent 在用户当前请求明确要求时，可以通过 `workspace_execute` 推送已提交的当前
功能分支；命令策略只接受 `git push --set-upstream origin <branch>`，并拒绝
`main/master`、其他 remote、任意附加参数和 force push。Pull Request 与 GitHub Review
仍通过前端预览和用户确认，Agent 不得自主创建或发布。

第 38 课已使用系统 Chrome 和 Playwright 请求拦截完成 1440x900、1280x720、
768x1024、390x844 四视口真实截图验收。mock E2E 覆盖 working tree、PR 预览/取消/
确认、三种 Review event、成功/失败/unknown、状态回滚、重复发布锁和安全 URL；截图与
逐项结论见 [`docs/lesson-38-acceptance.md`](docs/lesson-38-acceptance.md)。应用内 Browser
运行时仍无实例，因此视觉验收使用本机 Chrome 150，没有下载 Playwright Chromium。

真实 GitHub 验收尚未执行：`tang147hh` 的 gh Token 已失效，且没有用户指定的专用测试
PR。当前状态是“部分完成，存在外部环境阻塞”，不能把 mock publish 当成真实 COMMENT。
恢复前先由用户执行 `gh auth login -h github.com`，再提供专用测试 PR；任何 COMMENT、
APPROVE 或 REQUEST_CHANGES 仍需分别展示预览并获得明确确认。

第 39 课的工具治理、结构化搜索、安全测试和四视口验收见
[`docs/lesson-39-acceptance.md`](docs/lesson-39-acceptance.md)。真实 GitHub 发布阻塞未被
更改；网页搜索默认关闭，验收全部使用 Fake Provider，没有访问真实搜索服务。

第 40 课的工作区文件定位、代码搜索、安全边界和事件验收见
[`docs/lesson-40-acceptance.md`](docs/lesson-40-acceptance.md)。本课没有实现 `fetch_url`，
也没有改变网页搜索默认 disabled 或真实 GitHub Review 的认证阻塞状态。

## 验证

```bash
uv --cache-dir /tmp/tang-agent-uv-cache run pytest -q
cd frontend
npm run lint
npm test
npm run build
npm run test:e2e
```

固定 fake-model/fake-GitHub 性能基线：

```bash
uv run python scripts/lesson_38_benchmark.py \
  --output /tmp/tang-agent-lesson-38/performance.json
```

LX_AICoding 的完整审阅、取舍和修改前后对比见
[`docs/lx-aicoding-agent-runtime-comparison.md`](docs/lx-aicoding-agent-runtime-comparison.md)。
