# Tang Agent 前后端接口与前端功能规划

更新日期：2026-07-23

## 1. 文档目标

本文档说明 Tang Agent 当前已经向前端提供的 HTTP/SSE 接口、前端需要实现的功能、尚未具备的后端能力，以及推荐的前端实施顺序。

新版前端的核心产品结构为：

```text
左侧导航栏
├── 项目列表
├── 项目下的会话
├── 新建对话
├── Repositories 页面入口
└── Skills 页面入口

主内容区
├── 用户与 Agent 的多轮对话
├── Agent 当前执行状态
├── Agent 执行步骤
├── 已完成步骤划线
└── 底部消息输入框
```

## 2. 接口总览

### 2.1 系统接口

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| GET | `/health` | 检查后端是否正常运行 |

返回：

```json
{
  "ok": true
}
```

### 2.2 旧版单任务接口

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| POST | `/api/tasks` | 创建一次独立 Agent 任务 |
| GET | `/api/tasks/{thread_id}` | 查询独立任务状态与结果 |
| GET | `/api/tasks/{thread_id}/events` | 订阅独立任务 SSE |

这组接口服务于旧版单任务 Dashboard。新版多项目、多会话前端应主要使用 Project、Thread、Message 和 Run 接口。

## 3. Project 接口

### 3.1 获取项目列表

```http
GET /api/projects
```

响应：

```json
[
  {
    "project_id": "uuid",
    "name": "Tang Agent",
    "virtual_path": "/projects/tang-agent",
    "created_at": "2026-07-22T00:00:00+00:00",
    "updated_at": "2026-07-22T00:00:00+00:00"
  }
]
```

前端用途：渲染项目列表、切换当前项目、加载项目下的会话。

### 3.2 登记项目

```http
POST /api/projects
Content-Type: application/json
```

请求：

```json
{
  "name": "Tang Agent",
  "virtual_path": "/projects/tang-agent"
}
```

响应状态为 `201 Created`。

约束：

- `virtual_path` 必须是 `/projects` 下的直接子目录。
- 对应目录必须已经存在。
- 重复登记返回 `409 Conflict`。
- 非法路径或不存在的目录返回 `422 Unprocessable Content`。
- 该接口只登记项目，不负责创建目录或克隆 GitHub 仓库。

## 4. Thread 接口

### 4.1 获取项目下的会话

```http
GET /api/projects/{project_id}/threads
```

响应：

```json
[
  {
    "thread_id": "uuid",
    "project_id": "uuid",
    "title": "实现登录功能",
    "status": "idle",
    "created_at": "2026-07-22T00:00:00+00:00",
    "updated_at": "2026-07-22T00:00:00+00:00"
  }
]
```

Thread 状态：

| 状态 | 含义 |
| --- | --- |
| `idle` | 当前没有 Agent 执行，可以发送新消息 |
| `running` | 当前会话有 Agent 正在执行 |
| `error` | 最近一次执行失败 |

### 4.2 新建会话

```http
POST /api/projects/{project_id}/threads
Content-Type: application/json
```

请求：

```json
{
  "title": "新对话"
}
```

响应状态为 `201 Created`。

### 4.3 获取单个会话

```http
GET /api/threads/{thread_id}
```

用于刷新会话标题、项目归属和当前状态。

当前尚未提供会话手动重命名、删除和归档接口。标题为“新对话”的会话在首次发送消息时，会由后端根据首条消息自动生成简洁标题。

## 5. Message 接口

### 5.1 获取会话消息历史

```http
GET /api/threads/{thread_id}/messages
```

响应：

```json
[
  {
    "sequence": 1,
    "message_id": "uuid",
    "thread_id": "uuid",
    "run_id": "uuid",
    "role": "user",
    "content": "分析这个项目",
    "created_at": "2026-07-22T00:00:00+00:00"
  },
  {
    "sequence": 2,
    "message_id": "uuid",
    "thread_id": "uuid",
    "run_id": "uuid",
    "role": "assistant",
    "content": "项目结构如下……",
    "created_at": "2026-07-22T00:00:01+00:00"
  }
]
```

消息角色包括：

```text
user
assistant
system
```

前端必须按照 `sequence` 升序展示消息，不能只依赖时间排序。

## 6. Run 接口

### 6.1 发送消息并启动 Agent

```http
POST /api/threads/{thread_id}/runs
Content-Type: application/json
```

请求：

```json
{
  "content": "修改登录页面并运行测试",
  "task_kind": "coding"
}
```

`task_kind` 可选。新版前端会明确传入用户在输入框下方选择的模式；旧客户端不传时，后端仍根据 `content` 自动分类。

| `task_kind` | 权限与用途 |
| --- | --- |
| `coding` | 可读取、创建和编辑文件，并可执行受控命令 |
| `analysis` | 只读，用于分析项目和代码 |
| `planning` | 只读，用于制定实施方案 |
| `qa` | 只读，用于一般问答 |

权限由后端根据 Run 中持久化的 `task_kind` 强制执行，前端选择器不能自行扩大权限。

响应状态为 `202 Accepted`：

```json
{
  "run": {
    "run_id": "uuid",
    "thread_id": "uuid",
    "task_kind": "coding",
    "status": "pending",
    "error": null,
    "created_at": "2026-07-22T00:00:00+00:00",
    "updated_at": "2026-07-22T00:00:00+00:00"
  },
  "message": {
    "sequence": 3,
    "message_id": "uuid",
    "thread_id": "uuid",
    "run_id": "uuid",
    "role": "user",
    "content": "修改登录页面并运行测试",
    "created_at": "2026-07-22T00:00:00+00:00"
  }
}
```

该接口原子完成：

```text
创建 pending Run
+ 保存 user Message
+ 将 Thread 更新为 running
+ 调度后台 Agent
```

同一个 Thread 已有 `pending/running` Run 时返回 `409 Conflict`。

### 6.2 获取会话 Run 历史

```http
GET /api/threads/{thread_id}/runs
```

### 6.3 获取单个 Run

```http
GET /api/runs/{run_id}
```

Run 状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 等待执行 |
| `running` | Agent 正在执行 |
| `completed` | 本轮成功完成 |
| `failed` | 本轮执行失败 |
| `cancelled` | 本轮已取消；目前没有取消接口 |

Run 的列表和详情响应都会返回 `task_kind`，前端可据此展示历史 Run 实际使用的权限模式。

### 6.4 Thread 对话历史与 Run 状态隔离

`thread_id` 和 `run_id` 承担不同职责：

| 标识 | 生命周期 | 用途 |
| --- | --- | --- |
| `thread_id` | 整个业务会话 | 关联项目、消息历史和历次 Run |
| `run_id` | 单次 Agent 执行 | 隔离本轮 LangGraph 检查点和运行状态 |

SQLite `tasks.sqlite` 中按 `sequence` 保存的 Message 是对话历史的权威来源。每次启动 Agent 时，后端会从这些 Message 重建完整的 `user`、`assistant` 和 `system` 消息列表；只有本轮用户消息会额外加入当前项目虚拟路径与项目边界说明。

LangGraph 的 `checkpoints.sqlite` 使用 `run_id` 作为检查点 `thread_id`。因此，同一业务 Thread 内从 `qa` 或 `planning` 切换到 `coding` 时，新 Run 可以继续理解历史对话，但不会复用旧 Run 的只读 Agent 状态。该实现属于后端内部约束，前端仍只需保存接口返回的业务 `thread_id` 和 `run_id`。

## 7. Run SSE 接口

```http
GET /api/runs/{run_id}/events
```

浏览器订阅：

```typescript
const source = new EventSource(
  `/api/runs/${encodeURIComponent(runId)}/events`,
)
```

当前 Conversation Run 事件：

```text
created
running
token
tool_started
tool_finished
completed
failed
```

事件来源：

| `source` | 含义 |
| --- | --- |
| `system` | Run 生命周期事件 |
| `main` | 主 Agent 的文本或工具调用 |
| `subagent:{call_id}` | 某次子 Agent 委派中的文本或工具调用 |

事件数据示例：

```json
{
  "run_id": "uuid",
  "source": "system",
  "created_at": "2026-07-22T00:00:00+00:00",
  "status": "running",
  "task_kind": "coding"
}
```

`created` 和 `running` 生命周期事件会携带 `task_kind`；文本和工具事件不重复携带该字段。

工具事件示例：

```json
{
  "run_id": "uuid",
  "source": "subagent:call_subagent",
  "created_at": "2026-07-23T00:00:00+00:00",
  "name": "workspace_read",
  "tool_call_id": "call_read",
  "subagent": "general-purpose"
}
```

`tool_started` 与 `tool_finished` 使用同一个 `tool_call_id`，前端应将它们合并为同一条步骤。子 Agent 的 Token 只用于过程展示，不应累加到最终 Assistant 回复。

失败事件可能包含：

```json
{
  "run_id": "uuid",
  "source": "system",
  "created_at": "2026-07-22T00:00:00+00:00",
  "status": "failed",
  "error": "任务执行失败，请查看服务日志"
}
```

接口支持 SSE `Last-Event-ID` 续传。后端将其转换为 SQLite `after_id`，只返回游标之后的事件。

Conversation Run 已持久化工具和子 Agent 事件；SSE 断线重连后仍可通过 `Last-Event-ID` 从 SQLite 游标继续读取。

## 8. 前端页面结构

```text
┌─────────────────┬──────────────────────────────────┐
│ 左侧导航栏       │ 主内容区                          │
│                 │                                  │
│ Projects        │ 当前项目 / 当前会话               │
│ ├─ Project A    │                                  │
│ │  ├─ Thread 1  │ 对话消息                         │
│ │  ├─ Thread 2  │ ├─ User                         │
│ │  └─ 新对话     │ ├─ Agent 执行步骤                │
│ ├─ Project B    │ └─ Assistant                    │
│                 │                                  │
│ Skills          │ 底部输入框                       │
└─────────────────┴──────────────────────────────────┘
```

## 9. 前端核心功能

### 9.1 项目导航

- 启动时请求 `GET /api/projects`。
- 展示项目列表和当前项目高亮。
- 支持项目折叠、展开和切换。
- 切换项目后加载对应会话。
- 提供无项目空状态和登记项目表单。
- 第一版登记表单输入项目名称和 `/projects/...` 虚拟路径。

### 9.2 会话导航

- 请求 `GET /api/projects/{project_id}/threads`。
- 展示项目下的所有会话。
- 当前会话高亮。
- 提供“新建对话”按钮。
- `running` 会话显示运行标识。
- `error` 会话显示错误标识。
- 按后端返回顺序展示最近更新的会话。

### 9.3 对话历史

选择会话后并行请求：

```http
GET /api/threads/{thread_id}
GET /api/threads/{thread_id}/messages
GET /api/threads/{thread_id}/runs
```

主区需要支持用户、Assistant、System 消息，加载状态、空会话欢迎页和执行错误提示。

### 9.4 发送消息

用户提交后调用：

```http
POST /api/threads/{thread_id}/runs
```

前端随后：

输入框下方提供模式选择按钮。点击后在按钮上方弹出 `coding`、`analysis`、`planning`、`qa` 四个选项，当前选项保持高亮；前端将选中的值作为 `task_kind` 随消息发送。默认模式为 `coding`。

1. 立即加入接口返回的 user Message。
2. 保存返回的 `run_id` 和后端确认的 `task_kind`。
3. 禁用输入框，防止同一会话重复运行。
4. 建立 Run EventSource。
5. 展示流式回答和执行步骤。

Planning Run 完成后，前端在对应 Assistant Message 下方显示“按此方案实施”。关联必须使用 Message 与 Run 共有的 `run_id`，不能只判断最后一条 Assistant Message。点击按钮后：

1. 将输入框模式切换为 `coding`。
2. 预填“请按照上面的方案开始实施，并在完成后运行相关测试。”。
3. 聚焦输入框，等待用户检查或修改请求。
4. 不自动发送，不在用户确认前创建 Coding Run。

如果该 Planning Run 之后已经出现 Coding Run，或当前会话有 Run 正在执行，前端不再显示实施按钮。该流程直接复用 SQLite Message 历史和 Run 级 Checkpoint 隔离，不需要新增审批接口。

### 9.5 实时 Agent 状态

建议分别保存：

```typescript
messages    // 已持久化的权威消息
activeRun   // 当前执行的 Run
streamText  // 尚未完成的临时 Assistant 文本
steps       // 执行步骤
connection  // SSE 连接状态
```

事件处理：

| 事件 | 前端行为 |
| --- | --- |
| `created` | 增加“任务已创建”步骤 |
| `running` | 增加“Agent 正在执行”步骤 |
| `token` | 累加临时 Assistant 回答 |
| `completed` | 完成步骤划线，关闭 SSE，刷新权威数据 |
| `failed` | 标记失败，显示安全错误，刷新权威数据 |

步骤数据建议使用：

```typescript
interface AgentStep {
  id: string
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  detail?: string
}
```

### 9.6 终态校准

收到 `completed/failed` 后执行：

```text
关闭 EventSource
    ↓
GET /api/runs/{run_id}
    ↓
GET /api/threads/{thread_id}/messages
    ↓
GET /api/threads/{thread_id}/runs
    ↓
GET /api/threads/{thread_id}
```

SSE 用于过程展示，GET 接口返回的数据是最终权威状态。

### 9.7 Skills 页面

Skills 页面使用两个只读接口。

获取 Skill 摘要列表：

```http
GET /api/skills
```

响应：

```json
[
  {
    "name": "repo-analysis",
    "description": "分析陌生代码仓库",
    "path": "/skills/repo-analysis/SKILL.md"
  }
]
```

列表响应不包含完整正文。用户选中 Skill 后再请求详情：

```http
GET /api/skills/{skill_name}
```

详情响应：

```json
{
  "name": "repo-analysis",
  "description": "分析陌生代码仓库",
  "path": "/skills/repo-analysis/SKILL.md",
  "content": "# Repository Analysis"
}
```

约束：

- 所有路径都是 `/skills/...` 虚拟路径，不暴露真实磁盘路径。
- Skill 不存在时返回 `404 Not Found`。
- Skill 名称不合法时返回 `422 Unprocessable Content`。
- 当前接口只支持查看，不支持创建、编辑或删除 Skill。
- 前端列表支持搜索、选择、加载状态、错误重试和 Markdown 正文预览。

### 9.8 Repositories 页面

Repositories 页面使用第 10 节的仓库接口，并提供以下功能：

- 扫描并展示 `/projects` 下的本地 Git 仓库。
- 按仓库名称、虚拟路径和远程地址搜索。
- 展示当前分支、本地分支、工作区是否有未提交修改和脱敏后的 origin。
- 输入明确的 GitHub HTTPS 地址克隆仓库。
- 从固定的 `origin` 执行 fetch。
- 创建并切换到新分支，或切换到已有本地分支。
- 提交全部非敏感修改，并展示新提交的短 SHA。
- 经用户确认后推送当前功能分支到 `origin`。
- 经用户确认后创建 GitHub Pull Request，并展示可打开的 PR 链接。
- 对加载、空列表、错误、重试和操作进行中的状态提供反馈。

该页面不提供 token 输入。commit、push 和 Pull Request 都必须先经过独立确认对话框。

## 10. Repository 接口

Repository 表示 `/projects` 下真实存在的本地 Git 仓库。它与数据库中登记的 Project 是两个独立概念：克隆或发现仓库不会自动登记 Project，登记 Project 也不会创建或克隆仓库。

### 10.1 获取本地仓库列表

```http
GET /api/repositories
```

后端只扫描 `/projects` 的直接子目录，并识别其中包含 `.git` 的目录。不会递归扫描，也不会返回真实 macOS 路径。

响应：

```json
[
  {
    "name": "demo",
    "path": "/projects/demo",
    "remote_url": "https://github.com/example/demo",
    "current_branch": "main",
    "branches": ["feature/login", "main"],
    "dirty": false
  }
]
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `name` | `/projects` 下的目录名 |
| `path` | Agent 工作区虚拟路径 |
| `remote_url` | 已移除用户名、密码、查询参数和片段的 origin 地址；未配置时为空字符串 |
| `current_branch` | 当前本地分支；detached HEAD 时为 `DETACHED` |
| `branches` | 本地分支列表，不包含远程分支 |
| `dirty` | `git status --porcelain` 是否存在输出 |

### 10.2 克隆 GitHub 仓库

```http
POST /api/repositories/clone
Content-Type: application/json
```

请求：

```json
{
  "url": "https://github.com/example/demo.git"
}
```

成功时返回 `201 Created` 和完整 Repository 响应。目标目录只能由 URL 中的仓库名生成，例如上面的地址固定克隆到 `/projects/demo`，前端不能指定其他目录。

只接受以下形式：

```text
https://github.com/{owner}/{repository}
https://github.com/{owner}/{repository}.git
```

以下输入会返回 `422 Unprocessable Content`：

- HTTP、SSH、GitLab 或其他主机地址。
- URL 中包含用户名、密码、自定义端口、查询参数或片段。
- URL 路径层级不正确，或 owner、仓库名不合法。

目标目录已经存在时返回 `409 Conflict`。后端测试不会调用真实 GitHub 网络。

### 10.3 获取远程更新

```http
POST /api/repositories/{name}/fetch
```

后端固定执行：

```text
git fetch --prune origin
```

成功时返回 `200 OK` 和更新后的 Repository 响应。前端不能传入远程名称或额外 Git 参数。仓库不存在返回 `404 Not Found`；origin 不存在、认证失败、网络错误或其他 Git 冲突返回 `409 Conflict`。

### 10.4 创建本地分支

```http
POST /api/repositories/{name}/branches
Content-Type: application/json
```

请求：

```json
{
  "name": "feature/login"
}
```

后端先使用 `git check-ref-format --branch` 验证名称，再通过 `git switch -c` 创建并切换分支。成功时返回 `200 OK` 和更新后的 Repository 响应。

非法分支名返回 `422 Unprocessable Content`，分支已经存在或工作区状态阻止切换时返回 `409 Conflict`。

### 10.5 切换本地分支

```http
POST /api/repositories/{name}/checkout
Content-Type: application/json
```

请求：

```json
{
  "name": "main"
}
```

该接口只允许切换 Repository 响应中已有的本地分支，并使用 `git switch` 执行。成功时返回 `200 OK` 和更新后的 Repository 响应。

仓库或分支不存在返回 `404 Not Found`，非法分支名返回 `422 Unprocessable Content`，未提交修改或冲突阻止切换时返回 `409 Conflict`。

### 10.6 提交仓库修改

```http
POST /api/repositories/{name}/commit
Content-Type: application/json
```

请求：

```json
{
  "message": "feat: implement repository workflow"
}
```

后端先检查工作区和敏感文件，再固定执行 `git add --all` 与 `git commit -m {message}`。成功时返回：

```json
{
  "repository": {
    "name": "demo",
    "path": "/projects/demo",
    "remote_url": "https://github.com/example/demo",
    "current_branch": "feature/course",
    "branches": ["feature/course", "main"],
    "dirty": false
  },
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "subject": "feat: implement repository workflow"
}
```

提交信息必须是 1—200 个字符的单行文本。工作区干净、Git 用户信息缺失或提交钩子失败返回 `409 Conflict`。待提交路径包含 `.env`、`.env.*`、`.secrets`、私钥文件或证书密钥时返回 `422 Unprocessable Content`；`.env.example` 允许提交。

### 10.7 推送当前分支

```http
POST /api/repositories/{name}/push
```

后端不接受远程名称或分支参数，而是固定执行：

```text
git push --set-upstream origin {current_branch}
```

成功响应包含更新后的 `repository` 和实际推送的 `branch`。detached HEAD、缺少 origin、认证失败、远程冲突或直接推送 `main/master` 均返回 `409 Conflict`。接口不提供 force push。

### 10.8 创建 GitHub Pull Request

```http
POST /api/repositories/{name}/pull-requests
Content-Type: application/json
```

请求：

```json
{
  "title": "feat: implement repository workflow",
  "body": "## Summary\n\nComplete commit, push and PR flow.",
  "base": "main"
}
```

创建前必须满足：

- origin 是 GitHub 仓库地址。
- 当前分支不是 detached HEAD，且与 base 不同。
- 工作区没有未提交修改。
- 当前分支的 upstream 是对应的 `origin/{current_branch}`。
- 后端主机已经安装 GitHub CLI，并通过 `gh auth login` 登录 `github.com`。

后端只允许执行 `gh auth status --hostname github.com` 和固定参数顺序的 `gh pr create`。成功时返回 `201 Created`：

```json
{
  "number": 42,
  "url": "https://github.com/example/demo/pull/42",
  "title": "feat: implement repository workflow",
  "base": "main",
  "head": "feature/course"
}
```

GitHub CLI 未安装或未登录返回 `503 Service Unavailable`；已有 PR、分支状态不满足或 GitHub 拒绝创建返回 `409 Conflict`；非法标题、正文、base 或 origin 返回 `422 Unprocessable Content`。

### 10.9 安全约束

- 所有 Git 命令都通过 `CommandRunner` 的参数数组执行，不拼接 shell 字符串。
- Agent 传入的 `python` 和 `python3` 都会规范为 `python`，并固定执行 `/runtimes/python/bin/python`。
- Agent Python Runtime 缺失或不可执行时直接返回策略错误，不会回退到 macOS 系统 Python。
- `workspace_execute` 遇到 `CommandPolicyError` 时不会执行命令，而是向 Agent 返回 `status=rejected`、安全错误说明和替代操作提示；Agent 可以改用 `workspace_read` 等合规工具继续当前 Run。非策略类的意外异常仍会使 Run 失败。
- 所有仓库操作都限制在 Agent 工作区的 `/projects` 下。
- API 只返回 `/projects/...` 虚拟路径，不暴露真实 macOS Workspace 路径。
- API 返回远程地址前会移除可能存在的内嵌凭据，错误响应也不会回显 Git 命令输出中的凭据。
- fetch 的远程固定为 `origin`，不能由前端覆盖。
- push 的远程固定为 `origin`，分支固定为当前分支，并禁止 `main/master` 和任何 force 参数。
- commit、push 和 Pull Request 都需要前端用户二次确认。
- GitHub CLI 只在 Repository API 的专用执行器中启用；Agent 默认命令执行器仍拒绝所有 `gh` 命令。
- 本阶段不执行 force checkout、reset、clean、删除分支或强制推送。
- 前端不接收或保存 GitHub token；GitHub CLI 凭据由后端主机的系统凭据存储管理。

Repository 接口统一状态码：

| 状态码 | 含义 |
| --- | --- |
| `200 OK` | 列表、fetch、分支、commit 或 push 成功 |
| `201 Created` | clone 或 Pull Request 创建成功 |
| `404 Not Found` | 仓库或目标本地分支不存在 |
| `409 Conflict` | 目标目录已存在，或当前 Git 状态阻止操作 |
| `422 Unprocessable Content` | URL、仓库名、请求体或分支名不合法 |
| `503 Service Unavailable` | GitHub CLI 未安装或未登录 |

## 11. 当前缺失能力

| 前端目标 | 当前状态 |
| --- | --- |
| 项目列表 | 已支持 |
| 手动登记项目 | 已支持 |
| 自动扫描本地 Git 仓库 | 已支持，只扫描 `/projects` 直接子目录 |
| GitHub HTTPS 克隆仓库 | 已支持，前端输入明确 URL |
| Repository fetch | 已支持，固定 `origin` |
| 本地分支创建与切换 | 已支持 |
| 会话列表 | 已支持 |
| 新建会话 | 已支持 |
| 首条消息自动命名 | 已支持 |
| 会话手动重命名 | 未支持 |
| 会话删除/归档 | 未支持 |
| 多轮消息 | 已支持 |
| Run 实时 Token | 已支持 |
| Run 生命周期事件 | 已支持 |
| 工具调用步骤 | 已支持，按 `tool_call_id` 归并 |
| 子 Agent 步骤 | 已支持，按 `source` 区分来源 |
| Planning 确认后实施 | 已支持，确认后切换 Coding 并预填请求 |
| 取消 Run | 未支持 |
| 专用重试接口 | 未支持 |
| Skills 列表与详情 | 已支持，只读 |
| Git commit | 已支持，提交全部非敏感修改 |
| Git push | 已支持，固定 `origin`，禁止直接推送 `main/master` |
| GitHub Pull Request | 已支持，使用受限 GitHub CLI |
| 文件或图片上传 | 未支持 |
| 历史分页 | 未支持 |

## 12. 推荐实施顺序

1. 重写 `frontend/src/api.ts`，建立 Project、Thread、Message、Run 和 RunEvent 类型。
2. 创建 Codex 风格页面骨架。
3. 实现项目与会话侧边栏。
4. 实现登记项目和新建会话。
5. 实现消息历史和底部输入框。
6. 接入 Run SSE 和临时 Assistant 消息。
7. 实现步骤列表、运行状态和完成项划线。
8. 接入 Skills 列表、详情和 Markdown 预览。
9. 接入本地仓库发现、GitHub HTTPS clone、fetch 和本地分支操作。
10. 接入用户确认后的 commit、固定 origin push 和受限 GitHub CLI Pull Request。
11. 实现先方案后实施。
12. 实现 Reviewer 和端到端验收。

## 13. 当前结论

当前后端已经能够支持新版前端的核心链路：

```text
选择项目
→ 选择或创建会话
→ 加载历史消息
→ 发送用户消息
→ 启动 Agent Run
→ SSE 展示生命周期、工具、子 Agent 和流式回答
→ 终态后刷新 Run 与消息快照
```

仓库链路现已覆盖发现、clone、fetch、分支、commit、push 和 Pull Request，聊天链路也已支持 Planning 方案确认后切换 Coding。下一阶段进入 Reviewer 与端到端验收，并继续保持平台源码、Agent 工作区、GitHub 凭据和用户确认之间的边界。
