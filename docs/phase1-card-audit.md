# HLS 流式卡片 · Phase-1 全面审计报告

> 范围：`D:\Mimo_Desktop\projects\lark-hls-v2`（含记忆库既有问题清单交叉核对）
> 焦点：**流卡处理信息时的消息吞没 / 静默丢失**
> 日期：2026-09-24

---

## 0. 记忆库既有结论（交叉验证）

| 来源 | 问题 | 本次审计结论 |
|------|------|----------------|
| backlog | 200860 流式阶段未处理 → 静默丢失 | **确认存在**，已补处理 |
| backlog | 外部任务返回后走静态卡而非流卡 | 仍存在（见 §3.8），phase-2 |
| backlog | 流式卡片分离（多张独立卡） | 仍存在（continuation / interrupt 路径），phase-2 |
| backlog | seal 卡超时 | 仍存在（`_card_ready` 30s / TTL seal），phase-2 |
| lesson | 静态卡 vs 内容截断是两个问题 | 本次聚焦内容截断/吞没 |
| P0 | CardKit streaming TTL 后跳过 close_streaming | 已有历史修复；TTL prune 改为 seal |
| P0 | 300309/300313 drain 丢答案 | 已有 fallback；seal drain 仍有吞没（已修） |

---

## 1. 消息路径总览

```
agent callbacks
  stream_delta / interim / reasoning / tool
        │
        ▼
callbacks.py 包装器  ──(吞点 A: 恒 True 则不调 orig)──
        │
        ▼
hooks.py on_*_delta  ──(吞点 B: 恒 True)──
        │
        ▼
controller.on_*      ──(吞点 C: strip / session / guard 丢弃)──
        │
        ▼
UnifiedLinearState   ──(吞点 D: 累计前缀去重丢尾)──
        │
        ▼
card_flow flush      ──(吞点 E: 200860 / schema 清 dirty)──
        │
        ▼
flush.controller     ──(吞点 F: mark_completed 后 schedule 空转)──
        │
        ▼
feishu client API    ──(吞点 G: seal drain 失败仍清 dirty)──
        │
        ▼
adapter.send 抑制    ──(吞点 H: card_sent 抑制文本兑底)──
```

---

## 2. 吞没路径明细（按严重度）

### P0-1 · hooks 恒返回 True，丢弃后原回调不再兜底

**位置**：`interceptors/hooks.py` `on_answer_delta` / `on_thinking_delta` / `on_tool_updated`  
**机制**：hook 成功转发后无条件 `return True`。`callbacks.py` 见 True 则 `return`，**不调用** `_orig_stream`。  
**后果**：`controller.on_answer` 因 session 缺失 / guard skip / strip 后为空而丢弃时，内容既不上卡，也不进 Hermes 原通道 → **彻底吞没**。  
**修复（已做）**：`controller.on_answer/on_thinking/on_reasoning/on_tool_update` 返回是否入账；hooks 透传该布尔；wrapper 仅在 True 时短路。

### P0-2 · seal drain 失败仍清 `answer_dirty`

**位置**：`card_flow.py` `_finalize_card` seal drain answer 分支  
**机制**：`except FeishuAPIError` 的非 300309/300313 分支（含 200860）只打 warning，随后 **无条件** `state.answer_dirty = False`。  
**后果**：封卡瞬间 API 失败（超限/瞬态）→ 剩余答案被标记已写、实际未上卡。  
**修复（已做）**：失败时保留 dirty 并 `return`，交给后续 final fallback / 拆分路径。

### P0-3 · 流式阶段 200860 未处理

**位置**：`card_flow.py` `_do_unified_flush` / drain；`feishu/client.py` 已定义 `CARDKIT_CARD_TOO_LARGE = 200860` 但 **无调用点**  
**机制**：stream_element / partial_update 遇 200860 落入 debug 日志或 generic 失败，与记忆「静默丢失」一致。  
**修复（已做）**：  
- 流式 flush 识别 200860 → 保留 dirty，封卡走 20KB 拆分  
- drain 识别 200860 → 截断 fallback，失败仍保留 dirty  

### P0-4 · `strip_reasoning_tags` 整段清空吞掉答案尾

**位置**：`state/text.py`  
**机制**：文本以 `Reasoning:\n` 开头时 **整串返回 ""**。`on_answer` 对 **delta** 也用该函数 → 同 chunk 内「推理前缀 + 答案」全被吞。  
**修复（已做）**：  
- 新增 `strip_thinking_tags_only` 供流式 delta（只剥标签）  
- `strip_reasoning_tags` 改为：`Reasoning:` 块后空行分隔的答案段予以保留  
- `on_answer` 改用 delta 安全剥离  

### P1-5 · `on_reasoning_delta` 累计全文投递丢尾

**位置**：`state/linear.py`  
**机制**：累计投递时 `text` 以 `_current_reasoning` 为前缀则 **整段 return**，新尾部不追加。  
**修复（已做）**：前缀匹配改为「只追加新增尾部」；完全相同才跳过。

### P1-6 · `_linear_on_thinking` 非前缀扩展整段跳过

**位置**：`card_flow.py`  
**机制**：interim 全文若不是已有 answer 的前缀扩展（模型重写、分段不一致）→ 落入 `else: already captured, skip`。  
**修复（已做）**：更长且非前缀时整段替换并标 dirty。

### P1-7 · `mark_completed` 后 flush 调度空转

**位置**：`flush/controller.py` `schedule_update`  
**机制**：`_completed=True` 时直接 return。`on_answer` 在 COMPLETING 窗口仍可入账 dirty，但 `_do_unified_flush` 对 COMPLETING 直接 return，且 schedule 已关。  
**状态**：finalize 有二次 dirty drain，属窗口竞态；**未改**（phase-2 评估 COMPLETING 窗口入账策略）。

### P1-8 · adapter 抑制文本回复

**位置**：`interceptors/adapter.py`  
**机制**：`card_sent` / 活跃流卡 session 存在时抑制 gateway 文本（设计如此，防双发）。  
**风险**：卡片内容被吞后没有文本兜底 → 用户看到空卡/残卡。  
**状态**：依赖 P0-1~4 修复后内容完整性；抑制逻辑本身 **保留**。  
**建议 phase-2**：封卡失败或 answer 为空时放行文本 fallback，而不是继续抑制。

### P2-9 · schema error 清 dirty（有意丢弃）

**位置**：`card_flow.py` Phase2/3 schema 分支  
**机制**：永久 schema 错误清 dirty 停重试。  
**评价**：避免死循环合理，但等于丢内容。建议 phase-2 改为降级写纯文本元素。

### P2-10 · 其它已知但不在本焦点

- 外部任务/subagent 返回走静态卡（回调未触发流卡创建）  
- 流卡分离（continuation 多卡）  
- seal TTL / `_card_ready` 30s 超时  
- gateway 重启 CardSession 内存丢失  

---

## 3. Phase-1 已修改文件

| 文件 | 变更 |
|------|------|
| `state/text.py` | `strip_thinking_tags_only`；`strip_reasoning_tags` 保留答案尾 |
| `state/linear.py` | reasoning 累计前缀只追加尾部 |
| `controller.py` | `on_answer/on_thinking/on_reasoning/on_tool_update` 返回 bool；delta 用安全剥离 |
| `interceptors/hooks.py` | hooks 透传入账结果，不再恒 True |
| `card_flow.py` | seal drain 失败保留 dirty；200860 流式/drain 处理；thinking 非前缀替换 |

**语法检查**：`py_compile` 全部通过。

---

## 4. 验证建议（phase-1 交付后）

1. **混合 chunk**：构造 `stream_delta("Reasoning:\n想一想\n\n答案是 42")`，卡片应显示「答案是 42」。  
2. **200860**：灌 >20KB 答案，流式阶段不丢、封卡拆分。  
3. **累计 reasoning**：连续投递 `"abc"` → `"abcd"`，面板应出现 `abcd` 而非 `abc`。  
4. **seal 瞬时失败**：mock API 失败，确认 dirty 保留且 final fallback 能写出。  
5. **回归**：正常短问答卡片流式/封卡/动态台词不受影响。

---

## 5. Phase-2 建议优先级

1. COMPLETING 窗口答案入账与 flush 策略（P1-7）  
2. 封卡失败时 adapter 放行文本 fallback（P1-8）  
3. 外部任务返回 → 强制走流卡创建（backlog #1）  
4. continuation 单卡化 / 抑制多卡分裂（backlog #2）  
5. schema error 降级为纯文本元素而非清 dirty（P2-9）  

---

## 6. Phase-2 实施记录（2026-09-24）

| 项 | 状态 | 实现 |
|----|------|------|
| 空卡放行文本 fallback | 已做 | `adapter._session_card_has_content`；空卡不再抑制文本 |
| schema error 降级 | 已做 | Phase-2 flush schema 分支尝试 `_fallback_write_answer` 再清 dirty |
| 封卡 answer 元素缺失 | 已做 | finalize 用 `add_elements` 补建 markdown，不再跳过写入 |
| continuation 防分裂 | 已做 | 流关后同卡 `partial_update` 追加，不再另开 `-cont-N` 卡 |
| 孤儿完成补结果卡 | 已做 | `on_completed` 无 session 但有 answer 时 `_deliver_orphan_answer` |
| COMPLETING 窗口 | 部分 | finalize 整文重写已覆盖；flush 对 COMPLETING 仍早退（避免抢写） |

**语法检查**：`py_compile` 全部通过。  
**提交**：phase-1 已提交 `61395a2`；phase-2 改动待提交。