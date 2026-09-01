Code-Agent —— 自研终端编程智能体（推免考核项目）

【Git 仓库地址】
https://github.com/TianYu-2005/Code-Agent

【如何运行】
环境要求：Python 3.12+ 与 uv 包管理器。
1. git clone 本仓库后，在仓库目录执行 uv sync --all-packages 安装依赖；
2. 运行 code-agent 命令（uv run code-agent），首次启动自动进入交互式引导，填入模型 API Key 后保存至 ~/.code-agent/config.toml（权限 600），此后零配置启动；也可用环境变量 CODE_AGENT_API_KEY 代替；默认直连 DeepSeek，兼容任意 OpenAI 兼容服务；
3. 在任意项目目录启动即以该目录为工作区，进入 inline TUI 界面，--cli 可切换经典命令行，/help 查看命令。
无需真实 Key 即可验证工程质量：uv run pytest 运行 172 个离线测试。

【特色功能说明】
1. 完全自研：不使用任何 agent 框架或 SDK，仅依赖模型原生 tool calling；ReAct 循环、上下文管理、工具定义与执行、模型输出解析、循环终止与错误处理均自行实现。
2. 三层架构：llm 层（Provider 协议、OpenAI 兼容适配、重试）→ core 层（Agent Loop、工具协议、树状会话）→ cli 层（TUI/CLI 双外壳）。依赖单向，核心层只发事件、不感知终端细节。
3. 12 个内置工具：文件读/写/精确编辑、文本搜索、受限命令执行（超时与参数白名单）、后台进程生命周期管理、Git 状态与 diff。
4. 树状会话：对话历史是树而非单链，/rewind 回到任意历史节点分叉重试，/fork 在分支间切换，原分支完整保留，位置持久化。
5. 自动上下文压缩：长对话接近 token 预算时自动将旧消息摘要为总结，保留最近完整轮次；摘要绑定分支，rewind 后不误用；失败自动降级为截断。
6. 权限体系：写操作默认逐次人工审批（y 允许 / a 会话内允许 / n 拒绝），Shift+Tab 一键切换 auto 模式；路径越界、敏感资源由策略层直接拒绝，与审批模式解耦，auto 下依然生效。
7. 运行时切换模型：/model 在内置 preset 与配置文件自定义 profile（跨 endpoint）间即时切换，无需重启。
8. inline TUI：流式输出、工具调用单行摘要、审批面板、状态栏（模型/审批模式/工作目录），随终端尺寸自适应；全链路 Ctrl+C 可取消。
9. 工程质量：mypy 严格模式与 ruff 全绿，三层均有对应测试，FakeProvider 支持离线回放模型交互。

【其它说明】
仓库完整保留开发提交历史，开发过程可经 commit 记录追溯。架构设计见 docs/design.md，各模块实现记录见 docs/implement/。API Key 仅通过环境变量或未入库的本地配置文件提供，仓库与视频中均不含凭据。
