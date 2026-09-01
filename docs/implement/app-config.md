# 配置与运行时切换实现

## 目标

解决四个使用痛点：启动配置繁琐、工具逐次审批打断流、模型无法运行时切换、TUI 视觉节奏欠佳。全部改动收敛在 `code-agent-cli` 应用层，核心层零改动。

## 配置系统（config.py）

四层优先级：**CLI 参数 > 环境变量 > 项目配置 > 全局配置 > 默认值**。

| 来源 | 位置 | 说明 |
|---|---|---|
| 全局 | `~/.code-agent/config.toml` | 跨项目共享；目录可用 `CODE_AGENT_HOME` 覆盖 |
| 项目 | `<workspace>/.code-agent/config.toml` | 覆盖全局；与 sessions/processes 同目录 |
| 环境变量 | `CODE_AGENT_*` | 兼容原有用法 |
| CLI 参数 | `--model` `--api-key` `--base-url` `--workspace` `--cli` | `main.py` 统一 argparse |

要点：

- **首次启动向导**：`load_config_or_wizard()` 在 API Key 缺失且 stdin 是 TTY 时进入交互引导（Key / Base URL / Model），写入全局配置（权限 600）后正常启动；非 TTY 环境仍直接报错，保证 CI 可预期。
- **模型 profile**：`[profiles.<name>]` 段声明 `model` / `base_url` / `api_key`（后两者可省略，沿用顶层）。内置三个 DeepSeek preset（flash / v4 / reasoner）与用户 profile 合并暴露给 `/model`。
- **approval_mode**：`ask`（默认）/ `auto`，作为审批模式的持久化默认值。
- 写入用 `tomli-w`，读取用标准库 `tomllib`；空值不落盘，TOML 解析错误转为 `ConfigError`。

## 审批模式（auto / ask）

`ModeApprovalPort`（`cli/approval.py`）包装任意 `ApprovalPort`：

- `ask`（默认）：透传内层端口，行为与从前一致
- `auto`：短路返回 `approved=True`，不产生任何交互

关键性质：包装发生在 `ApprovalPort` 层，`DefaultPermissionPolicy` 的 DENY 决策（越界路径、敏感资源）不经过审批端口，**auto 模式下依然拒绝**——放开的是「询问」，不是「策略」。

切换入口：

- TUI：`Shift+Tab` 循环切换（通过 `PromptInput` 子类的 binding 拦截，避免被 Screen 的焦点移动 binding 消费）；`/permissions [ask|auto]`
- CLI：`/permissions [ask|auto]`
- 配置文件 `approval_mode` 提供启动默认值

状态栏实时显示当前模式。`AgentRuntime.set_approval_mode()` 同步更新 `AppConfig` 与包装端口。

## 运行时模型切换

`AgentRuntime.switch_model(name)`：

- `name` 命中 profile（内置或配置声明）→ `apply_profile` 更新 model/base_url/api_key；endpoint 变化时重建 `RetryingProvider(OpenAICompatibleProvider(...))` 与 Compactor
- `name` 是裸模型名 → 只改 `config.model`，沿用当前 endpoint
- 两者都触发 `_rebind_session()` 重建 RunSpec（model 是 per-request 字段，loop 无感知）

测试注入的外部 Provider（`FakeProvider`）不会被重建——`_external_provider` 标志保护注入实例。

`/model` 无参数列出全部候选并标记当前项；`/model <名称>` 即时切换，状态栏随之更新。

## TUI 视觉优化

对标 Claude Code 的信息密度与轮次节奏：

- **轮次分隔**：每条用户消息上方渲染细线 `────` + `❯ ` 前缀（加粗青色），多轮对话块状可辨
- **工具单行摘要**：`⏺ write_file(path=out.txt) ✓`——参数压缩为最多 2 个 `key=value`（优先 path/command/pattern 等关键键，值截断 48 字符），完整 JSON 不再刷屏；失败/拒绝仍展开内容块
- **状态栏四段**：` ● 状态 · 模型 · 审批模式 · 工作目录名`，模型与模式切换后即时刷新
- **输入框增高**：`padding: 1 2` 提升触达面积，placeholder 提示 Shift+Tab 快捷键
- **间距节奏**：工具行之间零空行（靠 `⏺` 符号与缩进自成一列），轮次间由分隔线 + 空行承担呼吸感

## 入口变化

`main.py` 改为 argparse：`code-agent [--cli] [--workspace DIR] [--model NAME] [--api-key KEY] [--base-url URL]`。TUI 与 CLI 入口均接受 `(workspace, overrides)`，共享 `load_config_or_wizard`。

## 测试

- `test_config.py`：四层优先级、profiles 解析与合并、approval_mode 校验、配置文件权限与空值过滤、缺失 Key 报错
- `test_approval_mode.py`：ModeApprovalPort 三态（auto 短路 / ask 透传 / 运行时切换）、switch_model 三路径（同 endpoint、裸模型名、跨 endpoint 重建）、runtime 审批模式往返
- `test_tui.py` 增补：Shift+Tab 切 auto 后写文件不再弹审批、`/model` 列表与切换、`/permissions` 命令、用户消息分隔线、工具行紧凑摘要（JSON 不泄漏）
