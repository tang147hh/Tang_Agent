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

### 3.3 获取项目文件改动统计

```http
GET /api/projects/{project_id}/file-changes
```

返回当前 Git 工作区相对 `HEAD` 的累计改动，包括已暂存、未暂存和未跟踪文件：

```json
{
  "project_path": "/projects/demo",
  "changed_files": 3,
  "additions": 27,
  "deletions": 8,
  "binary_files": 0,
  "hidden_files": 1,
  "files": [
    {
      "path": "/projects/demo/backend/app.py",
      "additions": 14,
      "deletions": 3,
      "binary": false,
      "status": "modified"
    }
  ]
}
```

`status` 为 `modified`、`added`、`deleted` 或 `untracked`。二进制或超过安全读取上限的
未跟踪文件无法可靠计算行数，此时 `binary=true`，`additions/deletions` 为 `null`。
敏感路径不返回，只累加 `hidden_files`。响应只含 `/projects/...` 虚拟路径和行数，不含
diff 内容或真实主机路径。

前端在执行侧栏显示总文件数、总增删行和逐文件统计，并在 Run 终态、项目切换及返回
聊天视图时刷新。这里展示的是当前工作区累计状态，不声明所有改动都由最近一次 Run
产生。项目不存在或不是 Git 仓库返回 `404`；改动过多或 Git 无法读取返回 `409`。

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
  "task_kind": "coding",
  "network_access": false
}
```

`task_kind` 可选。新版前端会明确传入用户在输入框下方选择的模式；旧客户端不传时，后端仍根据 `content` 自动分类。
`network_access` 也可选，缺省为 `false`。它在创建 Run 时与固定 provider 一起保存，
之后切换聊天框设置不会修改已经创建或完成的 Run。

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
    "network_access": false,
    "network_provider": "disabled",
    "network_request_count": 0,
    "network_result_count": 0,
    "network_bytes_received": 0,
    "network_cache_hit_count": 0,
    "network_limit_reached": false,
    "network_limit_reason": null,
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
同时返回联网授权快照和网络指标；API 不提供修改历史 Run 联网授权的操作。

### 6.4 Thread 对话历史与 Run 状态隔离

`thread_id` 和 `run_id` 承担不同职责：

| 标识 | 生命周期 | 用途 |
| --- | --- | --- |
| `thread_id` | 整个业务会话 | 关联项目、消息历史和历次 Run |
| `run_id` | 单次 Agent 执行 | 隔离本轮 LangGraph 检查点和运行状态 |

SQLite `tasks.sqlite` 中按 `sequence` 保存的 Message 是对话历史的权威来源。每次启动 Agent 时，后端会从这些 Message 重建完整的 `user`、`assistant` 和 `system` 消息列表；只有本轮用户消息会额外加入当前项目虚拟路径与项目边界说明。

LangGraph 的 `checkpoints.sqlite` 使用 `run_id` 作为检查点 `thread_id`。因此，同一业务 Thread 内从 `qa` 或 `planning` 切换到 `coding` 时，新 Run 可以继续理解历史对话，但不会复用旧 Run 的只读 Agent 状态。该实现属于后端内部约束，前端仍只需保存接口返回的业务 `thread_id` 和 `run_id`。

### 6.5 获取 Run 性能与预算

```http
GET /api/runs/{run_id}/performance
```

响应与业务 `run_id` 一对一持久化。刚创建、尚未产生输出的 Run 中，计数为 `0`，
`first_output_ms`、`duration_ms` 和 `termination_reason` 可以为 `null`：

```json
{
  "run_id": "uuid",
  "task_kind": "coding",
  "max_model_calls": 16,
  "max_tool_calls": 40,
  "max_first_output_seconds": 30,
  "max_seconds": 480,
  "max_identical_tool_calls": 2,
  "model_calls": 3,
  "tool_calls": 5,
  "repeated_tool_calls": 1,
  "tool_errors": 2,
  "safety_rejections": 1,
  "first_output_ms": 412.5,
  "duration_ms": 2850.2,
  "termination_reason": null,
  "created_at": "2026-07-23T00:00:00+00:00",
  "updated_at": "2026-07-23T00:00:03+00:00"
}
```

不存在的 Run 返回 `404`。历史数据库中存在 Run、但尚无性能记录时返回 `null`。
前端在选择会话、创建 Run 和收到终态事件后读取此接口。

默认预算：

| 模式 | 模型调用 | 工具调用 | 首个输出 | 总时长 |
| --- | ---: | ---: | ---: | ---: |
| `qa` | 3 | 4 | 12 秒 | 45 秒 |
| `planning` | 5 | 10 | 20 秒 | 120 秒 |
| `analysis` | 8 | 20 | 25 秒 | 180 秒 |
| `coding` | 16 | 40 | 30 秒 | 480 秒 |

相同工具与规范化参数默认最多出现两次。第二次由中间件返回可恢复拒绝；模型仍持续
发起相同调用时，后端以 `repeated_tool_call` 终止 Run。

### 6.5.1 工具与联网能力

发送前查询选定模式与下一次 Run 的联网状态：

```http
GET /api/tool-capabilities?task_kind=analysis&network_access=true
```

查询已创建 Run 的不可变快照：

```http
GET /api/runs/{run_id}/tool-capabilities
```

响应：

```json
{
  "task_kind": "analysis",
  "run_id": null,
  "network_access": true,
  "network_provider": "zhipu",
  "web_search": {
    "available": true,
    "provider": "zhipu",
    "configured": true,
    "provider_available": true,
    "allowed_in_mode": true,
    "enabled_for_run": true,
    "unavailable_reason": null
  },
  "network_budget": {
    "max_searches": 4,
    "max_results_per_search": 5,
    "request_timeout_seconds": 15,
    "max_result_chars_per_search": 8000,
    "max_total_result_chars": 24000,
    "max_bytes_received": 2097152
  },
  "tools": []
}
```

实际 `tools` 数组返回固定注册工具的完整元数据：`name`、`category`、`risk_level`、
`allowed_task_kinds`、`requires_network_access`、`model_callable`、`description`、
`availability` 和 `unavailable_reason`。`github_review_publish` 会出现为
`external_write` 且 `model_callable=false`，但不会进入任何 Agent 工具列表。

能力响应绝不包含 API Key、Token、SDK 路径、完整环境变量或 provider 原始异常。
Provider 未配置、缺 SDK/Key 或后端不可用时，前端在紧凑联网菜单中显示受控原因，
不提供 Key 输入框。

### 6.5.2 工作区文件定位与代码搜索

`workspace_glob` 和 `workspace_search` 是固定注册的 `local_read`、low-risk 能力，无需
`network_access`。qa/planning/analysis/coding 主 Agent 均可使用，analysis 子 Agent
也可使用；Reviewer 的 `tools=[]`，GitHub Review prepare/publish 不经过 Agent 工具。

`workspace_glob` 输入：

```json
{
  "path": "/projects/demo",
  "pattern": "**/*.py",
  "max_results": 100,
  "include_directories": false
}
```

成功输出：

```json
{
  "ok": true,
  "path": "/projects/demo",
  "pattern": "**/*.py",
  "matches": [
    {"path": "/projects/demo/app/main.py", "kind": "file", "size_bytes": 1250}
  ],
  "match_count": 1,
  "truncated": false,
  "scanned_entry_count": 12,
  "duration_ms": 4.0
}
```

`workspace_search` 输入：

```json
{
  "path": "/projects/demo",
  "query": "build_agent(",
  "file_pattern": "**/*.py",
  "max_results": 100,
  "case_sensitive": true
}
```

成功输出的 `matches[]` 包含虚拟 `path`、1-based `line_number`、`column_start`、
`column_end` 和最多 500 字符的 `snippet`；顶层还返回 `match_count`、`files_searched`、
`skipped_file_count`、`scanned_bytes`、`truncated` 和 `duration_ms`。

两个工具只接受 `/projects/...` 等虚拟根，模式必须相对于 `path`。`max_results` 为 1-500；
模式最长 512 字符，query 最长 500 字符。绝对主机/Windows 路径、`..`、NUL、控制字符、
`.secrets`、符号链接、依赖/缓存/构建目录和敏感文件均被拒绝或跳过。内容搜索是字面量
匹配，不接受正则；二进制、非 UTF-8 和超过 1,000,000 bytes 的文件不会读取。单次最多
扫描 50,000 个目录项和 20,000,000 bytes 文本。

这些调用使用现有 `ToolGovernanceMiddleware` 和 Run 事件 tracker，因此会计入
`tool_calls`，相同工具+规范参数会进入重复检测，超过预算产生 `terminated` 事件。工具
参数中不存在 task kind、Reviewer、network、write 或 command 权限开关。

### 6.6 Review Finding

Reviewer 的结果属于产生它的具体 Run，而不是只属于 Thread。同一个 Thread 的不同
审查 Run 可以保留相同问题，用于比较历次审查结果。

```http
GET /api/runs/{run_id}/review-findings
GET /api/runs/{run_id}/review-findings?severity=high&status=open
```

`severity` 和 `status` 均为可选筛选参数。默认排序依次为严重程度
`critical > high > medium > low`、`file_path`、`start_line`、`created_at` 和 `id`。

完整响应示例：

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "run_id": "c3b1a5d8-8e18-4b06-b6e9-d77b9ba9693f",
    "severity": "high",
    "category": "correctness",
    "file_path": "/projects/demo/backend/app.py",
    "start_line": 42,
    "end_line": 47,
    "line_side": "new",
    "title": "失败分支继续使用空值",
    "description": "配置缺失时函数继续执行，并在下一行解引用空值，导致请求返回 500。",
    "suggestion": "在配置校验失败后立即返回受控错误。",
    "status": "open",
    "fingerprint": "9a0d8f56d2b65e5a6e5a5da3ecda8f80f9d08db7580b31bbbeb28f7fa72a1b67",
    "review_diff_hash": "5721d9fca8a6...",
    "review_scope": "all",
    "base_revision": "a61c27f...",
    "head_revision": null,
    "created_at": "2026-07-23T00:00:00+00:00",
    "updated_at": "2026-07-23T00:00:00+00:00"
  }
]
```

| 字段 | 含义 |
| --- | --- |
| `id` | 后端生成的 Finding UUID |
| `run_id` | 产生 Finding 的业务 Run |
| `severity` | 稳定严重级别枚举 |
| `category` | 稳定问题类别枚举 |
| `file_path` | 当前项目内的虚拟路径；全局问题为 `null` |
| `start_line` / `end_line` | 闭区间正整数；全局问题均为 `null` |
| `line_side` | `old` 或 `new`；文件级/全局 Finding 为 `null` |
| `title` | 简短问题标题 |
| `description` | 风险、触发条件和影响 |
| `suggestion` | 可选修改建议，不表示已经修复 |
| `status` | 用户维护的 Finding 生命周期状态 |
| `fingerprint` | 后端生成的稳定 SHA-256 去重指纹 |
| `review_diff_hash` | 产生 Finding 的规范化、脱敏及截断后 Diff 哈希 |
| `review_scope` | `staged`、`unstaged` 或 `all`；第 34 课历史数据为 `null` |
| `base_revision` / `head_revision` | 本次审查的 Git revision 追溯字段；工作树侧没有 SHA 时为 `null` |
| `created_at` / `updated_at` | 创建与最后状态更新时间 |

枚举值：

```text
severity: critical | high | medium | low
category: correctness | security | performance | maintainability | testing | documentation
status: open | resolved | dismissed
```

全局问题的 `file_path`、行号和 `line_side` 必须同时为 `null`；文件级问题只保留
`file_path`；行级问题必须同时提供起止行，`end_line >= start_line`，并使用明确的
`old/new` 侧。后端只返回 `/projects/...`
虚拟路径，并拒绝 macOS 主机绝对路径、Windows 盘符、`..` 路径逃逸和不属于当前
Run 项目的路径。带 `review_diff_hash` 的 Finding 还必须指向本次 Diff 中模型实际看到
的文件和 hunk；被截断的不可见区域、Diff 外文件和二进制行号都会被拒绝。

只允许更新状态：

```http
PATCH /api/runs/{run_id}/review-findings/{finding_id}
Content-Type: application/json

{"status": "resolved"}
```

PATCH 只修改 `status` 和 `updated_at`。不存在的 Run，或 Finding 不属于 URL 中的
Run，返回 `404`；非法筛选枚举、非法状态和多余请求字段返回 `422`，且不会修改数据。
前端没有创建 Finding 的公开 API；Finding 只能来自后端受控的 Reviewer 解析链路。

### 6.7 发起安全 Git Diff 审查

```http
POST /api/runs/{run_id}/reviews
Content-Type: application/json

{"scope":"all"}
```

请求只接受 `scope`，并禁止额外字段。没有 `cwd`、仓库路径、Shell 命令或 base revision
参数。仓库从 `run_id -> thread -> registered project` 确定，且必须是
`/projects/...` 下的独立 Git 根目录。

scope 定义：

| scope | 内容 |
| --- | --- |
| `staged` | `HEAD`（无 HEAD 时为空树）到 index |
| `unstaged` | index 到工作树，不含 untracked |
| `all` | `HEAD`（无 HEAD 时为空树）到最终工作树，并加入未忽略的 untracked |

响应示例（API 不返回 `patch`，只返回由后端解析的安全结构）：

```json
{
  "run_id": "uuid",
  "status": "completed",
  "scope": "all",
  "diff": {
    "scope": "all",
    "repository_virtual_path": "/projects/demo",
    "base_revision": "a61c27f...",
    "head_revision": null,
    "file_count": 1,
    "total_additions": 2,
    "total_deletions": 1,
    "truncated": false,
    "truncation_reasons": [],
    "content_hash": "5721d9fca8a6...",
    "created_at": "2026-07-23T00:00:00+00:00",
    "redacted": false,
    "files": [
      {
        "old_path": "/projects/demo/app.py",
        "new_path": "/projects/demo/app.py",
        "change_type": "modified",
        "binary": false,
        "submodule": false,
        "additions": 2,
        "deletions": 1,
        "truncated": false,
        "truncation_reason": null,
        "changed_new_lines": [42, 43],
        "changed_old_lines": [42],
        "redacted": false,
        "hunks": [
          {
            "header": "@@ -42,1 +42,2 @@",
            "old_start": 42,
            "old_count": 1,
            "new_start": 42,
            "new_count": 2,
            "lines": [
              {
                "type": "deletion",
                "old_line_number": 42,
                "new_line_number": null,
                "content": "return old_value"
              },
              {
                "type": "addition",
                "old_line_number": null,
                "new_line_number": 42,
                "content": "return new_value"
              }
            ]
          }
        ]
      }
    ]
  },
  "findings": [],
  "finding_count": 0,
  "created_count": 0,
  "duplicate_count": 0,
  "summary": "审查范围：all。未发现问题。"
}
```

`change_type` 为 `modified|added|deleted|renamed|copied|untracked`。删除文件只有
`old_path`，新增/untracked 只有 `new_path`，rename/copy 同时保留两侧。二进制文件和
子模块只返回元数据，Reviewer 不会收到二进制字节，也不会递归读取子模块。

容量安全默认值：50 文件、单文件 40,000 patch 字符/800 变更行、总计 200,000
字符/3,000 变更行、Git 命令 30 秒。达到限制时使用稳定原因
`max_files|file_patch_chars|file_changed_lines|total_patch_chars|total_changed_lines|git_output`，
总结必须说明结果可能不完整。环境变量名见 `.env.example`。

进入 Reviewer 的 patch 已将私钥、GitHub/API/Bearer/Access Token、Secret、Password
和常见 `.env` 凭据替换为 `[REDACTED]`，换行及行号不变。Reviewer 没有文件、命令、
网络或发布工具；Diff 内提示词注入只会被当成代码。无变更时不调用模型。Diff 收集计入
共享 `tool_calls`，模型调用计入 `model_calls`，并受原 Run 的总时长和终态规则约束。

受控错误的 `detail` 为 `{code, message}`。常见 code 包括 `run_not_found`、
`repository_not_found`、`repository_outside_workspace`、`git_command_failed`、
`git_command_timeout`、`run_time_limit`、`reviewer_unavailable`、`reviewer_output_invalid` 和
`budget_exceeded`；message 不包含 Git stderr 或主机真实路径。

### 6.8 Review 快照与新审查 Run

第 36 课增加两个接口：

```http
GET /api/runs/{run_id}/review
POST /api/threads/{thread_id}/review-runs
Content-Type: application/json

{"scope":"all"}
```

`review_diff_snapshots` 以 `run_id` 为主键，只保存已经完成路径规范化、容量限制、UTF-8
安全截断、敏感内容脱敏和二进制排除的 `ReviewDiff`。同一 Run 不允许覆盖快照；再次
审查必须通过 `review-runs` 创建新的 `analysis` Run。快照在 Reviewer 模型调用前落库，
因此预算或模型阶段失败时仍能展示 Reviewer 实际收到或原计划收到的受控 Diff。

GET 详情返回 `status: collected|completed|failed`、summary、结构化 Diff 和当前 Finding。
每个 hunk 包含范围，每行类型为 `context|addition|deletion|no_newline`，并分别返回
`old_line_number` 与 `new_line_number`。删除 Finding 按 old 侧定位，新增/普通修改按
new 侧定位；文件级 Finding 定位文件标题，二进制只允许文件级定位。接口永远不返回
受控 patch 本身，更不返回原始未脱敏 patch 或主机路径。

前端 Repository 详情中的“代码审查”只对虚拟路径匹配的已登记 Project 开放，不接受
用户输入 cwd。工作台桌面端使用文件/Diff/问题三栏，平板和移动端使用标签页；只渲染
当前文件。Finding 状态 PATCH 采用乐观更新，失败后回滚并显示中文错误。`truncated`、
`redacted`、binary 和预算失败均有独立、非仅颜色的提示。

### 6.9 GitHub Review 安全发布

ReviewDiff 新增 `source: working_tree|pull_request`。`working_tree` 继续使用
`scope=staged|unstaged|all`，且 `repository/pr_number/head_revision` 不形成可发布身份；
`pull_request` 固定 `scope=all`，并保存已验证的 `repository`、`pr_number`、`base_revision`
和 `head_revision`。本地未提交代码禁止发布，不会自动寻找或映射到 PR。

项目级 capability 是只读查询：

```http
GET /api/projects/{project_id}/github-review/capability
```

返回 `gh_installed`、`authenticated`、`remote_found`、`publish_enabled`、`can_publish`、
受控 `reason`、repository、current_user 和最多 20 个 open PR。PR 字段为 pr_number、title、
canonical GitHub URL、state/draft、base/head branch、base/head SHA、author 和 repository。
owner/repo 只能从 registered project 的 origin 推导，前端不能覆盖。多个 origin URL
必须解析为同一仓库；非 github.com、缺少 origin 或非法 owner/repo 都返回受控状态。

发起 PR Review：

```http
POST /api/threads/{thread_id}/review-runs
Content-Type: application/json

{"scope":"all","source":"pull_request","pr_number":42}
```

后端再次验证 PR 属于当前仓库，并通过只读 `gh api` 获取 PR 和文件。远端 patch 仍执行
第 35 课路径、容量、脱敏、二进制/子模块和提示词注入隔离；缺失 patch 标记为
`github_patch_unavailable`。不 checkout、fetch、reset、merge 或修改工作树。

发布预览：

```http
POST /api/runs/{run_id}/github-review/prepare
Content-Type: application/json

{
  "pr_number": 42,
  "selected_finding_ids": ["finding-id"],
  "event": "REQUEST_CHANGES",
  "summary": "请先处理已验证的问题。"
}
```

仅 `open` Finding 默认选中。`new -> RIGHT`、`old -> LEFT`；多行范围使用 `line/side` 和
`start_line/start_side`。文件级、全局和二进制 Finding 明示进入总结；Diff 外、不可见
截断区或错误 side 会被拒绝，不猜测旧 position。prepare 返回 publication_id、仓库/PR、
base/head SHA、event、行内/总结/跳过项、warnings、summary body、payload hash 和 expires_at，
且不访问写接口。

用户确认后只提交 publication_id：

```http
POST /api/runs/{run_id}/github-review/publish
Content-Type: application/json

{"publication_id":"uuid"}
```

后端原子地把 `prepared|failed` 认领为 `publishing`，然后重新验证过期时间、Run 归属、
payload/Finding 状态、仓库、PR open/draft 和 head SHA。一次固定 GitHub Review POST 通过
JSON stdin 发送 commit_id、受控 body、event 和 comments。相同 payload 的
`publishing|published|unknown` 记录阻止第二次写入。普通 API 失败为 `failed`；写请求
超时或成功响应无法确认时为 `unknown`，禁止自动重试。审计查询：

```http
GET /api/runs/{run_id}/github-review/publications
```

响应不含 GitHub payload、评论原文、Diff、token、凭据路径或主机路径。稳定错误码：

```text
gh_not_installed github_not_authenticated github_remote_not_found
unsupported_github_host pull_request_not_found pull_request_closed
pull_request_changed pull_request_draft permission_denied
review_not_publishable finding_not_publishable publication_expired
publication_changed publication_already_published publication_in_progress
publication_result_unknown github_timeout github_api_error publishing_disabled
```

前端将错误映射为中文。确认弹窗展示仓库、PR、Review 类型、行内/总结/跳过数量、head SHA
和真实外部写入警告；按钮文案固定为“发布到 GitHub”。打开弹窗、取消、Enter、刷新均
不会发布。发布配置见 `.env.example`，测试必须注入 fake runner，禁止访问真实 GitHub。

离线安全测试命令：

```bash
uv run pytest tests/test_github_review.py -q
cd frontend && npm test
```

后端测试的 `FakeGitHubRunner` 提供认证、PR/files、发布成功/失败/超时响应，并记录 argv 与
JSON stdin；测试中不解析用户 gh 配置、不连接网络，也不可能向真实 PR 发布评论。

第 38 课应用内 Browser 仍返回 `No browser is available`，随后复用系统 Chrome 150 与
Playwright 请求拦截完成 1440x900、1280x720、768x1024、390x844 四种视口。E2E 使用
fixture API，不连接真实 GitHub；覆盖工作树禁用发布、PR Finding 选择、预览取消、
明确确认、成功/失败/unknown、Enter 不发布、重复点击锁定、old/new/文件级定位、滚动和
控制台/未处理请求检查。截图与结果见 `docs/lesson-38-acceptance.md`。

平板验收发现全局侧栏压缩 Review 工具栏，已在 761-1024px 的 Review 视图隐藏侧栏；
移动弹窗的长 PR 标题现在使用省略显示并保留外链图标。真实 GitHub COMMENT 仍因 gh
Token 失效且无专用测试 PR 而 blocked，不能把 mock 结果描述为真实发布。

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
terminated
review_findings_saved
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

`created` 和 `running` 生命周期事件会携带 `task_kind` 与 `budget`；文本和工具事件
不重复携带这些字段。
第 39 课起生命周期事件还携带 `network_access`、`network_provider` 和
`network_budget`，用于展示本 Run 的实际快照。

`review_findings_saved` 由 Reviewer 校验并保存完一批结果后产生，携带
`created_count`、`duplicate_count`、`rejected_count` 和 `summary`。它不是 Run 终态；
Reviewer 解析失败会产生来源为 `reviewer` 的 `failed` 事件，并把 Run 写为 `failed`。

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

预算终止使用独立的 `terminated` 事件，同时把 Run 写为 `failed`，例如：

```json
{
  "run_id": "uuid",
  "source": "system",
  "created_at": "2026-07-23T00:00:00+00:00",
  "status": "failed",
  "termination_reason": "total_time_limit",
  "error": "Run 已达到总运行时间预算：上限 480 秒。"
}
```

`termination_reason` 可能为 `model_call_limit`、`tool_call_limit`、
`first_output_timeout`、`total_time_limit` 或 `repeated_tool_call`。普通未知异常使用
`failed` 事件，性能记录中的原因是 `agent_error`。

工具执行失败时，`tool_finished` 可以携带 `status: "error"` 和
`recoverable: true`。这是工具步骤失败，不代表整个 Run 已失败；前端只能使用
`created/running/completed/failed/terminated` 生命周期事件更新 Run 状态。

工作区搜索开始事件不会包含原始 `query`，完成事件不会包含文件内容、`snippet` 或完整
`matches`。前端只获得定位进度所需的安全元数据：

```json
{
  "name": "workspace_search",
  "tool_call_id": "call-search-1",
  "path": "/projects/demo",
  "file_pattern": "**/*.py",
  "max_results": 100
}
```

```json
{
  "name": "workspace_search",
  "tool_call_id": "call-search-1",
  "status": "completed",
  "recoverable": false,
  "match_count": 3,
  "files_searched": 12,
  "skipped_file_count": 1,
  "scanned_bytes": 4096,
  "duration_ms": 4.0,
  "truncated": true
}
```

`workspace_glob` 的开始事件使用 `pattern`，完成事件使用 `match_count`、
`scanned_entry_count`、`duration_ms` 和 `truncated`。React 执行步骤会显示“正在定位文件”
或“正在搜索代码”，完成后显示匹配数、扫描文件数、耗时和截断状态。

`web_search` 开始事件只包含安全查询预览、固定 provider 和 `max_results`：

```json
{
  "name": "web_search",
  "tool_call_id": "call-search-1",
  "query": "FastAPI latest docs",
  "provider": "zhipu",
  "max_results": 5
}
```

完成事件不包含 snippet 或 provider 原始响应，只包含计数、耗时、缓存/截断状态和安全
来源：

```json
{
  "name": "web_search",
  "tool_call_id": "call-search-1",
  "status": "completed",
  "recoverable": false,
  "result_count": 1,
  "duration_ms": 82.4,
  "cached": false,
  "truncated": false,
  "sources": [
    {
      "citation_id": "S1",
      "title": "FastAPI Documentation",
      "url": "https://fastapi.tiangolo.com/"
    }
  ]
}
```

失败事件使用稳定 `error_code`、中文 `error` 和 `recoverable=true`。稳定代码包括
`network_access_disabled`、`network_provider_unavailable`、
`network_sensitive_input_rejected`、`network_search_limit`、`network_result_limit`、
`network_timeout`、`network_provider_error` 和 `network_invalid_result`。参数问题使用
`network_invalid_request`。这些是工具级可恢复错误，不会单独让 Run 停留在 running。

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
GET /api/runs/{run_id}/performance
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
| `terminated` | 标记预算终止，显示具体原因，关闭 SSE 并刷新指标 |

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

收到 `completed/failed/terminated` 后执行：

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
    ↓
GET /api/runs/{run_id}/performance
```

SSE 用于过程展示，GET 接口返回的数据是最终权威状态。
执行面板展示首个输出、总耗时、模型/工具预算消耗、重复调用、工具错误、安全拒绝和
预算终止原因。安全拒绝只标记对应工具步骤，不应提前关闭 SSE。

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

Repository PR 创建执行器只允许 `gh auth status --hostname github.com` 和固定参数顺序的
`gh pr create`。第 37 课 GitHub Review 使用 6.9 节的独立宿主机 runner 和固定
`gh api` endpoint/JSON stdin；两者都不暴露给 Agent。PR 创建成功时返回 `201 Created`：

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

## 11. 当前能力状态

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
| 工作区文件增删统计 | 已支持，执行侧栏显示累计 Git 工作区改动 |
| Planning 确认后实施 | 已支持，确认后切换 Coding 并预填请求 |
| 取消 Run | 未支持 |
| 专用重试接口 | 未支持 |
| Skills 列表与详情 | 已支持，只读 |
| Git commit | 已支持，提交全部非敏感修改 |
| Git push | 已支持，固定 `origin`，禁止直接推送 `main/master` |
| GitHub Pull Request | 已支持，使用受限 GitHub CLI |
| 结构化 Review Finding | 已支持，按 `run_id` 保存、筛选和更新状态 |
| 完整 Review 面板 | 已支持，快照文件树、结构化 Diff、Finding 定位/筛选/状态；四视口 Chrome 验收通过 |
| GitHub Review 发布 | 已支持，PR 快照、prepare/confirm/publish、SHA 校验、幂等和 unknown 状态 |
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
12. 接入结构化 Reviewer Finding（已完成）。
13. 安全 Git Diff、结构化 hunk 与 old/new 行号核对（已完成）。
14. 实现完整 Review 面板（已完成；系统 Chrome 四视口视觉验收通过）。
15. 实现用户确认后的 GitHub Review 发布（已完成）。
16. 在专用测试 PR 完成真实端到端验收（第 38 课本地/mock 阶段完成；真实阶段 blocked）。

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

仓库链路现已覆盖发现、clone、fetch、分支、commit、push、Pull Request 和用户确认后的 GitHub Review。Reviewer 按 Run 保存工作树或 PR Diff 快照和严格校验的 Finding；前端可浏览、定位、选择、预览并确认发布。第 38 课的完整回归、mock E2E、四视口视觉检查、性能基线和重启审计已经完成；真实 GitHub COMMENT、APPROVE、REQUEST_CHANGES 仍须在认证恢复后的专用测试 PR 上逐次授权，继续保持平台源码、Agent 工作区、GitHub 凭据和用户确认之间的边界。
