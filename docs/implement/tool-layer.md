# 工具层实现

## 目标

工具层在模型输出与本地能力之间提供唯一安全执行边界。模型生成的工具名和 `arguments_json` 均视为不可信数据，任何工具都必须经 `ToolExecutor` 完成验证、授权、限流和执行。

## 模块

| 模块 | 职责 |
|---|---|
| `tools/base.py` | Tool、ToolSpec、ValidatedToolCall、ToolTarget、ToolOutcome 和 ToolResult |
| `tools/schema.py` | 封闭 JSON Schema 编译和参数解析 |
| `tools/registry.py` | 显式注册、来源绑定、Schema 预编译和规范快照 |
| `tools/permissions.py` | allow/ask/deny 策略及审批协议 |
| `tools/concurrency.py` | 全局并发限制和多资源键互斥 |
| `tools/output.py` | 增量有界输出和元数据预算 |
| `tools/executor.py` | 验证、授权、审批、执行、超时、取消和错误归一化 |

## 执行流程

```text
ToolCall(arguments_json)
  -> 查找注册快照
  -> 限制参数大小并解析 JSON
  -> 封闭 JSON Schema 校验
  -> 解析动态 ToolTarget
  -> 合并静态与动态 effects
  -> 构造带调用指纹的 ValidatedToolCall
  -> PermissionPolicy 决策
  -> 必要时通过 ApprovalPort 审批
  -> 获取并发资源锁
  -> timeout / cancellation 包装
  -> Tool.execute + BoundedToolOutput
  -> ToolResult
```

## Schema 与注册

工具 Schema 固定使用 Draft 2020-12 的受限子集：

- 根节点必须是 `type: object`；
- 所有对象必须设置 `additionalProperties: false`；
- 禁止 `$ref`、`$dynamicRef`、`$id` 和高复杂度组合、正则关键字；
- 使用禁止远程 retrieve 的空 Registry；
- 限制 Schema 大小、深度和节点数；
- 限制参数字节数、深度、节点数和容器元素数；
- 拒绝 NaN、Infinity、指数溢出及超长整数等异常值。

`ToolRegistry` 在注册时预编译 Schema，并保存深度冻结的 `ToolSpec` 快照和指纹。工具来源不由工具自身决定，必须由可信组合根通过 `register(tool, origin=...)` 指定。

## 权限模型

`ToolSpec.effects` 是能力上界，动态目标只能增加权限要求，不能降低声明影响。默认策略：

- 来源可信、非敏感且位于工作区内的内置只读工具自动允许；
- 写入、执行、网络或外部来源工具要求审批；
- 未知影响、敏感目标和工作区外目标直接拒绝。

审批响应包含调用指纹。指纹绑定 Session、Run、工具注册快照、参数、目标和最终 effects，不能复用于其他调用。

## 执行与资源控制

首版默认全工具串行。提高并发度后，相同工具、声明并发键或目标资源仍互斥。多锁按固定顺序获取，取消时释放已获取锁；无使用者的锁会从表中回收。

输出通过 `ToolOutputSink` 分块写入，达到预算后只累计原始大小，不继续保存内容。Metadata 在复制前执行深度、元素数和字节预算检查。

审批和工具执行均支持超时与 `CancellationToken`。工具必须实现 `abort()` 清理子进程、请求或连接。Executor 在超时或取消后先调用 `abort()`，再等待执行协程真正终止；无法安全终止时抛出 `ToolTerminationError`，禁止返回虚假的完成状态。

普通工具异常、非法返回值和目标解析异常统一转换为脱敏的 `ToolResult(ERROR)`。

## 测试

离线测试覆盖：

- Schema 引用、方言、开放对象和复杂度限制；
- 非法 JSON、非对象参数、超长整数和非有限数；
- 注册冲突、可信来源覆盖和规范快照；
- 静态 effects 不可降级、外部/敏感目标拒绝；
- 审批指纹、审批超时和取消；
- 工具超时、Token 取消、调用方取消、自取消和中止失败；
- 多资源锁取消释放、锁表回收和同资源串行；
- 非法 ToolOutcome、错误脱敏及大输出截断。
