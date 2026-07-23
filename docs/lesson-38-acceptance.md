# 第 38 课验收报告

验收日期：2026-07-23

总体状态：**部分完成，存在外部环境阻塞**。

本地完整回归、mock GitHub E2E、四视口 Chrome 视觉验收、发布幂等、PR SHA
变化保护、unknown 锁定、SQLite 重启持久化和固定 fake-model 性能基线均已通过。
真实 GitHub 阶段未执行：当前 `gh` 账号为 `tang147hh`，Token 已失效，且用户尚未
提供专用测试仓库和测试 PR。没有向任何真实 PR 写入评论或 Review 状态。

## 1. 验收清单

状态只使用 `passed`、`failed`、`blocked`、`not_applicable`。

| 状态 | 验收目标 | 实际操作 | 实际结果 | 证据 | 未完成原因 | 是否修复 |
| --- | --- | --- | --- | --- | --- | --- |
| passed | 后端完整回归 | 执行完整 pytest | 268 passed，1 条第三方弃用警告，17.91s | `uv --cache-dir /tmp/tang-agent-uv-cache run pytest -q` | 无 | 不适用 |
| passed | 前端完整单元测试 | 执行 Vitest | 4 files、27 tests passed，290ms | `cd frontend && npm test` | 无 | 是；E2E 初次被 Vitest 误收集，已排除 `e2e/**` |
| passed | 前端 lint | 执行 oxlint | 通过，无 lint error | `cd frontend && npm run lint` | 无 | 不适用 |
| passed | 前端生产构建 | 执行 TypeScript 与 Vite build | 2031 modules，构建 214ms | `cd frontend && npm run build` | 无 | 不适用 |
| passed | 项目 Diff 格式 | 执行 `git diff --check` | 无错误 | 最终验证命令 | 无 | 不适用 |
| passed | 开发服务与健康 | 核对进程归属并请求 8001/5174 | 本项目 Uvicorn 与 Vite 均监听，两个地址返回 200 | `lsof`、`ps`、`curl` | 无 | 是；5174 原未启动，现已启动 |
| passed | GitHub remote 与 CLI | 读取 remote、分支和 gh 版本 | `main`，origin 指向 Tang_Agent，gh 2.96.0 | `git remote -v`、`git branch --show-current`、`gh --version` | 无 | 不适用 |
| blocked | 当前分支关联 PR | 执行 `gh pr status` | 无法取得 PR 状态 | gh Token 失效且 API 不可用 | 需先恢复认证 | 否 |
| blocked | Ruff | 检查 `command -v ruff` | 未安装，未执行 | 环境只读盘点 | 不擅自添加 Ruff 依赖 | 否 |
| passed | mock 浏览器 E2E | 系统 Chrome 150 + Playwright + API 拦截 | 4 tests passed，13.7s | `cd frontend && npm run test:e2e` | 无 | 是；新增稳定 E2E 基础 |
| passed | 1440x900 视觉验收 | working tree 三栏流程截图并目视检查 | 页面非空、三栏稳定、无重叠 | `/tmp/tang-agent-lesson-38/screenshots/1440x900-working-tree-review.png` | 无 | 不适用 |
| passed | 1280x720 视觉验收 | PR 发布预览截图并检查弹窗边界 | 弹窗、计数、跳过原因和按钮正常 | `/tmp/tang-agent-lesson-38/screenshots/1280x720-pull-request-preview.png` | 无 | 不适用 |
| passed | 768x1024 视觉验收 | 平板文件标签页截图并检查滚动 | 标签页、文件滚动、工具栏均正常 | `/tmp/tang-agent-lesson-38/screenshots/768x1024-tablet-file-list.png` | 无 | 是；审查视图在平板隐藏全局侧栏 |
| passed | 390x844 视觉验收 | 移动发布弹窗截图并检查边界 | 弹窗未越界，长 PR 标题省略，按钮可点击 | `/tmp/tang-agent-lesson-38/screenshots/390x844-mobile-publish-dialog.png` | 无 | 是；长链接增加省略与图标固定布局 |
| blocked | 应用内 Browser | 按 Browser 恢复流程初始化并列出实例 | 返回 `No browser is available`，可用列表为空 | Browser 运行时只读检查 | 当前会话无 Browser 实例 | 否；改用系统 Chrome 完成实际视觉验收 |
| blocked | GitHub 认证 | 执行 `gh auth status -h github.com` | `tang147hh` 为 active，但 Token invalid | gh 只读输出 | 需要用户运行 `gh auth login -h github.com` | 否 |
| blocked | 专用测试 PR | 检查 remote、分支和 PR 状态 | 当前仓库 remote 存在，未获得专用测试 PR | `origin=https://github.com/tang147hh/Tang_Agent.git` | 用户未指定专用测试仓库/PR | 否 |
| blocked | 真实 COMMENT | 未调用真实 publish | 没有真实 Review ID/URL | GitHub 写入计数为 0 | 认证失效且无专用 PR | 否 |
| blocked | 真实 APPROVE | 未执行 | 没有改变任何 PR Review 状态 | 无真实写入 | 需专用 PR 和单独确认 | 否 |
| blocked | 真实 REQUEST_CHANGES | 未执行 | 没有影响任何 PR 合并状态 | 无真实写入 | 需专用 PR 和单独确认 | 否 |
| passed | mock COMMENT/APPROVE/REQUEST_CHANGES 前端选择 | 浏览器切换三种 event 并检查风险提示 | 三种选项可见，风险文案正确 | Playwright 场景 B | 无 | 不适用 |
| passed | prepare 不写入 | fake runner 记录调用并执行预览 | 预览显示行内/总结/跳过数量；无 POST | pytest + Playwright 场景 B | 无 | 不适用 |
| passed | 用户取消不发布 | 打开预览、按 Enter、取消 | publish 调用保持 0 | Playwright 场景 B | 无 | 不适用 |
| passed | 用户确认后发布 | 主动点击“发布到 GitHub” | publishing 后进入 success，安全 URL 可见 | Playwright 场景 C | 无 | 不适用 |
| passed | 发布防重复 | 成功后按钮锁定；后端重复 payload 测试 | 前端只有 1 次 fake publish，后端无第二次写入 | E2E + `test_duplicate_payload_and_timeout_unknown_block_retry` | 无 | 不适用 |
| passed | PR SHA 变化保护 | fake PR head 变化后 publish | 返回 `pull_request_changed`，无写入 | `test_publish_rejects_changed_head_and_changed_finding` | 无 | 不适用 |
| blocked | 真实 PR SHA 变化 | 未向远程测试分支推送提交 | 没有修改任何真实 PR head | 无真实写入 | 未授权专用 PR 推送 | 否 |
| passed | unknown 人工核对保护 | fake POST 超时并重新打开 SQLite | 状态为 unknown，重启后仍禁止 publish | `test_publication_audit_and_retry_locks_survive_store_reopen` | 无 | 不适用 |
| passed | published 重启持久化 | 发布 fake Review，重新打开 SQLite | publication、Finding、Diff 仍在且不能重复发布 | 同上 | 无 | 不适用 |
| passed | 日志与响应脱敏 | 扫描日志、响应、性能 JSON、浏览器文字和截图 | 发现日志绝对路径问题后已修复；新输出无主机 home、凭据或私钥 | `test_logging_redacts_host_paths_credentials_and_tracebacks` + `rg` 扫描 | 无 | 是 |
| passed | 固定 fake-model 性能基线 | 8 场景各运行 3 次 | 逐 Run 指标与 min/median/max 已记录 | `/tmp/tang-agent-lesson-38/performance.json` | 无 | 不适用 |
| blocked | 真实模型延迟基线 | 仅确认凭据已配置，未发起付费调用 | 未生成真实性能数字 | 性能报告显式标记 blocked | 至少 15 次外部模型调用费用未单独授权 | 否 |
| blocked | 与第 33 课前真实延迟比较 | 保留 152.46s、28 tools、34.54s 历史参照 | 未计算改善百分比 | 性能报告 `comparison_status=blocked` | fake 延迟不能与历史真实模型延迟比较 | 否 |

## 2. 后端安全链路

| 状态 | 验收目标 | 实际操作 | 实际结果 | 证据 | 未完成原因 | 是否修复 |
| --- | --- | --- | --- | --- | --- | --- |
| passed | QA 问候不调用无意义工具 | 固定问候运行 3 次 | 每次 1 model、0 tool；无 list/read/network/Todo | 性能报告 `ordinary_greeting` | 无 | 不适用 |
| passed | 模型调用预算 | 完整运行预算测试 | 超限进入 failed 并保存原因 | `tests/test_run_limits.py`、`tests/test_review_diff.py` | 无 | 不适用 |
| passed | 工具调用预算 | 聚合主/子 Agent 工具事件 | 超限原因 `tool_call_limit` | `test_tracker_enforces_total_tool_call_limit` | 无 | 不适用 |
| passed | 重复工具阻止 | 同参数调用超过阈值 | 第三次终止为 `repeated_tool_call` | `test_tracker_terminates_a_repeated_tool_loop` | 无 | 不适用 |
| passed | Run 超时终态 | fake clock 和阻塞流测试 | 超时进入 failed，不停留 running | `tests/test_run_limits.py`、Run API 测试 | 无 | 不适用 |
| passed | 禁止 `python -c` | 执行命令策略测试 | 命令被拒绝 | `tests/test_command_runner.py` | 无 | 不适用 |
| passed | 可恢复命令错误返回模型 | 执行工具治理与 runtime 测试 | 受控错误作为 ToolMessage，未知异常终止 | `tests/test_task_policy.py`、runtime 测试 | 无 | 不适用 |
| passed | Finding 绑定 run_id | 保存与跨 Run 去重测试 | Finding 可追溯到唯一 Run | `tests/test_review_findings.py` | 无 | 不适用 |
| passed | Diff 路径/行号/side 校验 | 注入非法路径、不可见行、错误 old/new | 整批拒绝且不保存 | `tests/test_review_diff.py` | 无 | 不适用 |
| passed | 截断/脱敏/二进制隔离 | 临时 Git 构造大文件、凭据、二进制 | 标记稳定且无原值泄露 | `tests/test_review_diff.py` | 无 | 不适用 |
| passed | 快照不随工作树变化 | 保存后修改工作树并重复 GET | 两次快照相同 | `test_review_snapshot_is_structured_and_does_not_follow_worktree` | 无 | 不适用 |
| passed | working tree 不可发布 | 对本地快照调用 prepare | 返回 `review_not_publishable` | `test_working_tree_review_cannot_prepare` | 无 | 不适用 |
| passed | 仅 PR Review 可 prepare | fake PR 快照并 prepare | old/new 映射 RIGHT/LEFT，文件级进入总结 | `test_prepare_maps_right_left_and_moves_file_findings_to_summary` | 无 | 不适用 |
| passed | publish 只接受 publication_id | API 请求体测试 | owner/path/line/payload 均不能由前端覆盖 | pytest + `frontend/src/api.test.ts` | 无 | 不适用 |
| passed | 相同 payload 不重复发布 | 两 publication 使用同 payload | 第二次本地拒绝，远端写入不增加 | `test_duplicate_payload_and_timeout_unknown_block_retry` | 无 | 不适用 |
| passed | timeout unknown 不自动重试 | fake write 超时后再次 publish | 仍返回 unknown，无第二次写入 | 同上 + 重启回归 | 无 | 不适用 |
| passed | Agent 不能发布 GitHub Review | 检查工具列表与命令策略 | 无 publish 工具；`gh api` 被拒绝 | `test_agent_command_policy_rejects_gh_api` | 无 | 不适用 |
| passed | 旧 SQLite 兼容升级 | 删除新表/旧列后重新打开 Store | 历史 Run/Finding 保留，新表和列恢复 | Finding/Diff/publication schema upgrade tests | 无 | 不适用 |

## 3. 浏览器交互结论

Playwright 使用系统 `Google Chrome 150.0.7871.130`，Vite 运行于隔离的
`127.0.0.1:5174`，所有 `/api/**` 请求均被可控 fixture 拦截。测试会把未处理 API
请求、JavaScript exception、非预期 console error 和 request failure 视为失败。

- 场景 A：从 Repositories 进入 working tree Review，使用 `all`，搜索/滚动文件，
  定位 old/new Finding，验证状态失败回滚及成功更新，发布按钮保持禁用。
- 场景 B：打开 PR Review，切换三种 event，生成预览，检查 2 条行内评论、2 条总结、
  1 条跳过原因；Enter 和取消均不发布。
- 场景 C：明确确认后只发出一次 fake publish；显示 publishing、success 和 canonical
  GitHub URL；成功后相同 publication 不能再次发布。
- 场景 D：逐一模拟 `pull_request_changed`、`permission_denied`、
  `publication_expired`、`publication_already_published`、
  `publishing_disabled`、`publication_result_unknown`。页面只显示中文受控错误，
  unknown 关闭预览并锁定重试。

## 4. 性能数据

以下为 fake model/fake GitHub 固定案例的本地编排与 SQLite 实测，单位 ms。它用于验证
预算、调用数量和相对稳定性，不代表真实模型网络延迟。

| 场景 | 运行次数 | 总耗时 min / median / max | 首输出 min / median / max | model calls | tool calls | Review 文件 / Diff 字符 / Finding |
| --- | ---: | ---: | ---: | --- | --- | --- |
| 普通问候 | 3 | 3.896 / 4.204 / 4.475 | 1.915 / 2.151 / 2.194 | 1 / 1 / 1 | 0 / 0 / 0 | 0 / 0 / 0 |
| 简单 QA | 3 | 3.889 / 4.020 / 4.023 | 1.926 / 1.959 / 2.025 | 1 / 1 / 1 | 0 / 0 / 0 | 0 / 0 / 0 |
| 单文件读取 | 3 | 5.315 / 5.337 / 5.768 | 1.789 / 2.079 / 2.172 | 2 / 2 / 2 | 1 / 1 / 1 | 0 / 0 / 0 |
| 多文件分析 | 3 | 7.699 / 8.460 / 8.816 | 1.952 / 2.162 / 2.275 | 4 / 4 / 4 | 3 / 3 / 3 | 0 / 0 / 0 |
| 小型 coding Run | 3 | 7.436 / 7.596 / 7.879 | 1.856 / 1.888 / 1.944 | 4 / 4 / 4 | 3 / 3 / 3 | 0 / 0 / 0 |
| working tree Review | 3 | 93.848 / 94.258 / 95.948 | 不适用 | 1 / 1 / 1 | 1 / 1 / 1 | 1 / 143 / 1 |
| pull request Review | 3 | 20.727 / 21.302 / 22.014 | 不适用 | 1 / 1 / 1 | 1 / 1 / 1 | 1 / 113 / 1 |
| GitHub prepare | 3 | 18.277 / 18.463 / 18.558 | 不适用 | 0 / 0 / 0 | 2 / 2 / 2 | 1 / 113 / 1 |

逐 Run ID：

- `ordinary_greeting`：`7c99982f-e94e-43a7-be6e-2fb0e39f97d0`、`1543ed53-47ae-4653-a5f0-67c3afd5074a`、`84afb770-4eea-40e0-b808-06508edfebb1`
- `simple_qa`：`aeb5a735-fe2e-4cba-90a3-c0e315784291`、`f7d4baff-2364-4384-86a1-79d3454f2b48`、`c7e4880d-116c-4a34-9458-32d96f9a6f4c`
- `single_file_read`：`addbb823-24fc-4b8b-b176-7fd42cdb02b5`、`30fb348a-e271-4ded-bb6c-dffe616359d3`、`d2d4566f-82b7-4045-a1a2-566b4b8be5ce`
- `multi_file_analysis`：`7484e6ef-a3b8-4db5-be0c-26bf4752e0f9`、`af758354-217d-4ab2-a4a1-d49463975637`、`a43640ed-3670-40b2-b664-320d75fb0779`
- `small_coding_run`：`30977d60-446c-4d25-b9d3-77046890b394`、`1afb885c-84c5-447b-9753-b3aae5a693c3`、`4c72a8e4-d581-4bad-96e8-b9cdabd6c0fe`
- `working_tree_review`：`1fca23d9-cb9f-49c6-b030-870f76f62760`、`34787293-1218-441d-99d8-96a2f79fa787`、`1ee41a17-80c8-4885-8d95-5d2dbdb0a0ce`
- `pull_request_review` / `github_prepare`：`b3e5ad3d-8d79-42a3-baa0-6981119bd210`、`e98f7542-451f-4846-a376-bbf9bb2df649`、`9db8f843-0dae-4e43-99a8-6fb257600a74`

历史参照仍为复杂 Run 约 152.46s / 28 次工具，以及第 33 课前平均 34.54s。
由于本次没有真实模型延迟，改善百分比没有统计意义，状态为 blocked。

## 5. unknown 人工核对

1. 打开目标 PR，检查是否已经存在相同 Review。
2. 对照 repository、PR number、head SHA、payload hash 和发布时间。
3. 同时查询本地 publication，确认状态仍为 `unknown`。
4. 由人工判断远端是否成功，再决定后续处理。
5. 系统不能把超时自动推断为失败，也不能自动重试。

## 6. 发布结论与下一阶段

Reviewer 主线的本地代码、自动化、mock E2E、四视口 UI 和审计安全边界已经满足交付
条件。真实发布门禁尚未满足，因此第 38 课不能称为“完全通过”。解除阻塞需要：

1. 用户执行 `gh auth login -h github.com`，随后只读复查认证。
2. 用户明确提供专用测试仓库和 open PR。
3. 展示 COMMENT 预览并获得单次写入确认后，完成真实 ID/URL、远端存在性、幂等核对。
4. APPROVE 与 REQUEST_CHANGES 分别再次获得明确确认。

这些门禁继续阻止真实 GitHub Review 写入，但不阻止后续只读网页搜索课程。第 39 课
可以独立从“网页搜索工具的只读接口、来源引用、超时/容量限制和测试替身”开始；任何
网络读取能力都不能扩大 GitHub 发布权限，也不能先开放任意 URL 或发布权限。
