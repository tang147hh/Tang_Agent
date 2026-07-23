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
- Git Diff 专项：`agent/reviewer_diff.py`、`agent/reviewer.py`、
  `agent/reviewer_findings.py`、`agent/reviewer_publish.py`、
  `agent/tools/reviewer_tools.py`、`agent/skills/code-review/SKILL.md`、
  `agent/backends/local_shell.py`、`agent/backends/permissions.py`、
  `agent/core/repo_mapping.py` 和 `agent/server.py` 的实际接入。
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

Tang_Agent 保留第 34 课只读 `reviewer` 子 Agent 作为兼容入口，但第 35 课以后，未绑定
受控 `ReviewDiff` 的子 Agent 输出会在 runtime 被拒绝，不能持久化 Finding。正式入口是
`POST /api/runs/{run_id}/reviews`：后端先从 Run 上下文收集、脱敏和限制 Diff，再直接调用
不带任何工具的 Reviewer 模型。该调用与主/子 Agent 共用业务 Run 的模型、内部操作和
总时长指标，不会通过新建无预算子 Agent 绕过限制。

## Reviewer Finding 设计分析

1. LX_AICoding 的 `ReviewFinding` dataclass 只有 `file`、`line`、`severity`、`title`、
   `description`；SQLite 另有 `id`、`thread_id`、`status`、`created_at`、`updated_at`。
2. Finding 外键指向 Thread，不指向产生它的具体 Run；同一 Thread 多次审查的结果无法
   准确区分。
3. 模型并不返回统一的结构化审查对象。主 Agent 调用 `add_review_finding` 工具逐条写入，
   LangChain 只负责工具参数反序列化，没有 findings 批次模型或 JSON Schema。
4. `add_finding` 按随机短 ID 插入或更新，`list_findings(thread_id)` 按创建时间查询；没有
   语义指纹或同 Run 去重。发布层把列表格式化成文本后调用 Gitee 评论 API。
5. `file` 和 `line` 没有 Workspace 归属、绝对路径、逃逸、正整数或文件范围校验。
6. 参考实现没有 Reviewer 批次 JSON 解析器。坏 JSON 可能在模型工具调用层失败，但缺少
   确定的整批拒绝、拒绝计数和 Reviewer 专属终态恢复。
7. 可复用的是领域格式化、业务 SQLite 与 checkpoint 分离、只读审查职责和发布层独立。
8. 不能复制的是 thread 级归属、弱字符串字段、随机 ID 去重、拼接字符串执行 Git diff、
   Gitee/Windows 路径绑定，以及让模型直接控制系统字段。
9. Tang_Agent 用 `review_findings.run_id` 外键绑定产生 Finding 的业务 Run。Thread 仍可
   通过 Run 间接查询历史，但数据归属、预算、终态和审查输入都能追溯到单次执行。
10. 第 34 课只建立结构化输出、校验、去重、持久化、查询和状态更新。第 35 课负责安全
    Git diff 与实际行范围核对，第 36 课负责完整前端 Review 面板，第 37 课负责用户确认
    后的 GitHub Review 发布。

Tang_Agent 采用两个明确模型：`ModelReviewFinding` 只允许模型提供 severity、category、
位置、标题、描述和建议，且 `extra=forbid`；`ReviewFindingSnapshot` 再由后端补充 ID、
run_id、status、fingerprint 和时间。Markdown JSON 代码块可以受控解包，但不会从任意
自然语言中用正则提取 JSON。任何单项无效都会整批拒绝并让 Run 进入 `failed`，已完成
校验的批次才会进入 SQLite。

## Git Diff 专项审阅与取舍

LX_AICoding 查找仓库时，`repo_mapping.py` 优先验证 SQLite 映射，再检查
`projects/<repo>`，最后只扫描 `projects/*` 并用 origin 匹配；这是比接受任意 cwd 更好
的起点。但 `reviewer_diff.py` 最终仍接受 `repo_dir` 和任意 `base` 字符串，并执行：

```python
backend.run(f"git diff --unified=80 {base}", cwd=repo_dir)
```

底层 `LocalShellBackend.run()` 把字符串交给 `subprocess.run(..., shell=True)`。虽然通用
命令守卫屏蔽部分 Shell 操作符，审查函数本身没有固定 argv、`--` pathspec、防任意 base
校验或 Reviewer 专属 Git 白名单。它只得到一整段普通 diff：

1. 没有区分 staged、unstaged 和 all；默认 `git diff HEAD` 混合工作树差异，但漏掉
   untracked，也没有单独审查 index 的接口。
2. 无 HEAD 的新仓库会失败；未跟踪新增文件完全不可见。
3. rename/delete 只依赖文本 diff，没有 old/new path 领域字段；binary 和 submodule
   没有独立元数据或禁止递归策略。
4. 没有文件数、单文件/总字符、变更行或模型上下文限制，且统一 `--unified=80` 会显著
   放大上下文。
5. 没有凭据扫描或脱敏；Diff 可以原样进入模型、日志或发布层。
6. Prompt/Skill 规定“只审查 diff、不要纯风格建议”，方向正确，但没有把 Diff 指令视为
   不可信数据的提示词注入防护。
7. Finding 由模型通过 `add_review_finding` 工具逐条写入，路径和单个 line 不与 Diff hunk
   核对，也没有 old/new side；模型幻觉出的文件或行可以保存。
8. `reviewer_publish.py` 与 Gitee 评论发布分层清楚，值得保留接口边界，但第 35 课不能让
   Reviewer 自动发布，Tang_Agent 将发布继续留到第 37 课。

Tang_Agent 的重新设计使用 Run/Thread/Project 注册关系和 Workspace `resolve()` 确定
唯一仓库；比较 Git `--show-toplevel` 与项目真实根目录，阻止父仓库误命中和符号链接
逃逸。所有命令是固定 argv、`shell=False`、NUL 分隔机器输出、非交互环境、明确 cwd 和
超时；不会 add/commit/checkout/reset/clean。`staged` 比较 HEAD/空树到 index，
`unstaged` 比较 index 到工作树，`all` 比较 HEAD/空树到最终工作树并用
`ls-files --others --exclude-standard -z` 加入未忽略文件。

`ReviewDiffFile` 保留 change type、old/new 虚拟路径、binary/submodule、增删行、受限
patch、截断原因以及从多个 unified hunk 确定性解析的 old/new 变更行。未跟踪文本在
工作区内有界读取并合成 `/dev/null` patch；二进制不携带内容；子模块强制 short diff。
脱敏先于 hash 和 Prompt，截断后重新解析可见行，因此 Finding 只能绑定模型实际看到的
hunk。删除行定位 `old`，普通/新增行定位 `new`，二进制只允许文件级 Finding。

## 安全 Diff 对比

| 能力 | LX_AICoding | Tang_Agent 修改前 | Tang_Agent 修改后 |
|---|---|---|---|
| 仓库边界 | 映射/默认名/origin 扫描，但 review 函数仍接收 repo_dir | 已有 `/projects` Workspace 和 Project 注册，无审查链路 | 只从 run_id 关联注册项目；解析真实路径、校验独立 Git 根并阻止逃逸/符号链接 |
| Git 命令安全 | 拼接字符串，底层 `shell=True` | 通用 CommandRunner 为 argv + `shell=False` | 专用固定 argv、NUL 输出、`--`、非交互、无可选锁、明确 cwd/超时和受控错误 |
| staged/unstaged | 单条 `git diff ... HEAD`，无 scope | 无 | `staged`、`unstaged`、`all` 三种稳定语义 |
| untracked | 遗漏 | 文件级统计可见，未进入 Reviewer | `all` 使用 `ls-files --others --exclude-standard -z`，有界读取并合成 patch |
| 二进制文件 | 无独立处理 | 文件级统计标 binary | 只传元数据和文件级 Finding，不传二进制内容 |
| Diff 容量限制 | 无，且上下文 80 行 | 文件统计最多 500 项，无 Reviewer patch | 50 文件；单文件 40k 字符/800 行；总计 200k 字符/3000 行；确定性 UTF-8 截断 |
| 敏感内容 | 无 | 敏感文件可在统计层隐藏，无内容审查 | 私钥、GitHub/API/Bearer/Access Token、Secret/Password/.env 值逐行 `[REDACTED]` |
| 提示词注入 | 只有一般审查说明 | Reviewer 未接收 Git Diff | Diff 明确为不可信数据；正式 Reviewer 无文件、命令、网络或发布工具 |
| 行号映射 | 模型任意 file/line，不核对 hunk | Finding 只校验正数和项目路径 | 后端解析 old/new hunk；文件、side、可见上下文及变更行全部校验后保存 |
| 预算控制 | Reviewer 支撑函数没有独立共享预算 | 第 33/34 课子 Agent 共享预算 | Diff 计内部 tool_calls；正式 Reviewer 计 model_calls/总时长；超限保证活跃 Run 终态 |
| 自动化测试 | 无 pytest，主要是 verify 脚本 | 第 33/34 课预算和 Finding 测试 | 临时 Git 仓库覆盖 scope、无 HEAD、路径、类型、脱敏、截断、注入、定位、API、预算和迁移 |

## 前端代码审查工作台对比

LX_AICoding 的 `/review` 主要是远程仓库启用和组织级规则设置，不是逐文件审查结果
工作台。其 Agent 消息内 `DiffView` 从 original/new 完整内容在浏览器重新计算 diff，
只显示单行号，并带 `@ts-nocheck` 和 `any`；它不绑定审查时快照，也没有结构化 Finding
定位或状态回滚。Tang_Agent 第 36 课继续使用现有 Project/Thread/Run 导航，但把审查
结果做成独立工作视图，并以服务端快照作为唯一内容来源。

| 能力 | LX_AICoding | Tang_Agent 修改前 | Tang_Agent 修改后 |
|---|---|---|---|
| 审查入口 | PR Review 设置页和 Agent 消息内零散 Diff | 只有后端 POST Review，无前端入口 | Repository 详情和聊天 Run 均可进入；未登记仓库不能绕过 Project 边界 |
| 文件变更列表 | 没有审查结果文件导航 | 执行侧栏仅有累计 numstat | 可搜索，显示 M/A/D/R/C/U、目录、增删行、Finding 数、binary/submodule/truncated |
| Diff 查看 | 浏览器从两份完整文件重新计算，最多默认 20 行 | 前端拿不到审查内容 | 后端返回 hunk/line，前端只渲染当前文件，长行水平滚动且内容按纯文本显示 |
| old/new 行号 | 只显示一个合并行号 | Finding 有 line_side，但无可视化 | 固定 old/new 双列和 marker；删除行定位 old，新增/修改定位 new |
| Finding 定位 | 无结构化 Finding 到 Diff 定位 | 只有查询/PATCH API | 自动选文件、滚动居中、高亮范围；文件级定位标题，无效位置受控提示 |
| Finding 筛选 | 无 | 后端仅 severity/status 查询 | 前端 severity/category/status 联合筛选并按严重级别稳定排序 |
| 状态管理 | 无专用审查状态工作流 | PATCH 已存在但前端未使用 | open/resolved/dismissed 菜单，防重复提交，乐观更新失败自动回滚 |
| 截断提示 | Diff 无容量边界 | POST summary 有截断文字 | 总体和单文件明确提示“仅覆盖已展示部分”，不声称完整审查 |
| 脱敏提示 | 无凭据脱敏 | 后端已脱敏但前端不可见 | 快照保存 redacted 标记，工作台显示疑似凭据已隐藏，永不返回原值 |
| 响应式布局 | Review 设置页响应式，结果 Diff 仅消息内小窗 | 无 Review 页面 | 桌面三栏和可折叠面板；平板/移动端文件、Diff、问题标签页 |
| 快照一致性 | 不保存模型实际看到的 Diff，前端自行重算 | 只保存 Diff 哈希和 Finding 元数据 | `run_id` 一对一 SQLite 受控快照；GET 不读工作树；重新审查创建新 Run |
| 自动化测试 | 主要验证脚本，无该工作台测试 | 前端无测试脚本 | Vitest 覆盖筛选、排序、聚合、old/new/文件定位、回滚、scope、纯文本/binary/truncated；pytest 覆盖快照不变性、脱敏、迁移和 API |

## GitHub Review 发布专项审阅

对参考项目的 10 项结论如下：

1. 远程仓库和 PR 由 Agent/调用者直接传 `owner`、`repo`、`number`；repository mapping
   主要服务本地 Gitee clone 路径，不为发布函数建立可信 PR 身份。
2. `format_findings_comment()` 把 Finding 列表拼成一条普通 Markdown 评论，不生成行内
   path/line/side，也不验证评论是否对应 PR Diff。
3. 发布前不查询或校验 base/head commit SHA。
4. `publish_gitee_pr_comment` 是模型可见工具，不要求前端预览或用户确认。
5. 没有 payload hash、publication 唯一键或已发布状态，重复工具调用可能重复评论。
6. `agent/server.py` 明确把发布评论工具注册给主 Agent；只读 task guard 也没有覆盖该工具。
7. Finding 保存在 thread 级表，但评论返回结果没有独立持久化状态、远程 ID/URL 或审计链。
8. Gitee HTTP 使用 httpx，但本地 Review Diff 和通用命令路径仍存在字符串命令和
   `shell=True`；这不能作为 GitHub Review 发布的安全基础。
9. HTTP 失败直接抛出包含远端响应文本的异常；超时后没有区分明确失败和远端结果未知，
   也没有安全重试状态机。
10. 可吸收的是 Finding 格式化与发布模块分层；必须修正的是仓库/PR 身份、SHA、行号、
    用户确认、Agent 隔离、幂等、审计、脱敏错误和超时未知态。

Tang_Agent 第 37 课只从 registered project 的 origin 解析 github.com owner/repo，PR Review
快照保存 base/head SHA 和模型实际看到的受控 Diff。prepare 从 SQLite Finding 重建
path/line/side 和评论，不接受前端 GitHub payload；用户在确认弹窗主动点击后，publish
仍重新查询 PR head 并原子认领 publication。发布能力不进入 Agent、Reviewer 或通用
workspace tool，`gh api` 继续被命令策略拒绝。

| 能力 | LX_AICoding | Tang_Agent 修改前 | Tang_Agent 修改后 |
|---|---|---|---|
| GitHub 仓库验证 | 发布参数由 Agent/调用者提供，主要支持 Gitee | 仅 Repository PR 创建链路校验 GitHub origin | 从 Run 的 registered project 读取 origin；严格解析三种 github.com URL，拒绝覆盖/Enterprise/歧义 remote |
| PR 快照 | 无 | 只有本地工作树快照 | `pull_request` 快照保存 repository、PR、base/head SHA、受限 files/hunks/lines、hash、截断和脱敏状态 |
| SHA 一致性 | 无 | 工作树 Finding 无远端 SHA 语义 | prepare 和 publish 两次查询 head；变化即 `pull_request_changed` |
| 用户确认 | Agent 可直接发布 | 无发布能力 | prepare 只读预览；前端明确点击“发布到 GitHub”才调用 publish，取消/Enter/刷新不写入 |
| 行号映射 | 一条普通评论，无 PR 行映射 | old/new Finding 只用于本地定位 | new→RIGHT、old→LEFT，多行 start/end；文件/全局/二进制进总结，Diff 外或截断不可见区拒绝 |
| 发布预览 | 无 | 无 | 服务器端 publication 返回行内、总结、跳过、warning、payload hash 和过期时间 |
| 防重复发布 | 无 | 无 | SQLite 原子状态机 + payload hash；并发、已发布和 unknown 均阻止重复写入 |
| 超时未知状态 | 超时统一异常 | 无 | 写请求超时或响应无法确认记为 `unknown`，禁止自动重试 |
| Agent 权限隔离 | 发布工具注册给主 Agent | 通用命令已受白名单限制 | 发布仅由用户 UI 调专用 API；Agent 工具无 publish，workspace_execute 拒绝 `gh api` |
| 审计记录 | 无独立发布记录 | Finding/Diff 按 Run 保存 | 保存 repository、PR、SHA、event、Finding IDs、hash、状态、GitHub ID/URL/user、时间和受控错误 |
| 前端发布流程 | Review 设置页，无 Finding 发布确认 | 三栏本地 Review 工作台 | 来源/PR 选择、Finding 勾选、event、预览弹窗、loading/success/failed/unknown 和 GitHub 链接 |
| 自动化测试 | 主要验证脚本，可能触及真实服务 | fake Reviewer、临时 Git/SQLite | fake gh 与请求体覆盖仓库/PR/SHA/行号/脱敏/幂等/超时/迁移；前端覆盖受控参数、选择、弹窗和错误 |

第 38 课应用内 Browser 仍无实例，但系统 Chrome 150 可用。项目新增 Playwright E2E，
通过请求拦截在 1440x900、1280x720、768x1024、390x844 完成真实渲染、交互与截图。
验收覆盖三栏/标签页、old/new/文件级定位、长行与文件列表滚动、预览/取消/确认、三种
event、success/failed/unknown 和无未处理请求。平板侧栏挤压与移动长链接溢出已修复。

这仍不等于真实 GitHub 发布：gh Token 失效且没有专用测试 PR，因此 COMMENT、APPROVE、
REQUEST_CHANGES 的真实结果均为 blocked。固定 fake-model 基线每场景运行 3 次，适合
验证调用预算和本地编排稳定性；它不能与历史真实模型 152.46 秒或 34.54 秒直接计算
改善百分比。完整证据见 `docs/lesson-38-acceptance.md`。

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
| Reviewer | `core/review.py`、`core/subagents.py` | 结构化解析、路径校验、指纹及只读审查 |
| 安全 Diff | `core/review_diff.py`、`core/code_review.py` | Run 仓库解析、Git scope、脱敏/截断、无工具 Reviewer 和 hunk 范围校验 |
| GitHub Review | `core/github_review.py`、`store/navigation.py`、`api/routes.py` | remote/PR 验证、PR 快照、prepare/publish、幂等状态和审计 |
| 发布前端 | `frontend/src/review/ReviewWorkspace.tsx` | 来源/PR/Finding 选择、预览确认、成功/失败/unknown 展示 |
| 端到端验收 | `frontend/e2e/review-acceptance.spec.ts`、`playwright.config.ts` | fake API、系统 Chrome、四视口截图、发布交互和异常状态 |
| 性能基线 | `scripts/lesson_38_benchmark.py` | 固定 fake model/GitHub 场景、逐 Run 指标及 min/median/max |
| 日志安全 | `core/logging_config.py` | 渲染后统一隐藏凭据、私钥和宿主机 home 路径 |

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

## Review Finding 对比

| 能力 | LX_AICoding | Tang_Agent 修改前 | Tang_Agent 修改后 |
|---|---|---|---|
| Finding 数据归属 | `thread_id` 外键 | 无 Finding | `run_id` 外键，可经 Run 追溯 Thread/Project |
| 结构化输出 | 主 Agent 逐条调用弱类型工具 | 无 | `{findings, summary}` 批次模型，Pydantic 禁止额外字段 |
| 路径安全 | `file` 原样保存 | 已有 Workspace，但未用于 Finding | Workspace 规范化为当前项目内 `/projects/...`，拒绝主机、盘符、逃逸和跨项目路径 |
| 行号校验 | `line` 可空，无范围校验 | 无 | 文件与起止行整体一致，行号为正且结束行不小于开始行 |
| 指纹去重 | 随机短 ID，无语义去重 | 无 | 规范化位置、severity、category、title 的 SHA-256；服务和数据库双层去重 |
| 持久化 | SQLite thread 级表 | 无 | SQLite run 级表、外键、CHECK、唯一约束和兼容建表升级 |
| 查询接口 | 运行时任务摘要附带，无专用筛选 API | 无 | Run 子资源 API，支持 severity/status 筛选和稳定排序 |
| 状态更新 | Store 有初始 status，无专用更新 API | 无 | PATCH 只能修改 status 与 updated_at，校验 Run 归属 |
| 运行预算 | Review 工具在主 Agent 中，5000 次模型上限 | 无 Reviewer | Reviewer 与主/子 Agent 共用第 33 课模型、工具和总时长预算 |
| 自动化测试 | 主要为课程验证脚本 | 无 Finding 测试 | fake model/stream、临时 SQLite 和 API 测试覆盖解析、安全、去重、终态和权限 |

## 第 39 课：LX_AICoding 网页工具基线记录

修改前只读检查了 `agent/tools/web_search.py`、`fetch_url_tools.py`、`safe_http.py`、
`runtime_context.py`、工具导出、sanitize/error middleware、streaming runtime、prompt、
server 及两个 verify 脚本。记录如下：

1. `web_search` 由 `agent.tools` 直接导出，并在 `server.get_agent()` 的主 Agent `tools`
   数组中无条件注册；通用子 Agent没有独立网络能力装配。
2. Provider 是智谱 Web Search，固定 `search_engine="search_pro"`，注释称搜狗搜索；
   每次固定请求 3 条。
3. 客户端通过 `_get_zhipu_client()` 延迟创建，先尝试 `zai.ZhipuAiClient`，再尝试
   `zhipuai.ZhipuAI`。缺 `ZHIPU_API_KEY` 或 SDK 时只在调用工具时返回错误，不阻止
   FastAPI 导入和启动。这一点值得保留。
4. Provider 响应只读取 `search_result[].content` 并用空行拼接为字符串。
5. 没有稳定返回 title、URL、source、rank 或 citation ID，因此模型和用户不能验证来源。
6. 工具装配不按 `coding/analysis/planning/qa/reviewer` 或 Run 授权限制；prompt 只建议
   何时搜索，不能构成权限边界。
7. 没有独立网络预算、结果字符/字节预算或 provider timeout；只受整体 Agent 上限影响。
8. 正常查询、结果前 1,200 字符和异常会进入事件/日志；仅异常字符串调用 token mask，
   查询在出站前没有敏感信息拒绝。
9. 搜索结果没有“不可信外部数据”结构边界，也没有确定性提示词注入防护；拼接正文会
   直接作为普通工具文本返回模型。
10. `fetch_url` 和 `safe_http` 的逐跳 URL/DNS 校验值得后续安全正文读取课程参考，但本课
    明确不实现任意 URL fetch，避免把两个不同风险面混在一起。

吸收的设计是延迟 SDK 导入、缺配置不影响启动、工具失败返回可恢复内容，以及未来
fetch 需要逐跳 SSRF 防护。不能复制的是全模式无条件注册、主 Agent 直接绑定 SDK、
原始查询/结果日志、正文字符串返回、无来源、无 Run 授权/网络预算，以及把外部写工具
与普通 Agent 工具放在同一注册数组。

## 结构化网页搜索对比

| 能力 | LX_AICoding | Tang_Agent 第 39 课 |
| --- | --- | --- |
| 工具注册 | 主 Agent 无条件直接注册 | 固定能力目录；Run 未授权时不装配，工具内部再次校验 |
| Provider | 智谱 SDK 直接依赖 | `SearchProvider` 协议；Disabled/Fake/Zhipu 三种固定实现 |
| 启动兼容 | 延迟 client，缺 SDK/Key 调用时报错 | 延迟 import/client；能力 API 预先给出受控 unavailable reason |
| 请求模型 | 仅 `query` | query、1-5 results、最多 5 domains、1-365 天 recency |
| 出站保护 | 无调用前拒绝 | 凭据、私有路径、凭据 URL、大段代码整次拒绝，不替换后发送 |
| 返回 | 拼接 `content` 字符串 | 完整结构化 envelope + S1... citation/title/URL/snippet/source/rank |
| URL 安全 | 不返回 URL | 仅 HTTP(S)、无 userinfo/fragment/tracker、IDNA 主机、去重 |
| 提示词注入 | 无显式数据边界 | `trust=untrusted_external_data`、系统 prompt、无写/发布能力升级 |
| 权限 | 不按模式/Run | qa/planning/analysis/coding 需用户授权；Reviewer 永久禁止 |
| 预算 | 无网络子预算 | 主/analysis 子 Agent 共享次数、结果、超时、字符、字节预算 |
| 缓存 | 无 | provider+规范参数键、10 分钟/128 项、空结果短 TTL |
| 事件 | 查询与结果 preview | 安全查询、计数、耗时、缓存/截断、标题/URL；无 snippet/原始响应 |
| 外部写 | 与多个 Gitee 写工具并列注册 | GitHub publish `model_callable=false`，仍只走专用确认 API |
| 测试 | verify 脚本可能直接 invoke 工具 | Fake provider、临时 SQLite、pytest/Vitest/Playwright，全程不访问真实搜索 |

## 第 40 课：工作区文件定位与代码搜索对比

LX_AICoding 通过 `create_deep_agent(..., backend=backend_factory)` 使用 DeepAgents 原生
`glob`/`grep` 工具名，实际逻辑由自定义 `LocalShellBackend.glob/grep` 提供。主 Agent
和通用子 Agent 的 `FilesystemPermission` 限制 read 路径，backend resolver 再检查解析后
路径位于 workspace root；这套“双层路径边界”值得保留。

参考实现的 `glob` 使用 `Path.rglob("*")` 和 `fnmatch`，`grep` 对全部候选文件执行
`read_text(errors="ignore")`。两者没有结果、文件大小、总字节或目录项上限，不排除
`.git`、依赖、构建产物、`.secrets` 或二进制；符号链接行为没有显式契约。`glob` 返回
path/type/size/mtime，`grep` 返回 path/line/text，结构化定位本身有价值，但不能原样开放。

Tang_Agent 保持 DeepAgents 原生文件权限全局 deny，新增两个固定注册的自定义安全工具：

| 能力 | LX_AICoding | Tang_Agent 第 40 课 |
| --- | --- | --- |
| 模型工具 | DeepAgents 原生 `glob`/`grep` | `workspace_glob`/`workspace_search`，经现有 workspace 工具构建器 |
| 权限 | DeepAgents permission + backend resolver | 第 39 课固定 `local_read` 能力 + TaskScopedBackend + Workspace resolver |
| 可用角色 | 主/通用子 Agent 由文件权限控制 | qa/planning/analysis/coding 主 Agent和 analysis 子 Agent；Reviewer 永久无工具 |
| 路径 | backend 接受虚拟路径，也兼容绝对 Windows 路径后再判 workspace | 只接受单斜杠开头的虚拟路径；拒绝主机/Windows/`..`/控制字符 |
| Glob | 虚拟全路径或 basename fnmatch | 相对搜索根、分段 `**`、稳定排序、1-500 结果、512 字符模式 |
| 内容语义 | 大小写敏感 substring | 字面量搜索；显式 `case_sensitive`，文件 Glob 单独控制 |
| 符号链接 | `rglob` 隐式行为，结果转换时检查越界 | 所有 symlink 均不返回、不递归；搜索根逃逸由 resolver 拒绝 |
| 排除项 | 无统一排除 | VCS、依赖、venv、缓存、构建产物、敏感文件统一跳过 |
| 文本边界 | `errors="ignore"`，无大小上限 | UTF-8 普通文件，1 MB/文件、20 MB/次、二进制和无效 UTF-8 跳过 |
| 结果 | path/line/text，无截断说明 | path/1-based line+column/500 字符 snippet + 扫描指标/耗时/截断 |
| 运行治理 | 只受参考项目较宽的通用上限 | 每次计入第 33 课 tool call、重复检测和按模式 Run 预算 |
| 事件 | 通用工具步骤 | 只持久化虚拟根/模式/计数/耗时/截断，不复制 query/snippet/matches |

Agent 提示词要求先用一次路径或内容搜索定位，再对命中文件调用 `workspace_read`。典型
代码定位从多次 list/read 试探收敛为“1 次搜索 + 1 次精读”，但工具不会自动扩大项目
权限，也不能被 Reviewer 用来读取 `ReviewDiff` 之外内容。
