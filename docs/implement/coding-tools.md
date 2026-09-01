# Coding Tools 实现

## 目标

`code-agent-cli` 的 `coding_tools/` 提供 12 个内置编程工具（原 8 个文件/Git/命令工具 + 4 个后台进程工具）。所有工具实现 core 的 `Tool` 协议，经 `ToolRegistry` 注册后由 `ToolExecutor` 统一执行——参数校验、权限审批、超时和取消都不在工具内重复实现。

## 工具清单

| 工具 | 作用 | 权限 |
|---|---|---|
| `list_files` | 列出目录内容，跳过 `.git`、`node_modules` 等忽略目录 | 只读，自动允许 |
| `search_text` | 正则搜索工作区文件内容，输出 `file:line: text` | 只读，自动允许 |
| `read_file` | 分页读取文本文件，输出带行号内容 | 只读，自动允许 |
| `replace_in_file` | 精确替换：要求旧文本恰好出现一次，否则报错 | 写入，需审批 |
| `write_file` | 创建或覆盖文件，支持嵌套目录 | 写入，需审批 |
| `run_command` | argv 方式执行命令（无 shell），合并 stdout/stderr | 执行，需审批 |
| `git_status` | 工作区状态 | 只读 |
| `git_diff` | 工作区差异 | 只读 |

## 工作区安全

`workspace.py` 统一处理路径：

- `resolve_workspace_path` 把相对路径拼接到工作区并 `resolve()`，拒绝逃逸出工作区的路径（`../`、绝对路径指向外部）
- `is_sensitive` 识别 `.env`、`id_rsa`、`credentials.json` 等敏感文件名，标记后由权限策略拒绝
- `ensure_text_file` 拒绝超过 2MB 或二进制（含 NUL 字节）的文件

命令工具不直接执行普通 shell 字符串，只接受 `argv` 数组；子进程环境变量使用 allowlist（`PATH`、`HOME` 等），不继承 API Key；独立进程组使超时、取消或 stop 能终止整个进程树。针对模型偶发输出的双重序列化形式（如 `"[\"npm\",\"install\"]"`），工具仅在其能严格解析为非空字符串数组时自动规范化；普通命令字符串仍拒绝并返回带字段路径、期望类型和实际类型的诊断。

`run_command` 用于会结束的安装、构建和测试任务；默认 30 秒，调用参数 `timeout_seconds` 可提高至 600 秒，`cwd` 可指定工作区内子目录。长期服务必须通过 `start_process` 启动，其 stdout/stderr 写入 `.code-agent/processes/<id>.log`，并由其余三个进程工具管理。进程注册表绑定当前 Agent 进程，重启 Agent 后旧 process ID 不再可管理，但操作系统进程与日志不会被自动删除。

文件写入统一采用"临时文件 + `fsync` + `os.replace`"的原子写模式，写入失败不会留下半成品文件。

## 与工具层的协作

每个工具只实现四件事：

1. `spec`：封闭 JSON Schema（由 ToolExecutor 预编译校验）
2. `resolve_targets`：把参数解析成具体的 `ToolTarget`（路径/命令 + 效果）
3. `execute`：执行实际操作，通过 `output` 增量写入有界结果
4. `abort`：释放资源（`run_command` 用它终止进程组）

权限、审批、并发互斥（文件写入共享 `file-write` 键）、输出截断全部由 core 工具层处理。

## 测试

功能测试覆盖每个工具的正常路径和常见错误：

- `read_file`：行号输出、分页、工作区逃逸拒绝
- `list_files`：忽略目录过滤
- `search_text`：正则匹配、非法正则报错
- `replace_in_file`：唯一匹配替换、多重匹配拒绝
- `write_file`：嵌套目录创建
- `run_command`：成功执行、非零退出返回 error、JSON 编码 argv 自动修复、类型诊断、cwd、单次超时覆盖
- 后台进程：start → status → read output → stop 完整生命周期与日志忽略规则
- `git_status` / `git_diff`：真实 git 仓库中的状态和差异
- 敏感文件识别
- 全部 12 个工具可注册进同一 Registry
