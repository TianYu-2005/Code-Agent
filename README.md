# Code Agent

一个不依赖 Agent 框架、从协议层开始自研的终端编码助手。基于 ReAct 循环实现「模型推理 → 工具执行 → 结果反馈」的多轮闭环，默认提供 inline TUI（Textual）界面，支持流式输出、工具审批、Ctrl+C 取消和树状会话。

## 功能特性

- **ReAct Agent Loop**：多轮「请求模型 → 执行工具 → 请求模型」直至任务完成
- **8 个内置 Coding Tools**：读文件、写文件、编辑、搜索、列目录、执行命令、Git 状态、Git diff
- **权限审批**：写操作需人工确认，支持单次允许 / 本会话允许 / 拒绝
- **树状会话**：保留完整对话树，支持分支查看
- **流式输出**：模型回复逐字渲染，工具执行状态实时着色显示
- **可取消**：Ctrl+C 随时中断当前模型请求或工具执行
- **项目指令**：自动加载工作区根目录的 `AGENTS.md` 作为项目上下文
- **OpenAI 兼容协议**：可接入 DeepSeek 及任意 OpenAI 兼容服务

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 安装

```bash
git clone <repo-url>
cd Code-Agent
uv sync --all-packages
```

## 配置

通过环境变量配置，无需配置文件。默认开箱即用 DeepSeek，只需设置 API Key：

| 环境变量 | 必填 | 说明 | 默认值 |
|---|---|---|---|
| `CODE_AGENT_API_KEY` | 是 | 模型服务 API Key | — |
| `CODE_AGENT_BASE_URL` | 否 | OpenAI 兼容服务地址 | `https://api.deepseek.com` |
| `CODE_AGENT_MODEL` | 否 | 模型名称 | `deepseek-v4-flash` |
| `CODE_AGENT_MAX_TURNS` | 否 | 单次任务最大循环轮数 | `20` |

最简配置：

```bash
export CODE_AGENT_API_KEY="sk-xxxx"
```

DeepSeek 可用模型（参见 [DeepSeek API 文档](https://api-docs.deepseek.com/zh-cn/)）：

- `deepseek-v4-flash` — 旗舰轻量模型（默认）
- `deepseek-v4-pro` — 更强推理能力
- `deepseek-v4-flash-vision-exp` — 实验性，支持图片输入

接入其他 OpenAI 兼容服务（vLLM、Ollama、OpenRouter 等）时，覆盖 base_url 和 model 即可：

```bash
export CODE_AGENT_BASE_URL="https://your-service.com/v1"
export CODE_AGENT_MODEL="your-model"
```

API Key 从 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 获取。

## 使用教程

### 启动

```bash
cd /path/to/your/project   # 工作区 = 启动时所在目录
uv run --project /path/to/Code-Agent code-agent
```

默认启动 **TUI 界面**（Textual inline 模式，Claude Code 同款形态）：滚动对话流、流式回复、工具状态、黄色审批面板和底部状态栏。切换经典 CLI 交互：

```bash
code-agent --cli    # 显式使用 CLI
code-agent          # 管道/CI 等非 TTY 环境自动降级为 CLI
```

两种入口共享会话数据，可随时互换。TUI 中的操作：回车提交任务、`y/a/n` 审批、`Ctrl+C` 取消运行（空闲时退出）、`/help` 查看命令。

### 执行任务

直接用自然语言描述任务即可，其他输入会作为任务发送给 Agent：

```text
你> 帮我看看 src 目录的结构，并修复 login.py 里的空指针问题
```

Agent 会自动循环执行：流式输出思考过程 → 调用工具（读文件、执行命令等）→ 将工具结果反馈给模型 → 继续推理，直到模型判断任务完成。

一个完整的冒烟测试示例：

```bash
mkdir /tmp/agent-demo && cd /tmp/agent-demo
uv run --project /path/to/Code-Agent code-agent
```

```text
你> 帮我创建一个 hello.py，打印当前时间并运行它
```

这条指令会完整走一遍 `write_file`（触发审批）→ `bash` 执行 → 模型总结的闭环。

### 工具审批

当 Agent 执行写操作（如写文件、执行命令）时，会弹出审批提示：

```text
工具: write_file
目标: /tmp/agent-demo/hello.py (write)
允许执行？[y]允许 [a]本会话允许 [n]拒绝:
```

- `y`：仅允许本次
- `a`：本会话内允许该工具的所有调用
- `n`：拒绝，Agent 会收到拒绝结果并调整策略

### 取消运行

在模型输出或工具执行过程中按 `Ctrl+C`，可取消当前运行并回到输入提示符，对话历史保留。

### 斜杠命令

| 命令 | 作用 |
|---|---|
| `/help` | 显示帮助 |
| `/new` | 开始新会话（清空当前对话） |
| `/model` | 显示当前模型和 endpoint |
| `/tree` | 显示当前对话树的分支结构 |
| `/sessions` | 列出历史会话（尚未实现） |
| `/compact` | 手动压缩上下文（尚未实现） |
| `/quit` | 退出 |

### 项目指令（AGENTS.md）

在工作区根目录放置 `AGENTS.md`，内容会自动注入系统提示，用于告知 Agent 项目的构建方式、代码约定等：

```markdown
# 项目约定

- 使用 uv 管理依赖
- 运行测试：uv run pytest
- 代码风格：遵循 ruff 默认规则
```

## 项目结构

```text
packages/
├── code-agent-llm/    # 模型层：Provider 协议、OpenAI 兼容适配、重试、FakeProvider
├── code-agent-core/   # 核心层：tools / session / context / runtime（Agent Loop）
└── code-agent-cli/    # 应用层：TUI 与 CLI 双入口、审批、渲染、8 个 Coding Tools
```

分层依赖方向：`cli → core → llm`，核心层不感知终端，未来可用 TUI 替换 CLI 而零改动核心。

## 开发

```bash
uv sync --all-packages
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范：`feat` / `fix` / `docs` / `chore` / `test` / `refactor` 等，如 `feat(cli): add compact command`。

## 文档

- 架构设计：[`docs/design.md`](docs/design.md)
- 开发流程：[`docs/development-guide.md`](docs/development-guide.md)
- 各模块实现记录：[`docs/implement/`](docs/implement/)
