# 第 39 课验收报告

验收日期：2026-07-23

总体状态：**本地实现与离线验收完成**。

本课没有执行真实智谱搜索，也没有实现 `fetch_url`。真实 Provider 默认禁用；这不影响
工具治理、Provider 适配器、Run 授权、缓存、预算、安全边界和 Fake Provider 验收。
第 38 课真实 GitHub COMMENT/APPROVE/REQUEST_CHANGES 的认证阻塞保持原状态，未被网络
工具绕过，也不阻塞本课只读搜索开发。

## 1. 实现结论

| 状态 | 验收项 | 结果 |
| --- | --- | --- |
| passed | 固定工具能力模型 | 五类能力、风险、模式、联网要求、模型可调用性和可用原因均有静态元数据；无动态插件加载 |
| passed | 模式矩阵 | qa/planning/analysis 只读，coding 保留写/命令，Reviewer 工具列表为空且无网络 |
| passed | Run 联网快照 | `network_access` 默认 false，与 provider 一次性写入 Run；旧库逐列迁移 |
| passed | Provider 抽象 | Disabled/Fake/Zhipu；智谱 SDK/客户端延迟加载，缺 SDK/Key 不影响启动 |
| passed | 结构化请求 | query、max_results、domains、recency 均规范化并有硬范围 |
| passed | 出站敏感信息保护 | 私钥、Token、Key、Password、Secret、主机路径、凭据 URL 和大段代码在 provider 前整次拒绝 |
| passed | 结构化来源 | S1...、title、HTTP(S) URL、snippet、source、published_at、rank；URL 清理与去重 |
| passed | 注入防护 | 结果标记为不可信数据；prompt 禁止遵循指令、执行命令、写文件或发布 GitHub |
| passed | 共享网络预算 | 主 Agent 与 analysis 子 Agent 共用搜索次数、结果、15 秒超时、字符和字节预算 |
| passed | 有界缓存 | 10 分钟/128 项，空结果 60 秒，provider+规范参数键，cache hit 不增加 provider request |
| passed | 可恢复错误 | 稳定 network error code 作为结构化 ToolMessage 内容，不单独终止 Run |
| passed | 事件与前端 | 安全查询、结果数、耗时、缓存/截断、来源标题/URL；无 snippet、原始响应或敏感值 |
| passed | 能力接口 | 发送前与 Run 级接口均返回 provider、模式、授权、原因、预算和工具元数据 |
| passed | 外部写隔离 | GitHub Review publish 仍不是 Agent 工具，只能经专用 API 和用户确认 |
| passed | 课程边界 | 没有新增任意 URL 正文读取或 `fetch_url` |

## 2. 自动化结果

| 验证 | 结果 |
| --- | --- |
| 后端全量 pytest | 303 passed，1 条第三方 Starlette/httpx 弃用警告 |
| 第 39 课搜索专项 | 26 passed；不访问真实网络 |
| 前端 Vitest | 4 files，29 tests passed |
| 前端 lint | 通过，无 lint error |
| 前端生产构建 | TypeScript + Vite 通过，2031 modules |
| Playwright mock E2E | 5 tests passed，其中第 39 课场景验证联网快照、来源链接与四视口 |
| Git diff 格式 | `git diff --check` 通过 |

专项测试覆盖：参数边界、域名等价规范化、敏感查询不调用 Provider、追踪参数/fragment/
userinfo 清理、URL 去重、结果凭据清理、空结果、跨 Run 缓存、权限二次检查、搜索次数
限额、Provider timeout、Disabled/Zhipu 懒加载、能力 API、Run 指标、结构化事件和敏感
事件脱敏。

## 3. 浏览器验收

Playwright 复用用户已经运行在 `127.0.0.1:5174` 的 Vite 服务，所有 `/api/**` 请求均由
本地 fixture 拦截，没有调用后端模型、智谱或 GitHub。测试先在聊天框选择 `qa` 和
“允许联网”，断言 POST 仅包含：

```json
{
  "content": "查询 FastAPI 最新文档",
  "task_kind": "qa",
  "network_access": true
}
```

随后消费结构化 SSE，确认“搜索完成”、来源数量和链接可见；链接为
`target="_blank" rel="noopener noreferrer"`。Run 完成后切回“禁止联网”，原请求快照
仍为 true。四个视口均无 body 横向溢出，截图位于：

- `/tmp/tang-agent-lesson-38/screenshots/1440x900-network-search.png`
- `/tmp/tang-agent-lesson-38/screenshots/1280x720-network-search.png`
- `/tmp/tang-agent-lesson-38/screenshots/768x1024-network-search.png`
- `/tmp/tang-agent-lesson-38/screenshots/390x844-network-search.png`

## 4. 安全说明

- `TANG_AGENT_WEB_SEARCH_PROVIDER` 只接受 `disabled` 或 `zhipu`，API 不能传 module、base URL
  或 endpoint。
- `ZHIPU_API_KEY` 只存在后端 Settings 私密字段中，不进入 repr、能力响应、事件或前端。
- 敏感查询返回 `network_sensitive_input_rejected`，不会“脱敏后继续发送”。
- Provider 异常日志只记 error type，不记录原始异常、SDK 对象或响应。
- 搜索结果可包含恶意自然语言，但只作为 `trust=untrusted_external_data` 数据；它不能
  改变工具列表、Run 模式、联网授权或 GitHub publish 门禁。
- `workspace_execute` 白名单本来不含 curl/wget；联网关闭时 TaskScopedBackend 还会拒绝
  curl/wget 和 Git clone/fetch/pull/ls-remote/archive/submodule 网络读取动作。
- Run 总工具调用、重复调用检测和总 timeout 仍是网络预算之外的最终边界。

## 5. 配置与后续

默认配置：

```text
TANG_AGENT_WEB_SEARCH_PROVIDER=disabled
```

部署方安装兼容智谱 SDK并在后端环境配置 `ZHIPU_API_KEY` 后，可改为 `zhipu`。前端不提供
Key 输入。正式启用前建议由部署方使用公开、无敏感信息的查询完成一次真实 Provider
烟测；该烟测不是本课离线自动化的一部分。

后续课程若实现网页正文读取，必须单独增加 URL scheme/userinfo/端口校验、DNS 解析和
连接 pin、每跳重定向复检、私网/回环/保留地址阻断、Content-Type/解压/正文容量限制，
不能直接复用搜索 Provider 作为任意 URL fetch。
