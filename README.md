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

## 验证

```bash
uv --cache-dir /tmp/tang-agent-uv-cache run pytest -q
cd frontend
npm run lint
npm run build
```

LX_AICoding 的完整审阅、取舍和修改前后对比见
[`docs/lx-aicoding-agent-runtime-comparison.md`](docs/lx-aicoding-agent-runtime-comparison.md)。
