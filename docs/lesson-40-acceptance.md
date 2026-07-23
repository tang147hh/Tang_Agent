# 第 40 课验收报告

验收日期：2026-07-23

总体状态：**本地实现与离线验收完成**。

## 1. 修改前调研记录

本节在修改 Tang_Agent 前，只读检查了 LX_AICoding 的
`agent/backends/local_shell.py`、`agent/server.py`、`agent/prompt.py`、
`agent/core/middleware/tool_sanitize.py`、文件工具验证脚本，以及 DeepAgents 文件工具的
装配方式。结论如下。

1. **内置工具还是自定义工具**：模型看到的是 DeepAgents 原生 `glob`、`grep`；
   `create_deep_agent(..., backend=backend_factory)` 根据 backend 协议生成这些工具。
   实际遍历和匹配逻辑由 LX_AICoding 自定义的 `LocalShellBackend.glob/grep` 实现，
   不是独立注册的 LangChain 自定义工具。
2. **Workspace 权限校验**：有双层校验。DeepAgents `FilesystemPermission` 对主 Agent 和
   通用子 Agent 限定 read/write 虚拟路径；backend 的 `_resolve_virtual_path()` 和
   `_to_virtual_path()` 再用解析后的真实路径确认目标仍在 workspace root 内。
3. **工作区外访问**：按预期调用不能访问工作区外；绝对 Windows 路径和符号链接解析后
   越界会被 backend 拒绝。但 `glob/grep` 自身没有独立的路径参数模型或固定虚拟根目录
   allowlist，最终安全性依赖通用 permission middleware 与 backend resolver 同时生效。
4. **符号链接**：实现使用 `Path.rglob()`，没有显式声明符号链接策略，也没有逐项先排除
   symlink。`_to_virtual_path()` 会拒绝解析后逃逸 workspace 的结果，但参考实现没有把
   “不递归符号链接目录、是否返回内部链接”固化为可测试契约。
5. **结果数量和文件大小**：`glob` 和 `grep` 都没有 `max_results`；`grep` 对所有候选文件
   直接 `read_text(..., errors="ignore")`，没有单文件大小、总扫描字节、文件数或耗时上限。
6. **二进制、依赖和敏感文件**：没有统一排除。`grep` 可能扫描二进制、`node_modules`、
   虚拟环境、构建产物、`.git` 和 `.secrets`；无效 UTF-8 字节会被静默忽略。
7. **结构化结果**：`glob` 返回 path、is_dir、size、modified_at；`grep` 返回 path、line、
   text，具备文件、行号和单行片段，但没有截断标记、扫描指标或结果容量说明。
8. **Tang_Agent 不能直接开放原生 glob/grep 的原因**：Tang_Agent 已明确拒绝所有
   DeepAgents 原生文件权限，并把项目访问统一收口到虚拟路径安全工具。直接开放原生
   工具会绕过当前 `TaskScopedBackend`、固定能力元数据、可恢复错误语义、敏感/依赖排除
   和本课所需的确定性容量限制，也会扩大 Reviewer 误获文件工具的风险。
9. **复用第 39 课能力矩阵**：`workspace_glob` 和 `workspace_search` 固定登记为
   `local_read`、low risk、无需网络、可由模型调用；只允许 qa/planning/analysis/coding。
   主 Agent 与 analysis 子 Agent 通过同一工具构建器获得能力，正式 Reviewer 和 GitHub
   Review 发布流程仍保持零搜索工具，工具参数不能覆盖这些静态权限。
10. **减少模型往返**：Agent 先用一次 `workspace_glob` 定位候选路径，或一次
    `workspace_search` 取得文件、行号和受限片段，再只读取命中的少数文件。典型定位流程
    从多次 `workspace_list -> workspace_read` 试探，收敛为一次搜索加一次精确读取；每次
    搜索仍计入既有 tool call、重复调用和 Run 总预算。

## 2. Tang_Agent 安全设计

- 新工具只接受虚拟搜索根；Glob 必须相对于该根，拒绝绝对路径、Windows 盘符、`..`、
  NUL、控制字符和超长输入。
- backend 直接遍历工作区，不执行 shell、`rg` 或任意命令；不提供写入或联网能力。
- 遍历不跟随符号链接，并统一排除依赖目录、版本控制元数据、缓存、构建产物和敏感文件。
- 内容搜索只做字面量匹配；只读取大小受限的 UTF-8 文本，跳过二进制和超大文件。
- 结果按虚拟路径和行号稳定排序，使用固定 `max_results` 上限并返回 `truncated`、扫描数和
  `duration_ms`。
- SSE 只保存虚拟搜索根、模式、结果数量、耗时和截断状态；不复制代码片段、文件内容或
  原始搜索词到事件和前端步骤。

## 3. 自动化结果

### 3.1 实现结论

| 状态 | 验收项 | 结果 |
| --- | --- | --- |
| passed | 固定能力矩阵 | 两工具均为 `local_read`/low risk/无需网络；qa/planning/analysis/coding 允许 |
| passed | 主 Agent | 四种任务模式均经 `build_workspace_tools` 获得搜索能力 |
| passed | analysis 子 Agent | 获得 list/glob/search/read；仍无写入或命令 |
| passed | Reviewer 隔离 | Reviewer `tools=[]`，prompt 明确禁止 glob/search，不能扩大 ReviewDiff 范围 |
| passed | GitHub 发布隔离 | prepare/publish 仍为专用 API；Agent 能力中没有 Review publish 或搜索升级参数 |
| passed | Glob 输入安全 | 虚拟根、相对模式、512 字符、1-500 结果；拒绝主机/盘符/`..`/控制字符 |
| passed | Glob 输出 | path/kind/size、稳定排序、计数、扫描数、截断和耗时 |
| passed | 内容搜索 | 字面量、文件 Glob、大小写选项、path/1-based 行列/snippet 和扫描指标 |
| passed | 文件安全 | 不跟随 symlink；排除依赖、VCS、缓存、构建产物、敏感、二进制、非 UTF-8、超大文件 |
| passed | 容量边界 | 50,000 目录项、1 MB/文件、20 MB/次、500 字符 snippet、最多 500 matches |
| passed | 工具治理 | 实际 Agent 测试覆盖 tool call 预算和相同搜索参数重复终止 |
| passed | 往返优化 | fake Agent 用一次 search 定位、一次 read 精读，共两个工具结果，不再逐层 list/盲读 |
| passed | 事件脱敏 | SSE 无 workspace query、snippet、matches；仅虚拟根/模式/计数/耗时/截断 |
| passed | 前端展示 | 定位/搜索开始与完成文案，匹配数、扫描文件数、耗时和截断状态 |

### 3.2 自动化结果

| 验证 | 结果 |
| --- | --- |
| 后端全量 pytest | 349 passed，1 条第三方 Starlette/httpx 弃用警告 |
| 第 40 课 Workspace 专项 | 39 passed |
| 第 39 课 web_search 回归 | 26 passed，默认 Provider 仍 disabled |
| Workspace/Agent/子 Agent/事件联合定向 | 102 passed |
| 前端 Vitest | 5 files，31 tests passed |
| 前端 oxlint | 通过，无 warning/error |
| 前端生产构建 | TypeScript + Vite 通过，2032 modules |
| Playwright mock E2E | 5 passed；包含 workspace_search 事件和四视口 |
| Git diff 格式 | 通过 |

专项测试覆盖五种课程 Glob、稳定排序、目录开关、截断、虚拟/主机/Windows/逃逸路径、
NUL/控制字符、超长模式/query、1-500 结果、内外部 symlink、依赖/VCS/敏感文件、二进制、
无效 UTF-8、超大文件、字面量、大小写、文件模式、行列/snippet、固定能力元数据、真实
DeepAgent 搜索后精读、重复检测、工具预算、Run 指标以及 SSE 不持久化 query/snippet。

## 4. 浏览器验收

Playwright 复用用户运行在 `127.0.0.1:5174` 的 Vite 服务，并拦截全部 `/api/**`。mock
SSE 在原第 39 课网页搜索前加入 `workspace_search` 开始/完成事件，确认前端显示：

```text
代码搜索完成
3 处匹配 · 扫描 12 个文件 · 4 ms · 结果已截断
```

1440x900、1280x720、768x1024、390x844 均无 body 横向溢出；桌面详情完整，移动端正常
换行，无步骤、文本或控件重叠。截图位于：

- `/tmp/tang-agent-lesson-40/screenshots/1440x900-network-search.png`
- `/tmp/tang-agent-lesson-40/screenshots/1280x720-network-search.png`
- `/tmp/tang-agent-lesson-40/screenshots/768x1024-network-search.png`
- `/tmp/tang-agent-lesson-40/screenshots/390x844-network-search.png`

## 5. 保持不变的边界

- `TANG_AGENT_WEB_SEARCH_PROVIDER` 默认仍为 `disabled`；本课没有访问真实网页搜索。
- `fetch_url` 仍未实现，没有把本地文件搜索扩展成任意 URL 读取。
- 第 38 课真实 GitHub Review 仍因 `tang147hh` 的失效 Token 和缺少专用测试 PR 而 blocked；
  本课没有调用、绕过或放宽发布流程。
- 当前工作区原有大量用户修改均被保留；本课没有暂存、提交、重置或清理任何文件。
