# Tang Agent

运行在 macOS、面向 GitHub 仓库的本地 AI Coding Agent。

## 当前进度

已完成：

- 项目工程骨架
- Python 后端目录分层
- 平台源码与 Agent 工作区分离设计
- Git `main` 分支初始化

尚未接入：

- FastAPI
- 大模型
- DeepAgents / LangGraph
- GitHub API
- React Dashboard

## 项目目录

- `backend/`：FastAPI 与 Agent 后端
- `frontend/`：React Dashboard
- `tests/`：自动化测试
- `scripts/`：启动和验证脚本
- `data/`：本地 SQLite 数据
- `logs/`：运行日志
- `docs/`：架构与课程文档

## Agent 工作区

平台源码位于：

```text
/Users/tang/Documents/projects/Tang_Agent
```

Agent 操作的目标仓库将存放在独立工作区：

```text
~/ai-workspace/projects
```

Agent 不应修改 Tang Agent 平台自身的源码，也不应访问工作区之外的文件。