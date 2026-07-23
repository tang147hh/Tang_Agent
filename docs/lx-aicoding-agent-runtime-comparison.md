# Agent Runtime Comparison

本文记录 2026-07-23 对只读参考项目
`/Users/tang/Documents/projects/myAgent/LX_AICoding` 的执行链路审阅结果。
参考项目未被修改。

## 审阅范围与调用链

本次沿真实执行路径阅读了以下实现，而不是只比较目录或接口名称：

- 创建与入口：`agent/core/runtime.py`、`agent/server.py`、`agent/core/graph.py`。
- Agent loop 与模型：`create_deep_agent` 装配、主/子模型创建、checkpoint 配置。
- 中间件：`model_call_limit`、`tool_sanitize.py`、`tool_error.py`、
  `run_limits.py`。
- 流式与状态：`streaming_runtime.py` 的 v3 raw event 消费、`events.py`、
  `state.py`、`sqlite_store.py`、FastAPI dashboard/SSE 路由。
- 模式与任务：`qa`、`inspect`、`sync`、`analysis`、`planning`、`coding`
  的分类、权限和 runtime 分支。
- Reviewer/子 Agent：`server.py` 的 `general-purpose` 子 Agent，
  `reviewer*.py`、`tools/reviewer_tools.py` 及 review findings 表。
- 平台绑定：Windows 本地 backend、PowerShell askpass、Gitee API/Git 工具、
  配置、课程文档和验证脚本。

实际主链路为：

```text
FastAPI 后台任务
  -> runtime 创建业务 run_id 并写 running
  -> _build_agent_for_runtime(...)
  -> server.get_agent(config)
  -> 每 Run 创建模型、prompt、tools、middleware、subagent 和 DeepAgent 图
  -> agent.stream_events(version="v3")
  -> streaming_runtime 解析 token/tool/subagent 事件并检查工具/时间预算
  -> runtime 统一写 completed 或 failed，并结束 SSE
```

## LX_AICoding 审阅结论

1. **模型调用次数**：`agent/server.py` 在每次 `get_agent()` 中安装
   `ModelCallLimitMiddleware(run_limit=5000, exit_behavior="end")`，同时图的
   `recursion_limit` 默认为 9999。中间件在下一次模型调用前读取本 Run 计数并结束图，
   机制正确，但 5000 对交互式 Coding Agent 几乎等于没有实际预算；业务 Store 也没有
   保存专门的 `model_call_limit` 终止原因。
2. **工具调用次数和总时间**：`AgentRunLimits` 按任务类型配置阈值，
   `AgentRunLimitTracker` 在 `streaming_runtime.py` 消费 v3 raw event 时统计
   `tool_calls`/`tool-started`，并用单调时钟检查总时长。默认值从 qa 的
   60 次/600 秒到 coding 的 300 次/1800 秒，仍明显偏大。时间只在下一条事件到达时
   检查；如果模型或工具长时间阻塞且没有事件，截止时间不能准时触发，也没有首输出预算。
3. **限制后的终态**：工具或时间超限会抛出 `AgentRunLimitExceeded`，streaming
   层写 `agent:run-limit` 错误事件，runtime 的统一异常分支将 run/thread 写成
   `failed` 并结束 SSE。模型限制使用 `exit_behavior="end"`，会由框架提前结束图，
   但业务层无法把它与正常图结束稳定地区分，也没有独立的模型预算终止原因。
4. **工具错误恢复**：`ToolErrorMiddleware` 同时实现同步和异步 wrapper，把普通工具
   异常转换成 `ToolMessage(status="error")` 返回模型继续处理，并回写原工具事件，
   避免前端步骤停在 `in_progress`。`SanitizeToolInputsMiddleware` 的路径/Gitee 参数
   安全拒绝同样可恢复。这是参考实现中最值得吸收的部分；风险是它捕获所有
   `Exception`，可能把编程错误也误当成可恢复输入问题。
5. **循环和预算风险**：未发现相同工具+参数的重复调用检测。模型 5000 次、工具
   60-300 次、总时长 10-30 分钟使错误规划、重复读取和无意义循环有较大空间。
6. **每次 Run 重建对象**：DeepAgent 图、主模型客户端、子 Agent 模型客户端、主/子
   Agent 配置、中间件、prompt、permissions 和 tools 会在每次 `get_agent()` 重建。
   `AgentRunLimitTracker` 也在每次流消费时创建，这是正确的 Run 隔离；两个模型客户端
   和固定 backend 配置重复创建则没有必要。
7. **可安全复用对象**：SQLite Store、checkpointer 已是进程级懒加载；
   `LocalShellBackend` 按 thread 缓存。无可变会话状态的模型客户端和只读静态配置也
   可以复用。工具对象是否缓存取决于其是否闭包捕获 Run 权限；带计数器的中间件、
   task scoped backend 和 Agent 图必须按 Run 隔离。
8. **流式、状态和恢复优点**：v3 raw event 能输出 token delta、todo、工具和子 Agent
   事件；SQLite WAL + busy timeout 支持后台写入和 SSE 读取；run/thread 双层终态、
   checkpoint 与业务 Store 分离、工具错误回写、断线后的持久化事件读取和 token
   脱敏都值得保留。文本按长度/换行批量落库，也避免了逐 token SQLite 写放大。
9. **不可直接复制的绑定**：`E:\\ai_workspace`、盘符/反斜杠处理、PowerShell askpass、
   `.cmd` 启动脚本、`shell=True` 命令拼接、Gitee URL/token/PR/master 分支假设、
   `/projects` 到 Windows 路径的实现，以及该课程项目的 dashboard payload 结构。
   `reviewer_diff.py` 通过拼接字符串执行 `git diff`，也不能用于 Tang_Agent 的命令边界。
10. **Tang_Agent 修改前缺口**：已有 macOS 虚拟工作区、参数数组命令、Run 级事件、
    SSE 续传、任务权限和安全拒绝恢复，但没有模型/工具/时间预算、重复工具检测、
    通用工具异常恢复、首输出统计、Run 性能持久化、性能 API 或可见预算终止原因。

## Reviewer 与子 Agent 结论

LX_AICoding 真正注册到 DeepAgent 的只有 `general-purpose` 子 Agent。它使用单独创建的
模型客户端，限制写项目源码，但并没有注册名为 Reviewer 的专用子 Agent。
`reviewer.py`、`reviewer_diff.py`、`reviewer_publish.py` 主要是读取/格式化 finding、
读取 Git diff 和发布 Gitee 评论的支撑函数；`add_review_finding` 与
`list_review_findings` 作为主 Agent 工具注册。也就是说，课程文档描述了 Reviewer
方向，但当前运行时更接近“主 Agent 调 review 工具”，不能据此声称已有独立 Reviewer
循环或 Reviewer 专属预算。

Tang_Agent 保留清晰职责：主 Agent 拥有最终执行权，`general-purpose` 子 Agent 只读
分析，工具层执行并反馈受控动作。主/子 Agent 共用同一个 Run 级模型和工具治理实例，
所以委派不会绕过总预算；官方 `ModelCallLimitMiddleware` 仍分别保护每个图。后续加入
真正 Reviewer 时，应建立独立 prompt/只读工具集，但继续归入调用它的业务 Run 总预算。

## 重新设计原则

- 保留 `Tang_Agent` 的 `/projects` 虚拟路径和 macOS workspace 解析，不引入盘符规则。
- Git、GitHub CLI 和 Agent 命令继续只接受参数数组并固定关键参数，保持 `shell=False`。
- Agent 图和有状态中间件按 Run 创建；workspace backend、模型客户端、SQLite Store
  和 checkpointer 在进程内复用。
- 官方模型限制与共享 Run 级模型中间件共同限制主/子 Agent；工具预算、重复检测和
  可恢复异常放在共享工具中间件；总时间与首输出截止时间放在带阻塞等待超时的事件
  消费层。任何一层触发都写明确终止原因和最终状态。
- 性能数据使用业务 `run_id` 持久化，不使用 thread 级临时事件代替历史指标。
- 已知的文件、路径、权限、命令策略和超时错误允许模型恢复；未知异常失败得更明确，
  避免把代码缺陷伪装成可重试工具输入错误。

## Tang_Agent 实现映射

| 层 | 实现 | 责任 |
| --- | --- | --- |
| 预算配置 | `backend/app/core/run_limits.py` | 四类预算、单调时钟、首输出/总时长、聚合事件计数 |
| 模型边界 | `middleware/model_governance.py` | 主/子 Agent 共享模型调用计数，调用前截断 |
| 工具边界 | `middleware/tool_governance.py` | 聚合工具预算、规范化重复检测、可恢复错误 |
| Agent 装配 | `backend/app/core/agent.py` | 每 Run 创建图和治理中间件，复用模型/backend |
| Run runtime | `conversation_runtime.py` | 流消费截止、事件、终态、指标最终落库 |
| 持久化/API | `store/navigation.py`、`api/routes.py` | `run_id` 一对一指标与性能查询接口 |
| 前端 | `frontend/src/App.tsx` | 展示预算消耗、延迟、拒绝和终止原因 |

默认相同调用最大出现次数为 2。第二次由工具中间件返回可恢复拒绝，提示模型使用已有
结果或调整参数；如果模型仍发出第三次相同调用，则终止为 `repeated_tool_call`。总时间
消费器使用独立 daemon worker 和逐事件确认：consumer 未处理完当前事件时，不允许流
推进到下一个图节点；等待事件本身也有截止时间，因此比 LX_AICoding 的“下个事件到达
时再检查”更接近实际硬预算。Python 无法强制杀死已经进入第三方 SDK 的阻塞线程，超时
后会停止消费、写终态且不再推进后续节点；模型 HTTP timeout 仍是底层最终中断保障。

## 对比总结

| 能力 | LX_AICoding | Tang_Agent 修改前 | Tang_Agent 修改后 |
|---|---|---|---|
| 模型调用限制 | 内置中间件，单一 5000 次上限，`end` 退出 | 无 | 官方中间件 + 主/子 Agent 共享计数，按模式 3/5/8/16 次，明确终止原因 |
| 工具调用限制 | raw event 统计，任务默认 60-300 次 | 无 | 工具中间件与 runtime 双层校验，任务默认 4/10/20/40 次 |
| 总运行时间限制 | 单调时钟随事件检查，任务默认 600-1800 秒 | 无 | 阻塞等待也受限；首输出 12-30 秒、总时长 45-480 秒，按任务配置 |
| 重复调用检测 | 无 | 仅按 call_id 去重展示事件 | 相同工具+规范化参数检测；重复先可恢复拒绝，持续重复终止 Run |
| 错误恢复 | 工具异常和输入拒绝转 ToolMessage | 命令策略拒绝可恢复，其他工具异常通常失败 | 已知文件/路径/权限/超时错误统一转可恢复 ToolMessage；未知错误仍失败 |
| 性能指标 | 日志和调用计数，不按 run_id 持久化 | 无 | 按 run_id 保存预算、首输出、总耗时、模型/工具/重复/错误/拒绝计数 |
| 前端展示 | SSE 展示步骤，无独立性能数据 | Run 步骤、模式和通用失败 | 性能 API + 执行面板展示预算消耗、延迟和用户可见终止原因 |
| 自动化测试 | 以独立 verify 脚本为主，部分写真实项目数据库 | pytest 覆盖基础 Agent/SSE/Store | pytest 使用假时钟、假流和临时 SQLite 确定性覆盖预算、恢复、终态和 API |
