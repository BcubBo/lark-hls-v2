# hermes-lark-streaming-v2 技术架构分析

## 1. 整体架构概览

HLS v2 是一个 Hermes Agent 插件，通过**运行时 monkey patching** 将飞书 CardKit 流式卡片能力注入 Hermes 的消息处理管线。架构分为 5 个核心子系统：

```
interceptors/     ──── 6 个拦截器，hook 进 Hermes 内部方法
  ├── __init__.py          patch 注册中心 + 共享状态
  ├── gateway.py           GatewayRunner 4 个方法包装
  ├── adapter.py           FeishuAdapter 7 个方法包装
  ├── callbacks.py         AIAgent 5 个流式回调包装
  ├── hooks.py             10 个注入点函数（桥接到 controller）
  └── hermes_compat.py     Hermes 内部接口隔离层

lifecycle/        ──── 会话生命周期管理
  ├── manager.py           SessionManager: 线程安全 CRUD + TTL 清理
  ├── interrupt.py         InterruptResolver: 中断链 + 续接映射
  └── reactivation.py      ContinuationReactivation: 流式关闭后续接

state/            ──── 状态机 + 数据模型
  ├── session.py           CardSession: 单消息卡片会话状态
  ├── phase.py             CardPhase: 8 阶段状态机
  ├── linear.py            UnifiedLinearState: 单面板推理+工具追踪
  ├── text.py              TextState: 增量文本累积
  └── tooluse.py           ToolUseTracker: 工具调用追踪与脱敏

feishu/           ──── 飞书 API 层
  ├── client.py            FeishuClient: lark-oapi SDK 封装
  └── guard.py             UnavailableGuard: 消息不可用检测

flush/            ──── 节流控制
  └── controller.py        FlushController: 节流 flush 调度
```

---

## 2. interceptors/ — 拦截器层

### 2.1 `__init__.py` — 注册中心与共享状态

**职责**：管理所有 monkey patch 的生命周期，维护跨模块共享状态。

**共享状态**（全局单例）：

| 变量 | 类型 | 用途 |
|------|------|------|
| `_thread_local_ctx` | `threading.local` | 向线程池 worker 传播消息上下文 |
| `_msg_ctx` | `ContextVar[dict]` | 当前消息上下文（event_message_id、card_sent 等） |
| `_started_msg_ids` | `set[str]` | 已开始处理的消息 ID（中断检测用） |
| `_gateway_cards` | `dict[str, dict]` | gateway 消息卡片注册表（card_msg_id → 元数据） |
| `_patch_status` | `dict` | 各 patch 状态汇总（doctor 命令用） |
| `_patched_feishu_classes` | `set[int]` | 已 patch 的 FeishuAdapter 类 ID（防重复 patch） |

**核心函数**：

- **`apply_patches()`**：唯一公共入口。按顺序 patch 6 个目标：
  1. `GatewayRunner`（4 方法）— 立即或延迟轮询（2s 间隔，60s 超时）
  2. `agent.conversation_loop` 模块（run_conversation 函数）
  3. `AIAgent`（直接 patch，兼容 Hermes <v0.10）
  4. `cron.scheduler._deliver_result`
  5. `FeishuAdapter`（7 方法）
  6. `platform_registry.create_adapter`（v1.6.0 钩子）

- **`_apply_gateway_runner_patches()`**：逐方法 patch GatewayRunner，任一方法缺失不影响其他。

- **`_apply_feishu_adapter_patches()`**：patch FeishuAdapter 的 send/edit/reaction/clarify/card_action 方法。用 `id(cls)` 追踪已 patch 的类，防止重复。

- **`_apply_create_adapter_hook()`**（v1.6.0）：hook `platform_registry.create_adapter`，在每个 FeishuAdapter 实例创建时自动 patch 其类。解决了 hermes v0.17.0+ 延迟加载平台导致的"替身/真身"问题。

**延迟 patch 机制**：当 `gateway.run` 模块尚未加载时，启动守护线程每 2s 轮询，60s 超时后放弃。这是必要的，因为插件注册时 `GatewayRunner` 类可能还不存在。

### 2.2 `gateway.py` — GatewayRunner 方法包装

**职责**：拦截 Hermes 消息处理主链路的 4 个方法。

| 包装函数 | 目标方法 | 注入时机 |
|----------|----------|----------|
| `_wrap_handle_message` | `_handle_message` | 消息入口：注入 NORMALIZE hook + /aowen 中断提示 |
| `_wrap_handle_message_with_agent` | `_handle_message_with_agent` | Agent 启动：注入 START hook，返回时检测 ABORT/INTERRUPT |
| `_wrap_run_agent` | `_run_agent` | Agent 运行：传播 event_message_id，注入 COMPLETE hook |
| `_wrap_run_background_task` | `_run_background_task` | /background 任务：注入 START/COMPLETE hook |

**`_wrap_handle_message`**：
- 在消息处理前调用 `on_feishu_normalize`（修正飞书引用消息的虚假 thread_id）
- 检测 /aowen 命令在 agent 运行中被调用，发送中断提示卡片

**`_wrap_handle_message_with_agent`**：
- 入口：注册消息到 `_started_msg_ids`，调用 `on_message_started`，设置 `_msg_ctx`
- 出口（3 种路径）：
  - `result is not None` 且 `card_sent=True` → 抑制 gateway 文本回复（返回 None）
  - `result is None` 且 `card_sent=True` → 检测中断（验证其他消息有活跃 session）
  - `result is None` 且 `card_sent=False` → 真正的 abort（调用 `on_message_aborted`）
- **v1.3.4 修复**：异常路径和正常路径都调用 `_hls_cleanup_ctx()` 清理 `_msg_ctx`，防止 stale event_message_id 导致下一条消息卡片不出现

**`_wrap_run_agent`**：
- 支持 `_interrupt_depth > 0`（子 agent 递归调用）：创建新 `_msg_ctx`，保存父上下文，触发子消息的 START hook
- 异常时恢复父上下文（防止 "wrong card gets completion" bug）
- 返回时：先触发子 agent 的 COMPLETE hook，再触发父 agent 的 ABORTED COMPLETE
- 从 `_agent_ref` 提取 cache tokens / reasoning tokens / cost 信息传递给 COMPLETE hook

**`_wrap_run_background_task`**：
- 为 /background 任务创建 `_msg_ctx`（event_message_id = task_id）
- 临时替换 adapter.send 为拦截版本（card_sent 后抑制文本重复）
- finally 中恢复 adapter.send 并清理上下文

### 2.3 `adapter.py` — FeishuAdapter 方法包装

**职责**：拦截飞书适配器的发送/编辑/反应/追问操作。

**消息分类**（`_classify_gateway_message`）：

| 类别 | 关键词示例 |
|------|-----------|
| `auth` | "pairing code"、"配对码" |
| `error` | "❌"、"⚠️"、"error"、"failed" |
| `session` | "Session"、"🔄"、"compress" |
| `slash` | "/help"、"/status"、"/model" 等 |
| `system` | 其他 |

**`_wrap_feishu_adapter_send`**：
- **on-demand repatch**：如果当前 adapter 类未被 patch，立即触发 patch（v1.6.0 鸡蛋问题的辅助方案）
- **EphemeralReply 透传**：不拦截临时回复
- **Agent 路径**：`_msg_ctx` 存在且 `card_sent=True` → 返回 `SendResult(success=True)` 抑制文本
- **/stop 检测**：短消息 + "⚡" + "已停止" → 中断活跃 streaming session 并抑制 gateway 卡片
- **Gateway 卡片路径**：分类消息 → `ctrl._do_gateway_deliver()` → 注册到 `_gateway_cards`
- **Fallback**：原始纯文本发送

**`_wrap_feishu_adapter_edit`**：
- 检查 `_gateway_cards` 注册表，匹配则更新 gateway 卡片（`ctrl._do_gateway_card_update()`）
- 不匹配则 fallback 到原始 edit_message

**`_wrap_feishu_adapter_add_reaction / delete_reaction`**：
- 将飞书 reaction emoji 映射为 gateway 卡片状态指示器
- `_REACTION_STATUS_MAP`: 👀→Reading, 👍→Done, 🤔→Thinking, ⏳→Processing 等
- add_reaction → 更新卡片状态，delete_reaction → 清除卡片状态

**`_wrap_feishu_adapter_send_clarify`**：
- 追问卡片完整流程：
  1. Flush 活跃 streaming session 的脏数据
  2. 构建 clarify 卡片（`build_clarify_card`）
  3. 存储 choices/question/timestamp（TTL 30 分钟）
  4. 发送卡片（reply_card 或 send_card_to_chat）
  5. 注册到 gateway_cards
  6. 调用 `mark_awaiting_text`

**`_wrap_handle_card_action_event`**：
- 拦截 clarify 卡片的回调事件
- 4 种动作类型：`select`（下拉选择）、`input_submit`（文本输入）、`button_submit`（按钮提交）、`retry_submit`（重试）
- 三态流程：选择/输入 → 软锁定（submitted card + retry 按钮） → 硬锁定（confirmed card）
- 异步调度 `resolve_gateway_clarify`（通过 `safe_schedule_threadsafe`）
- 授权检查：调用 adapter 的 `_is_interactive_operator_authorized`

### 2.4 `callbacks.py` — 流式回调包装

**职责**：替换 AIAgent 的 5 个流式回调，桥接到 HLS 的 hooks。

| 回调 | 包装函数 | 注入的 hook |
|------|----------|------------|
| `stream_delta_callback` | `_answer_wrapper` | `on_answer_delta` |
| `interim_assistant_callback` | `_thinking_wrapper` | `on_thinking_delta` |
| `tool_progress_callback` | `_tool_wrapper` | `on_tool_updated` |
| `reasoning_callback` | `_reasoning_wrapper` | `on_reasoning_delta` |
| `background_review_callback` | `_bg_wrapper` | `on_background_review_message` |

**`_maybe_wrap_callbacks`**：
- 在 `run_conversation` 调用前执行
- 检查 `_msg_ctx` 是否有 event_message_id（非飞书上下文则跳过）
- 用 `_hls_wrapper` 属性标记已包装的回调（防重复包装）
- **去重机制**：`_stream_consumed_len` 跟踪 answer 文本已消费长度，thinking 回调的文本如果完全被 answer 消费则跳过（P3-02 修复）
- **late-arriving reasoning_callback**：如果回调在 wrapper 已安装后才到达，单独包装
- 存储 agent 引用到 `_msg_ctx["_agent_ref"]`（用于提取 cache tokens）

### 2.5 `hooks.py` — 注入点函数

**职责**：桥接拦截器和 controller，提供统一的异常处理。

**`_safe_hook` 装饰器**：
- 统一检查 `ctrl.enabled`
- 捕获所有异常（不向上抛）
- 可配置 default_return 和 log_level

**10 个注入点**：

| # | 函数 | 触发时机 | 调用的 controller 方法 |
|---|------|----------|----------------------|
| 0 | `on_feishu_normalize` | _handle_message 入口 | 修正虚假 thread_id |
| 1 | `on_message_started` | agent 启动 | `ctrl.on_message_started()` |
| 2 | `on_message_completed` | agent 返回 | `ctrl.on_completed()` → 返回 bool |
| 3 | `on_tool_updated` | 工具事件 | `ctrl.on_tool_update()` → 返回 bool |
| 4 | `on_answer_delta` | answer 文本增量 | `ctrl.on_answer()` → 返回 bool |
| 5 | `on_thinking_delta` | thinking 文本增量 | `ctrl.on_thinking()` → 返回 bool |
| 6 | `on_reasoning_delta` | reasoning 文本增量 | `ctrl.on_reasoning()` → 返回 bool |
| 7 | `on_background_review_message` | 后台审核 | `ctrl.defer_background_review()` → 返回 bool |
| 8 | `on_message_aborted` | 消息中止 | `ctrl.on_aborted()` |
| 9 | `on_message_interrupted` | 消息中断 | `ctrl.on_interrupted()` |
| 10 | `on_cron_deliver` | cron 推送 | `ctrl.on_cron_deliver_async()` |

### 2.6 `hermes_compat.py` — Hermes 接口隔离层

**职责**：隔离所有 Hermes 内部模块访问，提供版本检测和模块解析。

**HermesCompat 类**：
- **版本检测**：`importlib.metadata` → `hermes_cli.__version__` → "unknown"
- **模块解析**：
  - `GatewayRunner` ← `gateway.run`
  - `AIAgent` ← `run_agent`
  - `FeishuAdapter` ← 3 个路径优先级：`hermes_plugins.feishu_platform.adapter`（真身）> `plugins.platforms.feishu.adapter`（替身）> `gateway.platforms.feishu`（legacy）
  - `cron.scheduler` ← 尝试多个模块名
  - `conversation_loop` ← 3 策略：sys.modules 缓存 → 锚点发现（从 gateway.run 反推）→ 标准 import

**conversation_loop 解析策略**：
1. `sys.modules.get("agent.conversation_loop")`
2. 锚点发现：从 `gateway.run` 或 `run_agent` 的 `__file__` 推导出 `agent/conversation_loop.py`，用 `importlib.util.spec_from_file_location` 手动加载
3. 标准 `from agent.conversation_loop import run_conversation`

---

## 3. lifecycle/ — 生命周期管理

### 3.1 `manager.py` — SessionManager

**职责**：线程安全的会话 CRUD、TTL 清理、重量级数据释放。

**线程安全**：
- 所有 `_sessions` 读写通过 `_sessions_lock`（RLock）
- `_cleanup` 按固定顺序获取锁：`_sessions_lock` → `_interrupt_map_lock` → `_continuation_map_lock`（防死锁）

**核心 API**：

| 方法 | 用途 |
|------|------|
| `_sess_get(message_id)` | 按 message_id 查找 session |
| `_sess_put(key, session)` | 存储 session |
| `_sess_pop(key)` | 移除并返回 session |
| `_sess_items_snapshot()` | 线程安全快照 |
| `_sess_values_snapshot()` | 所有 session 快照 |
| `_sess_active_count()` | 活跃 session 计数 |
| `_prune_stale_sessions()` | 清理超过 TTL 的终态 session（不清理活跃 session） |
| `_cleanup(message_id)` | 从所有 map 中移除 session（sessions + interrupt + continuation） |
| `_release_session_data(session)` | 卡片密封后释放 unified_state/text/tool_use/footer |

**TTL 清理策略**：
- 默认 TTL：1800 秒（30 分钟）
- 只清理终态（COMPLETED/ABORTED/CREATION_FAILED/TERMINATED）且超 TTL 的 session
- 活跃 session 超 TTL 仅告警不清理（避免丢失 AI 回调数据）

### 3.2 `interrupt.py` — InterruptResolver

**职责**：管理中断链重定向和续接映射。

**两个独立映射**：

1. **`_interrupt_map`**：`old_message_id → new_message_id`
   - `register_interrupt()`：记录重定向 + 链式扩展（A→B 已存在时 B→C 会更新 A→C）
   - LRU 驱逐：超过 200 条时删除最早的
   - `pop_interrupt_redirect()`：一次性消费（on_completed 调用）

2. **`_continuation_map`**：`old_message_id → continuation_message_id`
   - 当 streaming card 收到 300309 错误但 session 仍活跃时，创建续接卡片
   - `_resolve_continuation_id()`：查询是否已续接
   - `_register_continuation()`：记录续接映射
   - `_pop_continuation_id()`：一次性消费

**线程安全**：每个映射独立 Lock（非 RLock），与 `_sessions_lock` 分离避免死锁。

### 3.3 `reactivation.py` — ContinuationReactivation

**职责**：streaming card 被关闭（300309）后续接创建新卡片。

**触发条件**（全部满足才触发）：
1. 原始 session 存在且未终态
2. `_streaming_closed = True`（卡片流式模式已关闭）
3. `_is_continuation = False`（不是续接 session 本身）
4. `_continuation_reactivation_count < 1`（最多续接 1 次）

**续接流程**：
1. 生成新 message_id：`<anchor_id>-cont-<seq>`
2. 创建新 CardSession（`_is_continuation=True`, `linear=True`, 预创建 `UnifiedLinearState`）
3. 注册到 `_interrupt_resolver._continuation_map`
4. 异步触发新卡片创建（`_do_create_linear_card`）
5. 将旧 session 移至 COMPLETING 状态（密封旧卡片）

**竞态保护**：
- `_continuation_reactivation_count >= 1` 防递归
- 新 session 不覆盖旧 session 的 anchor_id 别名
- 终态 session 不被续接（迟到的 token 丢弃）

---

## 4. state/ — 状态机与数据模型

### 4.1 `phase.py` — CardPhase 状态机

**8 个阶段**：

```
IDLE → CREATING → STREAMING → COMPLETING → COMPLETED (终态)
  ↓         ↓          ↓           ↓
ABORTED   CREATION_FAILED  TERMINATED  ABORTED/TERMINATED
(终态)     (终态)          (终态)       (终态)
```

**合法转换**（`PHASE_TRANSITIONS`）：

| 当前阶段 | 可转换到 |
|----------|---------|
| IDLE | CREATING, ABORTED, TERMINATED |
| CREATING | STREAMING, CREATION_FAILED, TERMINATED |
| STREAMING | COMPLETING, ABORTED, TERMINATED |
| COMPLETING | COMPLETED, CREATION_FAILED, ABORTED, TERMINATED |
| COMPLETED | — |
| CREATION_FAILED | — |
| ABORTED | — |
| TERMINATED | — |

**TerminalReason**：NORMAL, ERROR, ABORT, UNAVAILABLE, CREATION_FAILED

**设计特点**：
- 非法转换不抛异常，仅记录警告
- 幂等转换（同阶段→同阶段）返回 True
- 进入终态时自动调用 `enter_terminal()` 记录原因和来源
- `create_epoch` 在进入 CREATING 时快照，用于过期创建检测

### 4.2 `session.py` — CardSession

**职责**：单消息卡片会话的完整状态容器。

**关键字段**（`__slots__` 声明）：

| 类别 | 字段 | 说明 |
|------|------|------|
| 标识 | `message_id`, `anchor_id`, `chat_id` | 消息标识 |
| 卡片 | `card_msg_id`, `card_id`, `card_trace_id` | CardKit 卡片标识 |
| 阶段 | `state`, `create_epoch`, `terminal_reason` | 状态机状态 |
| 子状态 | `text`, `tool_use`, `flush`, `guard` | 组合的子状态对象 |
| 面板 | `linear`, `unified_state` | 线性模式状态 |
| 元数据 | `footer`, `sequence`, `existing_elements` | 卡片渲染元数据 |
| 时序 | `created_at`, `card_created_at`, `_first_answer_time` | 时间戳 |
| 标志 | `_streaming_closed`, `_is_continuation`, `_was_aborted` | 内部标志 |

**核心方法**：
- `transition(to, source, reason)`：状态机转换，拒绝非法转换
- `should_proceed(source)`：组合 state + guard 检查
- `is_stale_create(epoch)`：检查 epoch 是否过期
- `enter_terminal(reason, source)`：进入终态（仅首次记录原因）
- `_on_guard_terminate()`：UnavailableGuard 回调（消息被删除/撤回）

### 4.3 `linear.py` — UnifiedLinearState

**职责**：单面板推理+工具追踪的统一状态。

**ReasoningRound**：每轮 AI 推理的数据（index, text, elapsed_ms, start_time, finalized）

**UnifiedLinearState 核心逻辑**：
- `on_reasoning_delta(text)`：增量推理文本，自动去重（前缀匹配检测 post-stream 重复）
- `on_answer_delta(text)`：触发当前推理轮次的 finalize，累积答案文本
- `on_tool_event(is_new_tool)`：触发当前推理轮次的 finalize，记录工具事件时间线
- `panel_visible`：首次收到推理或工具事件时设为 True（触发面板创建）
- `panel_events`：按时间顺序记录 `("reasoning"|"tool", index)` 事件

**脏标记系统**：
- `panel_dirty`：面板数据需刷新
- `answer_dirty`：答案数据需刷新
- `has_dirty`：任一脏标记为 True

### 4.4 `text.py` — TextState + Reasoning 解析

**职责**：增量文本累积 + reasoning/thinking 标签解析。

**推理文本解析**：
- `split_reasoning_text(text)`：将文本拆分为 reasoning + answer
- 支持格式：`Reasoning:\n` 前缀、`<thinking>` / `<thought>` / `<antthinking>` 标签
- `extract_thinking_content(text)`：状态机遍历标签提取推理内容
- `strip_reasoning_tags(text)`：移除所有推理标签

**TextState**：
- `completed_text`：已交付的文本
- `accumulated`：累积中的增量文本
- `display_text`：优先显示 accumulated，fallback 到 completed_text

### 4.5 `tooluse.py` — ToolUseTracker

**职责**：工具调用追踪、脱敏、渲染。

**ToolStep 数据类**：name, status(running/success/error), detail, output, error, result_block, error_block, started_at, elapsed_ms

**安全脱敏**：
- `redact_inline_secrets(value)`：3 层正则脱敏
  - `_INLINE_ASSIGNMENT_RE`：`key=secret` 模式（匹配 token/secret/password/api_key 等）
  - `_AUTH_HEADER_RE`：`Authorization: Bearer xxx` 模式
  - `_SECRET_FLAG_RE`：`--flag secret` 模式

**sanitizer 类型**：
- `command`：脱敏 secrets + 路径 basename 化
- `path`：仅 basename
- `search`：去引号
- `url`：去 "from " 前缀 + 去引号

**工具描述符**（`_TOOL_DESCRIPTORS`）：12 种工具类型的 icon/title/sanitizer/no_result 配置。

**ToolUseTracker**：
- `record_start(name, detail)`：记录工具开始
- `record_end(name, error, output)`：按名字反向匹配最近的 running 步骤结束
- `build_display_steps()`：构建卡片渲染用的步骤列表

---

## 5. feishu/ — 飞书 API 层

### 5.1 `client.py` — FeishuClient

**职责**：封装所有飞书 Open API 调用。

**API 方法**：

| 方法 | 功能 | CardKit API |
|------|------|-------------|
| `send_card_to_chat` | 发送独立卡片 | IM v1 create |
| `reply_card` | 回复卡片消息 | IM v1 reply |
| `reply_text` | 回复纯文本 | IM v1 reply |
| `reply_card_by_id` | 通过 card_id 回复 | IM v1 reply |
| `update_card` | PATCH 更新已发卡片 | IM v1 patch |
| `cardkit_create` | 创建 CardKit 实体 | CardKit v1 card create |
| `cardkit_stream_element` | 流式更新 element（打字机效果） | CardKit v1 card_element content |
| `cardkit_update` | 全量更新 CardKit 卡片 | CardKit v1 card update |
| `cardkit_batch_update` | 局部更新（增删改组件） | CardKit v1 card batch_update |
| `cardkit_close_streaming` | 关闭流式模式 + summary | CardKit v1 card settings |
| `cardkit_update_summary` | 更新 summary（不关闭流式） | CardKit v1 card settings |
| `upload_image` | 下载远程图片上传飞书 | IM v1 image create |
| `upload_local_image` | 上传本地图片 | IM v1 image create |

**重试机制**：
- `_retry_transient()`：通用瞬态错误重试（3 次，指数退避 0.1s/0.3s/0.6s）
- 覆盖：CardKit 瞬态错误码（2200/1663/300000/99991400）+ httpx 网络异常 + ObtainAccessTokenException
- `cardkit_stream_element` 额外重试 element_not_found（300313/300314/300315+not find elementID）

**错误码常量**：

| 常量 | 值 | 含义 |
|------|-----|------|
| `CARDKIT_CONTENT_FAILED` | 230099 | 卡片内容创建失败（通用） |
| `CARDKIT_ELEMENT_LIMIT` | 11310 | 子码：元素超限 |
| `CARDKIT_ELEMENT_LIMIT_DIRECT` | 300305 | 直报码：元素超限 |
| `CARDKIT_SCHEMA_ERROR` | 300315 | Schema 非法属性 |
| `CARDKIT_STREAMING_CLOSED` | 300309 | 流式模式已关闭 |
| `CARDKIT_SEQUENCE_CONFLICT` | 300317 | sequence 冲突 |
| `CARDKIT_ELEMENT_NOT_FOUND` | 300313 | 元素不存在 |
| `MSG_NOT_FOUND` | 1000023 | 消息不存在/已删除 |

**安全措施**：
- `_sanitize_message()`：从错误消息中移除 tenant_access_token / app_secret / Bearer token
- `FeishuAPIError`：携带 code + log_id + 子错误码提取方法

### 5.2 `guard.py` — UnavailableGuard

**职责**：检测消息被删除/撤回后终止 pipeline。

**不可用缓存**：
- `_unavailable_cache`：`message_id → {code, operation, at}`（TTL 30 分钟）
- `_PRUNE_THRESHOLD = 50`：缓存超过 50 条时触发清理

**终端消息码**：`{231003, 1000023, 230011}`（deleted/recalled/not found）

**UnavailableGuard 类**：
- `should_skip(source)`：检查 reply_to_message_id 是否已知不可用
- `terminate(source, err)`：尝试从错误码或缓存中判断是否终端码，是则终止 pipeline
- `on_terminate` 回调：设置 session 状态为 TERMINATED，signal `_card_ready`

---

## 6. flush/ — 节流控制

### 6.1 `controller.py` — FlushController

**职责**：决定何时执行 API 刷新回调，不包含飞书业务逻辑。

**核心参数**：
- `CARDKIT_MS = 0.080`：80ms 刷新间隔（≥ 飞书默认 print_frequency_ms 70ms，留余量给 API 往返）
- `LONG_GAP_MS = 1.000`：超过此间隔视为长时间空闲
- `BATCH_AFTER_GAP_MS = 0.100`：长时间空闲后等待 100ms 再 flush

**状态**：
- `_flush_in_progress`：正在进行的 flush（避免并发）
- `_needs_reflush`：flush 期间有新数据到达
- `_completed`：标记完成，拒绝新更新
- `_card_message_ready`：卡片消息已就绪（初始化时间戳）

**调度逻辑**（`_schedule_update_on_loop`）：
1. `elapsed >= throttle_ms` 且 `elapsed > LONG_GAP_MS` → 延迟 100ms（等待更多内容）
2. `elapsed >= throttle_ms` 且 `elapsed <= LONG_GAP_MS` → 立即 flush
3. `elapsed < throttle_ms` → 延迟到窗口边界

**`flush_now(do_flush)`**：立即执行 flush（取消 pending timer），等待完成。

**`wait_for_flush()`**：等待进行中的 flush 完成（通过 Future 链）。

**线程安全**：通过 `loop.call_soon_threadsafe` 从 worker 线程调度到事件循环。

---

## 7. plugin/__init__.py — 插件注册

**职责**：插件注册/注销入口。

**注册流程**（`register(ctx)`）：
1. `_ensure_streaming_config()`：确保 config.yaml 包含 `hermes_lark_streaming_v2` 配置
2. `apply_patches()`：应用所有 runtime monkey patches
3. 预热 FeishuClient（异步任务）
4. 注册 `/aowen` 命令 hook（`pre_gateway_dispatch`）

**配置管理**：
- `_backup_config()`：首次注册时备份 config.yaml（带时间戳，仅备份一次）
- `_ensure_streaming_config()`：注入默认配置 + 添加到 plugins.enabled
- `_cleanup_config()`：注销时移除配置和插件引用

**默认配置项**：panel_expanded, streaming_panel_expanded, print_strategy, print_step, flush_interval_ms, card_ttl_sec, max_tool_steps, max_reasoning_rounds, footer

---

## 8. 关键设计模式与修复

### 8.1 Patching 策略演进

| 版本 | 策略 | 问题 |
|------|------|------|
| v1.3.0 | 模块级 patch + 2s+10s 定时器 repatch | 赌时窗，不可靠 |
| v1.4.0 | on-demand repatch（_wrap_send 内检查） | 鸡蛋问题：wrapper 只装在已 patch 的类上 |
| v1.6.0 | hook `platform_registry.create_adapter` | 根本解决：所有 adapter 实例创建时自动 patch |

### 8.2 上下文传播

```
_handle_message_with_agent → _msg_ctx.set(ctx) → _thread_local_ctx.data = ctx
     ↓
_wrap_run_agent → 更新 ctx["event_message_id"] → _thread_local_ctx.data = ctx
     ↓
_wrap_run_conversation → _maybe_wrap_callbacks(agent) → 从 _msg_ctx 获取 eid
     ↓
callbacks → on_answer_delta / on_thinking_delta / on_tool_updated → controller
```

### 8.3 终态保护

- 4 个终态（COMPLETED, CREATION_FAILED, ABORTED, TERMINATED）均为吸收态
- `is_legal_transition()` 阻止从终态转换
- `enter_terminal()` 仅首次记录原因（first-write-wins）
- `create_epoch` 递增用于检测过期创建

### 8.4 关键 Bug 修复索引

| 版本 | Bug ID | 描述 |
|------|--------|------|
| v1.3.4 | P1 | `_msg_ctx` 异常路径未清理 → 下一消息卡片不出现 |
| v1.3.4 | P1 | `_saved_parent_ctx` 异常路径未恢复 → wrong card gets completion |
| v1.3.2 | P3-02 | thinking/answer 回调重复消费 → 文本重复 |
| v1.3.0 | P1-01 | session 遍历时 RuntimeError → 用 snapshot |
| v1.4.0 | 问题3根因1 | delegate_task 后卡片降级 → ContinuationReactivation |
| v1.3.1 | B3-04 | /stop 响应被当作 gateway 内部消息 → 检测并 abort |
| v1.3.2 | B3-05 | `_schedule_confirm_card` 重复 import asyncio |
| v1.3.2 | B3-06 | counter 默认值 1 → 0（decrement 一致性） |
