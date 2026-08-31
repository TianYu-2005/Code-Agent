# CLI 实现

## 目标

`code-agent-cli` 的 `cli/` 提供终端交互：自然语言任务输入、流式模型输出、工具执行状态、权限审批和 Ctrl+C 取消。CLI 只消费 `RuntimeEvent`，不向 AgentLoop 注入任何终端专用逻辑，未来可被 TUI 直接替换。

## 模块

| 模块 | 职责 |
|---|---|
| `cli/app.py` | REPL 主循环：读取输入、分发命令、运行 AgentLoop |
| `cli/renderer.py` | `RuntimeEvent` → 终端输出（流式文本、工具状态着色） |
| `cli/approval.py` | `ApprovalPort` 终端实现（y/a/n 交互） |
| `cli/commands.py` | 斜杠命令解析（/help /new /model /tree /quit 等） |
| `cli/interrupt.py` | SIGINT → `CancellationToken` |
| `config.py` | 环境变量读取 |
| `bootstrap.py` | 组合根：装配 Provider、Registry、Executor、Loop |

## 配置

环境变量：

```text
CODE_AGENT_API_KEY       必填
CODE_AGENT_BASE_URL      默认 https://api.deepseek.com
CODE_AGENT_MODEL         默认 deepseek-v4-flash
CODE_AGENT_MAX_TURNS     默认 20
```

默认值按 [DeepSeek API 文档](https://api-docs.deepseek.com/zh-cn/) 对齐：只设置 API Key 即可直连 DeepSeek。`base_url` 的域名会自动加入 Provider 的可信主机列表，满足模型层的 SSRF 防护要求。

## 交互流程

```text
你> 修复 login.py 的空指针
  │
  ├─ 流式打印模型输出（MODEL_DELTA）
  ├─ ▸ 工具执行中... [success/error/denied]
  ├─ 需要审批时：
  │    工具: write_file
  │    目标: /path/login.py (write)
  │    允许执行？[y]允许 [a]本会话允许 [n]拒绝:
  ├─ Ctrl+C → 取消当前模型请求或工具执行
  └─ 结束后回到输入提示
```

## 事件驱动渲染

`TerminalRenderer` 是纯事件消费者：

- `MODEL_DELTA` → 逐字流式输出
- `TOOL_STARTED` / `TOOL_COMPLETED` → 带颜色的状态行
- 不直接访问 Session、Provider 或工具

这保证了按设计文档的承诺：换成 TUI 时只需提供新的 Renderer 和 ApprovalPort，核心层零改动。

## 组合根

`bootstrap.AgentRuntime` 是唯一允许同时依赖抽象和具体实现的组装点：

- 创建 `RetryingProvider(OpenAICompatibleProvider)`
- 注册全部 8 个内置 Coding Tools
- 加载 `AGENTS.md` 项目指令进 ContextPolicy
- 绑定 `TerminalApprovalPort` 和 `CancelState`

入口命令：`code-agent`（pyproject 的 project.scripts）。

## 测试

- 命令解析：聊天 / 命令 / 退出三分类
- 配置加载：缺失 API Key 报错、环境变量读取、可信域名推导
- 渲染器：流式文本、工具状态、banner
- 审批端口：y 确认、输入流可注入
- 取消状态：信号状态转换
- 组合根冒烟：8 个工具装配、Loop 创建成功
