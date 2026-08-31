# lark-hls-v2 cron 卡片改进设计文档

> 版本：P0 设计 v1.0  
> 日期：2026-08-30  
> 状态：设计中

---

## 1. 问题诊断

当前 `build_cron_card()`（card/special.py L127-151）的问题：

| 问题 | 现状 | 影响 |
|------|------|------|
| Header 永远绿色 | `template="green"` 硬编码 | 成功/失败/警告无法视觉区分 |
| Header 无结构 | 只有 title "⏰ 定时任务" | 看不出哪个 job、什么时候跑的 |
| 内容无折叠 | markdown 全量 dump 到 body | 超长内容撑爆卡片（飞书单卡硬限 200 元素） |
| Footer 信息缺失 | 只有 label + time + job_name | 无 schedule、无耗时、无状态 |

---

## 2. 数据流分析

```
Hermes cron scheduler
  └─ _deliver_result(job, content, adapters, loop)
       └─ feishu_adapter.send(chat_id, content, metadata={job_id: ...})
            └─ _wrap_cron_deliver 拦截
                 └─ ctrl._do_cron_deliver(chat_id, cleaned, job_name=job_name)
                      └─ build_cron_card(content, job_name=job_name)
                           └─ CardKit 2.0 JSON → 飞书 API
```

**job dict 可用字段**（从 scheduler.py 分析）：
- `job["name"]` — 任务名称（str）
- `job["id"]` — 任务 ID（str）
- `job["schedule"]` — 调度配置 dict，含 `kind`（cron/interval/once）、`cron`（表达式）、`interval`（秒数）
- `job["last_run_at"]` — 上次执行时间（ISO str）
- `job["failure_streak"]` — 连续失败次数（int）
- `job["last_error"]` — 最近错误信息（str）
- `job["deliver"]` — 投递目标

**content 中的状态信号**（scheduler.py L209-343）：
- 成功：正常内容（无特殊前缀）
- 失败：以 `"⚠️ Cron '{job_name}' failed:"` 开头
- 超时：`"...script timed out..."`
- 认证失败：`"...authentication error..."`
- Provider 不可达：`"...provider timeout..."`

---

## 3. 改动范围

### 3.1 签名变更

#### `build_cron_card()`（card/special.py）

当前签名：
```python
def build_cron_card(content: str, *, title: str = "⏰ 定时任务", job_name: str = "") -> dict[str, Any]
```

改进签名：
```python
def build_cron_card(
    content: str,
    *,
    title: str = "⏰ 定时任务",
    job_name: str = "",
    status: str = "success",      # "success" | "error" | "warning"
    job_id: str = "",             # 用于 footer 显示
    schedule: dict | None = None, # job["schedule"] 透传，用于 footer
    failure_streak: int = 0,      # 连续失败次数
    last_error: str = "",         # 错误摘要
    elapsed_ms: float = 0,        # 执行耗时（毫秒），0 表示未知
) -> dict[str, Any]
```

#### `_do_cron_deliver()`（controller.py）

当前签名：
```python
async def _do_cron_deliver(self, chat_id: str, content: str, *, job_name: str = "") -> None
```

改进签名：
```python
async def _do_cron_deliver(
    self, chat_id: str, content: str, *,
    job_name: str = "",
    job_id: str = "",
    status: str = "success",
    schedule: dict | None = None,
    failure_streak: int = 0,
    last_error: str = "",
) -> None
```

#### `on_cron_deliver()`（interceptors/hooks.py）

当前签名：
```python
async def on_cron_deliver(*, chat_id: str, content: str, category: str = "", loop: Any = None) -> bool
```

改进签名：
```python
async def on_cron_deliver(
    *,
    chat_id: str,
    content: str,
    category: str = "",
    loop: Any = None,
    job: dict | None = None,    # 新增：完整 job dict
) -> bool
```

#### `_wrap_cron_deliver` → `on_cron_deliver` 调用链

在 gateway.py `_card_sending_send` 中，当前：
```python
await ctrl._do_cron_deliver(chat_id, cleaned.strip(), job_name=job_name)
```

改为：
```python
await ctrl._do_cron_deliver(
    chat_id, cleaned.strip(),
    job_name=job_name,
    job_id=job.get("id", ""),
    status=_detect_cron_status(cleaned),
    schedule=job.get("schedule"),
    failure_streak=int(job.get("failure_streak", 0)),
    last_error=job.get("last_error", ""),
)
```

### 3.2 返回值

`build_cron_card` 返回值不变（`dict[str, Any]` — CardKit 2.0 JSON）。无破坏性变更。

---

## 4. 状态检测逻辑

### 4.1 优先级策略

```
1. content 内容检测（最高优先级 — 本次执行的真实结果）
2. job["failure_streak"]（fallback — scheduler 追踪的连续失败）
3. 默认 "success"
```

### 4.2 content 检测规则

在 `_wrap_cron_deliver` 的 `_card_sending_send` 中，content 已经经过 scheduler 的 `_summarize_cron_failure_for_delivery` 处理：

```python
def _detect_cron_status(content: str) -> str:
    """从 content 中推断执行状态。"""
    if not content:
        return "success"
    
    lower = content.lower().strip()
    
    # 红色：明确的错误信号
    error_signals = [
        "⚠️ cron",        # scheduler 的失败前缀
        "failed:",        # 通用失败
        "error:",         # 错误标记
        "traceback",      # Python 异常
        "exception",      # 异常
    ]
    if any(s in lower for s in error_signals):
        return "error"
    
    # 橙色：警告信号
    warning_signals = [
        "warning",
        "⚠️",
        "deprecated",
        "timeout",
    ]
    if any(s in lower for s in warning_signals):
        return "warning"
    
    return "success"
```

### 4.3 状态 → 颜色映射

| 状态 | Header 颜色 | Emoji | Header 标题 |
|------|------------|-------|------------|
| success | green | ✅ | `{job_name}` |
| error | red | ❌ | `{job_name}` |
| warning | orange | ⚠️ | `{job_name}` |

---

## 5. Header 模板设计

### 5.1 结构化 Header

```python
def _build_cron_header(
    job_name: str,
    status: str,
    elapsed_ms: float = 0,
) -> dict:
    """构建 cron 卡片的结构化 header。"""
    # 状态 → 颜色 + emoji
    status_map = {
        "success": ("green", "✅"),
        "error":   ("red",   "❌"),
        "warning": ("orange", "⚠️"),
    }
    template, emoji = status_map.get(status, ("green", "✅"))
    
    # 标题：emoji + job_name
    title = f"{emoji} {job_name or '定时任务'}"
    
    # 副标题：耗时（如果 > 0）
    subtitle = ""
    if elapsed_ms > 0:
        from .elements import _format_elapsed
        subtitle = _format_elapsed(elapsed_ms)
    
    return build_card_header(
        title=title,
        subtitle=subtitle,
        template=template,
    )
```

### 5.2 header 效果预览

```
✅ 早报推送                    → 绿色 header
   · 2.3s

❌ 天气查询                    → 红色 header
   · 15.0s

⚠️ 数据同步                    → 橙色 header
   · 5.1s
```

---

## 6. 内容折叠实现

### 6.1 方案选择：collapsible_panel

飞书 CardKit 2.0 原生支持 `collapsible_panel`，项目中已有 `_collapsible_panel` 积木（elements.py L179-210），无需引入新组件。

### 6.2 折叠策略

```python
CRON_CONTENT_FOLD_THRESHOLD = 800  # 字符数阈值
CRON_CONTENT_PREVIEW_LEN = 300     # 预览长度
```

**规则**：
- content ≤ 800 字符：全部展示，不折叠
- content > 800 字符：前 300 字符作为预览，完整内容放入 collapsible_panel（默认折叠）

### 6.3 实现结构

```python
def _build_cron_content_elements(content: str) -> list[dict]:
    """构建 cron 卡片的内容区域，超长内容自动折叠。"""
    from .md import optimize_markdown_style, _downgrade_tables, _split_long_text
    from .elements import _collapsible_panel
    
    # 预处理：标题降级 + 表格降级 + 星号转义
    processed = optimize_markdown_style(content)
    processed = _downgrade_tables(processed, limit=_MAX_CRON_TABLES)
    
    if len(processed) <= CRON_CONTENT_FOLD_THRESHOLD:
        # 短内容：直接展示
        chunks = _split_long_text(processed)
        return [{"tag": "markdown", "content": c} for c in chunks if c.strip()]
    
    # 长内容：折叠
    preview = processed[:CRON_CONTENT_PREVIEW_LEN].replace("\n", " ").strip()
    full_chunks = _split_long_text(processed)
    
    panel = _collapsible_panel(
        expanded=False,
        title_el={
            "tag": "plain_text",
            "content": f"📄 完整内容 ({len(processed)} 字)",
            "text_size": "notation",
        },
        elements=[{"tag": "markdown", "content": c} for c in full_chunks if c.strip()],
        border_color="grey",
        header_color="grey",
    )
    
    return [
        {"tag": "markdown", "content": f"{preview}..."},
        panel,
    ]
```

### 6.4 折叠效果预览

```
┌─────────────────────────────────────┐
│ ✅ 早报推送                         │
├─────────────────────────────────────┤
│ 今日天气晴，气温 25-32℃...          │  ← 预览（300字）
│                                     │
│ ┌─────────────────────────────────┐ │
│ │ 📄 完整内容 (2,450 字)     ▶   │ │  ← 折叠面板（默认收起）
│ └─────────────────────────────────┘ │
│─────────────────────────────────────│
│ 📌 定时任务 · 早报推送 · 08:30      │  ← Footer
└─────────────────────────────────────┘
```

---

## 7. Footer 信息增强

### 7.1 当前 Footer

```
📌 定时任务 · {job_name} · {HH:MM}
```

### 7.2 改进 Footer

```
📌 {label} · {job_name} · {schedule_display} · {HH:MM}
   状态: ✅ 成功 | 耗时: 2.3s | 失败次数: 0
```

### 7.3 Footer 实现

```python
def _build_cron_footer(
    job_name: str = "",
    status: str = "success",
    schedule: dict | None = None,
    failure_streak: int = 0,
    elapsed_ms: float = 0,
) -> list[dict]:
    """构建 cron 卡片的增强 footer。"""
    from datetime import datetime
    from .elements import _format_elapsed
    
    now = datetime.now().strftime("%H:%M")
    
    # 行 1：标签 + 任务名 + schedule
    parts = ["📌 定时任务"]
    if job_name:
        parts.append(job_name)
    if schedule:
        schedule_display = _format_schedule(schedule)
        if schedule_display:
            parts.append(schedule_display)
    parts.append(now)
    
    # 行 2：状态 + 耗时 + 失败次数
    status_map = {"success": "✅ 成功", "error": "❌ 失败", "warning": "⚠️ 警告"}
    meta_parts = [status_map.get(status, status)]
    if elapsed_ms > 0:
        meta_parts.append(f"耗时 {_format_elapsed(elapsed_ms)}")
    if failure_streak > 0:
        meta_parts.append(f"连续失败 {failure_streak} 次")
    
    elements = [{"tag": "hr"}]
    elements.append({
        "tag": "markdown",
        "content": " · ".join(parts),
        "text_size": "notation",
    })
    if len(meta_parts) > 1:  # 只在有额外信息时显示第二行
        elements.append({
            "tag": "markdown",
            "content": " · ".join(meta_parts),
            "text_size": "notation",
        })
    
    return elements


def _format_schedule(schedule: dict) -> str:
    """将 schedule dict 格式化为可读字符串。"""
    kind = schedule.get("kind", "")
    if kind == "cron":
        expr = schedule.get("cron", "")
        return f"cron: {expr}" if expr else ""
    elif kind == "interval":
        seconds = schedule.get("interval", 0)
        if seconds >= 3600:
            return f"每 {seconds // 3600}h"
        elif seconds >= 60:
            return f"每 {seconds // 60}m"
        return f"每 {seconds}s"
    elif kind == "once":
        return "一次性"
    return ""
```

---

## 8. 30KB 大小控制

### 8.1 约束

- 飞书 CardKit 2.0 单卡 JSON 大小软限 ≈ 30KB
- 飞书单卡硬限：200 个含 `tag` 的 JSON 对象
- 当前 `_split_long_text` 限制单块 2400 字符

### 8.2 控制策略

```python
CRON_CARD_MAX_CHARS = 25000  # 安全阈值（低于 30KB，留余量

def _enforce_cron_card_limit(content: str, max_chars: int = CRON_CARD_MAX_CHARS) -> str:
    """截断超长 cron 内容，保留首尾。"""
    if len(content) <= max_chars:
        return content
    
    head_len = int(max_chars * 0.6)   # 前 60%
    tail_len = int(max_chars * 0.35)  # 后 35%
    hint = f"\n\n--- ⚡ 内容已截断（原始 {len(content)} 字，显示前 {head_len} + 后 {tail_len} 字）---\n\n"
    
    return content[:head_len] + hint + content[-tail_len:]
```

### 8.3 元素计数防护

复用 builder.py 的 `_enforce_card_element_limit()` 逻辑。cron 卡片的 body.elements 在组装后调用 `_count_tag_objects(card)`，超过 195（200-5 margin）时从 content 的 markdown 块中截断。

---

## 9. 完整 build_cron_card 改进后结构

```
build_cron_card(content, job_name, status, job_id, schedule, failure_streak, last_error)
│
├─ header: _build_cron_header(job_name, status, elapsed_ms)
│    ├─ template: green/red/orange（按 status）
│    ├─ title: "✅ 早报推送"
│    └─ subtitle: "2.3s"（耗时）
│
├─ config.summary: content[:120]（消息列表预览）
│
├─ body.elements:
│   ├─ _build_cron_content_elements(content)
│   │    ├─ [短内容] 1-3 个 markdown chunk
│   │    └─ [长内容] preview markdown + collapsible_panel（完整内容）
│   │
│   └─ _build_cron_footer(job_name, status, schedule, failure_streak, elapsed_ms)
│        ├─ hr
│        ├─ "📌 定时任务 · 早报推送 · 每 8h · 08:30"
│        └─ "✅ 成功 · 耗时 2.3s"
│
└─ 最终 JSON 校验: _enforce_cron_card_limit() + _count_tag_objects()
```

---

## 10. 修改文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `card/special.py` | 核心改动 | `build_cron_card` 签名扩展 + header/折叠/footer 重构 |
| `card/elements.py` | 新增函数 | `_build_cron_footer()`, `_format_schedule()` |
| `controller.py` | 签名扩展 | `_do_cron_deliver` 透传 status/schedule/failure_streak |
| `interceptors/hooks.py` | 签名扩展 | `on_cron_deliver` 接受 `job` dict |
| `interceptors/gateway.py` | 调用适配 | `_card_sending_send` 传递 job 元数据 + 调用 `_detect_cron_status` |
| `config/defaults.py` | 新增常量 | `CRON_CONTENT_FOLD_THRESHOLD`, `CRON_CONTENT_PREVIEW_LEN`, `CRON_CARD_MAX_CHARS` |

---

## 11. 向后兼容

- `build_cron_card` 新参数全部有默认值，旧调用方不受影响
- `_do_cron_deliver` 新参数全部有默认值
- `on_cron_deliver` 的 `job` 参数可选
- 不改 CardKit schema 版本（仍为 2.0）
- 不改 `__all__` 导出列表

---

## 12. 测试要点

1. **短内容**（< 800 字）：无折叠，header 绿色，footer 两行
2. **长内容**（> 800 字）：折叠面板出现，preview 300 字
3. **错误内容**（含 "failed:"）：header 红色，footer 显示"❌ 失败"
4. **警告内容**（含 "⚠️"）：header 橙色
5. **超长内容**（> 25000 字）：被截断，显示截断提示
6. **无 job_name**：header 显示 "⏰ 定时任务"
7. **有 schedule**：footer 显示 cron 表达式或间隔
8. **failure_streak > 0**：footer 显示连续失败次数
9. **元素计数**：body.elements 总数 < 200
10. **JSON 大小**：最终 JSON < 30KB
