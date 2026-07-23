# Tang Agent 前后端接口与前端功能规划

更新日期：2026-07-22

## 1. 文档目标

本文档说明 Tang Agent 当前已经向前端提供的 HTTP/SSE 接口、前端需要实现的功能、尚未具备的后端能力，以及推荐的前端实施顺序。

新版前端的核心产品结构为：

```text
左侧导航栏
├── 项目列表
├── 项目下的会话
├── 新建对话
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
  "content": "分析这个项目并给出改进建议"
}
```

响应状态为 `202 Accepted`：

```json
{
  "run": {
    "run_id": "uuid",
    "thread_id": "uuid",
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
    "content": "分析这个项目并给出改进建议",
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
completed
failed
```

事件数据示例：

```json
{
  "run_id": "uuid",
  "source": "main",
  "created_at": "2026-07-22T00:00:00+00:00",
  "text": "正在分析项目结构……"
}
```

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

当前 Conversation Run 尚未产生 `tool_started/tool_finished` 和子 Agent 事件。

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

1. 立即加入接口返回的 user Message。
2. 保存返回的 `run_id`。
3. 禁用输入框，防止同一会话重复运行。
4. 建立 Run EventSource。
5. 展示流式回答和执行步骤。

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

当前后端尚无 Skills API。第一版前端先实现：

- 左侧 Skills 导航入口。
- Skills 页面布局。
- 后端尚未接入时的空状态。

后续至少需要增加：

```http
GET /api/skills
GET /api/skills/{skill_name}
```

第一阶段 Skills 页面建议展示名称、简介、来源、可用状态、`SKILL.md` 内容预览，并支持搜索和筛选。

## 10. 当前缺失能力

| 前端目标 | 当前状态 |
| --- | --- |
| 项目列表 | 已支持 |
| 手动登记项目 | 已支持 |
| 自动扫描工作区项目 | 未支持 |
| GitHub 克隆项目 | 未支持 |
| 会话列表 | 已支持 |
| 新建会话 | 已支持 |
| 首条消息自动命名 | 已支持 |
| 会话手动重命名 | 未支持 |
| 会话删除/归档 | 未支持 |
| 多轮消息 | 已支持 |
| Run 实时 Token | 已支持 |
| Run 生命周期事件 | 已支持 |
| 工具调用步骤 | Conversation Run 尚未支持 |
| 子 Agent 步骤 | 尚未支持 |
| 取消 Run | 未支持 |
| 专用重试接口 | 未支持 |
| Skills 列表与详情 | 未支持 |
| GitHub 仓库、分支和 PR | 未支持 |
| 文件或图片上传 | 未支持 |
| 历史分页 | 未支持 |

## 11. 推荐实施顺序

1. 重写 `frontend/src/api.ts`，建立 Project、Thread、Message、Run 和 RunEvent 类型。
2. 创建 Codex 风格页面骨架。
3. 实现项目与会话侧边栏。
4. 实现登记项目和新建会话。
5. 实现消息历史和底部输入框。
6. 接入 Run SSE 和临时 Assistant 消息。
7. 实现步骤列表、运行状态和完成项划线。
8. 增加 Skills 页面入口与空状态。
9. 补充 Skills、工具事件和 GitHub 所需后端接口。

## 12. 当前结论

当前后端已经能够支持新版前端的核心链路：

```text
选择项目
→ 选择或创建会话
→ 加载历史消息
→ 发送用户消息
→ 启动 Agent Run
→ SSE 展示生命周期和流式回答
→ 终态后刷新 Run 与消息快照
```

下一阶段可以开始实现前端。Skills 和 GitHub 先建立导航入口及页面骨架，再逐步补齐对应后端能力。
