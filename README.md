# Code Agent

一个不依赖 Agent 框架、从协议层开始自研的终端编码助手。基于 ReAct 循环实现「模型推理 → 工具执行 → 结果反馈」的多轮闭环，默认提供 inline TUI（Textual）界面，支持流式输出、工具审批、树状会话、会话持久化和自动上下文压缩。

## 功能特性

- **inline TUI**：Textual 驱动的默认界面（Claude Code 同款形态），滚动对话流 + 流式回复 + 审批面板 + 状态栏；`--cli` 可切回经典命令行
- **ReAct Agent Loop**：多轮「请求模型 → 执行工具 → 请求模型」直至任务完成
- **8 个内置 Coding Tools**：读文件、写文件、编辑、搜索、列目录、执行命令、Git 状态、Git diff
- **权限审批**：写操作需人工确认，支持单次允许 / 本会话允许 / 拒绝
- **树状会话**：完整对话树保留所有分支，支持回退（rewind）与切换（fork）
- **会话持久化**：对话自动落盘到工作区，重启后可列出、恢复、导出历史会话
- **自动上下文压缩**：接近 token 预算时自动把旧消息摘要成总结，保留最近完整轮次，模型不"失忆"
- **可取消**：Ctrl+C 随时中断当前模型请求或工具执行
- **项目指令**：自动加载工作区根目录的 `AGENTS.md` 作为项目上下文
- **OpenAI 兼容协议**：默认直连 DeepSeek，也可接入 vLLM、Ollama、OpenRouter 等任意兼容服务

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

接入其他 OpenAI 兼容服务时，覆盖 base_url 和 model 即可：

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

默认启动 **TUI 界面**（inline 模式，不切换终端屏幕）：

```text
┌ 对话流（可滚动）───────────────────┐
│ 你  帮我修复 login.py 的空指针      │
│ AI  我先看一下文件…                 │
│   ▸ ✓ success                      │
│ AI  已修复，改动如下…               │
├─────────────────────────────────────┤
│ ▍流式中的回复（完成后落入对话流）    │
├─────────────────────────────────────┤
│ ⚠ 权限审批                          │
│   工具: write_file                  │
│   目标: login.py (write)            │
│   [y] 允许 [a] 本会话允许 [n] 拒绝   │
├─────────────────────────────────────┤
│ > 输入任务，/ 查看命令…              │
│ ⏵ 就绪 · deepseek-v4-flash · /help  │
└─────────────────────────────────────┘
```

**TUI 操作**：回车提交任务、`y/a/n` 应答审批、`Ctrl+C` 取消运行（空闲时退出）、`↑/↓` 翻阅输入历史。

切换经典 CLI 交互：

```bash
code-agent --cli    # 显式使用 CLI
code-agent          # 管道/CI 等非 TTY 环境自动降级为 CLI
```

两种入口共享会话数据，可随时互换。

### 执行任务

用自然语言描述任务即可：

```text
> 帮我看看 src 目录的结构，并修复 login.py 里的空指针问题
```

Agent 会自动循环执行：流式输出思考过程 → 调用工具（读文件、执行命令等）→ 将工具结果反馈给模型 → 继续推理，直到模型判断任务完成。

冒烟测试示例：

```bash
mkdir /tmp/agent-demo && cd /tmp/agent-demo
uv run --project /path/to/Code-Agent code-agent
```

```text
> 帮我创建一个 hello.py，打印当前时间并运行它
```

这条指令会完整走一遍 `write_file`（触发审批）→ `run_command` 执行 → 模型总结的闭环。

### 工具审批

Agent 执行写操作（写文件、执行命令等）时会弹出审批面板（TUI）或行内提示（CLI）：

- `y` — 仅允许本次
- `a` — 本会话内允许该工具的所有调用
- `n` — 拒绝，Agent 会收到拒绝结果并调整策略

### 斜杠命令

| 命令 | 作用 |
|---|---|
| `/help` | 显示帮助 |
| `/new` | 开始新会话（自动持久化） |
| `/model` | 显示当前模型和 endpoint |
| `/sessions` | 列出历史会话；`/sessions <序号>` 恢复；`/sessions export <序号>` 导出 Markdown |
| `/tree` | 显示当前对话树的分支结构 |
| `/rewind` | 预览最近消息；`/rewind <n>` 回退 n 条并从该点分叉 |
| `/fork` | 列出分支；`/fork <序号>` 切换到该分支继续 |
| `/quit` | 退出 |

### 会话持久化

所有对话自动保存到 `<workspace>/.code-agent/sessions/`（JSONL 逐条落盘，已自动写入 `.gitignore`）：

- **恢复**：`/sessions` 列出历史（时间、消息数、标题），`/sessions 2` 直接恢复第 2 个会话继续聊
- **导出**：`/sessions export 2` 生成 Markdown 纪录（默认存到工作区根目录）
- **互通**：TUI 和 CLI 共享会话目录，一边创建的会话另一边可恢复

### 树状会话（rewind / fork）

对话历史是树而非单链，任何历史节点都可以成为新起点：

- `/rewind 3` — 回退 3 条消息，之后的对话从该点**分叉**出新分支（原分支完整保留）
- `/fork 1` — 切换到分支列表中的第 1 个分支继续对话
- `/tree` — 查看所有分支及消息数

典型用法：Agent 把方向走偏了，`/rewind` 回到偏航前的节点换一种说法重新引导。

### 自动上下文压缩

长对话接近 token 预算（默认 32K 的 80%）时自动触发：

- 旧消息被摘要成一条总结（保留任务目标、已完成改动、关键决策、未完成事项），最近 2 个完整轮次保持原文
- 界面显示 `◇ 上下文已自动压缩（N 条历史消息转为摘要）`，全程无感
- 压缩产物绑定分支：rewind 分叉后旧摘要自动失效，不会误用
- 摘要失败时自动降级为截断，任务不中断

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
├── code-agent-core/   # 核心层：tools / session / context / runtime（Agent Loop + 压缩）
└── code-agent-cli/    # 应用层：TUI 与 CLI 双入口、审批、渲染、8 个 Coding Tools
```

分层依赖方向：`cli → core → llm`。核心层只发出 `RuntimeEvent`、不感知任何终端细节——TUI 与 CLI 是同一组合根的两个可替换外壳。

## 开发

```bash
uv sync --all-packages
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范：`feat` / `fix` / `docs` / `chore` / `test` / `refactor` 等，如 `feat(tui): add approval panel`。

## 文档

- 架构设计：[`docs/design.md`](docs/design.md)
- 开发流程：[`docs/development-guide.md`](docs/development-guide.md)
- 各模块实现记录：[`docs/implement/`](docs/implement/)
