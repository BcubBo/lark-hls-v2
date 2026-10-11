# lark-hls-v2 Phase-5 完整卡片审计（对 Hermes 0.21.6）

> 范围：prod 运行时 + 全链路代码（create → stream → seal）  
> 背景：hermes update `0.21.5 → v0.21.6+635.g5473cec`（674 commits）后  
> 日期：2026-10-11  
> 真源：WSL `~/.hermes/profiles/bo/plugins/lark-hls-v2` / prod `~/.hermes/plugins/lark-hls-v2`

---

## 0. 一句话结论

**流卡主路径是通的**（今日 41 次 `linear card created` → `finalize` → `card completed`）。  
真正让人「总觉得哪里不对」的，是 **Hermes 0.21.6 的 stream consumer 与 HLS 卡片流是两套体系**：hermes 每回合都打 `final-send NOT suppressed`（405 次），`streamed=False / content_delivered=False`。  
是否「卡片+纯文本双发」取决于 HLS 的 adapter 抑制；**日志里看不到 suppress 命中证据**，需要你实测一条消息确认 UI 上有没有双份。

---

## 1. 运行时（0.21.6 更新后）

| 项 | 状态 |
|----|------|
| Gateway | active，feishu websocket 已连 |
| HLS 加载 | `HLS ready` + `patches applied OK` |
| patch self-check | GatewayRunner=ok FeishuAdapter=ok |
| 流卡生命周期 | 今日多轮 create → seal → completed **正常** |
| `_vendor/yaml` | 在，插件不依赖 tools 环境 yaml |
| `hermes_version` 显示 | 仍写 `0.21.5`（包元数据/检测缓存），**与真实 0.21.6+ 不符**（仅观测问题） |

---

## 2. 与 0.21.6 的接口对齐

| 挂载点 | 0.21.6 位置 | 结论 |
|--------|-------------|------|
| `GatewayRunner._handle_message` | `gateway/run.py` / mixin | 仍可挂 |
| `_handle_message_with_agent` | **`gateway/run_turn.py`** | 类属性仍存在，patch 成功 |
| `_run_agent` / `_run_background_task` | **`gateway/run_turn.py`** | 同上 |
| `run_conversation` | `agent/conversation_loop.py` + `turn_facade.py` | 仍可挂 |
| `FeishuAdapter.send/edit_message/send_clarify/_handle_card_action_event` | `plugins/platforms/feishu/adapter.py` | 仍在 |

**风险**：turn 逻辑整文件拆到 `run_turn.py`。HLS 靠 `GatewayRunner._xxx` 类属性 monkey-patch，**当前 OK**；若以后改成实例绑定/换 mixin 名，会静默变成「无卡片、纯文本」。`CRITICAL patch targets missing` 是唯一哨兵。

---

## 3. 「总觉得有问题」——重点嫌疑

### P1-A · 双体系 → final-send 风暴（405 次）

```
Normal final-send NOT suppressed despite active stream consumer
streamed=False previewed=False content_delivered=False
```

- hermes 0.21.6 自己的 stream consumer 认为「没流式交付」→ 总要补一刀 final 文本  
- HLS 用 CardKit 流，**不会**去标 hermes 的 `final_content_delivered`  
- 结果：每回合一条 WARNING；若 adapter 抑制失败 → **卡片 + 纯文本双份**

**验证**：飞书里看一条回复是否出现「卡片下面又一条纯文本」。  
**修复方向**（二选一）：
1. HLS 封卡时通知 hermes stream consumer「已交付」（若 API 暴露）  
2. 保持 adapter 抑制，并在 suppress 成功时打 WARNING 留证（现在 INFO 可能被吃掉）

### P1-B · 抑制是否生效无证据

- `errors.log` 中 **0 条** `suppressing text` / `allow text fallback`  
- 可能只是日志级别，也可能抑制分支没走到  

**建议**：suppress 命中改为 WARNING，跑一条消息即可证实/证伪。

### P2-C · 版本号观测错误

- 实际 `v0.21.6+635.g5473cec`，HLS 日志 `hermes_version: 0.21.5`  
- 来自 `importlib.metadata.version("hermes-agent")` 或 fallback，update 后未刷新  

### P2-D · 历史已修项（回归仍在）

| 项 | 状态 |
|----|------|
| hooks 入账 bool / 吞没 | 已修 |
| strip 混排 / thinking 块 | 已修 |
| 200860 / seal dirty | 已修 |
| 空卡文本 fallback | 已修 |
| continuation 同卡 | 已修 |
| identity 多路径 + stub | 已修 |
| register 10s 超时 | 已修 |
| vendor PyYAML | 已修 |

---

## 4. 代码规模 vs 结构风险

约 35 py / 万行（含 vendor）。结构债仍集中在：

1. monkey-patch 面过大（Gateway 4 处 + Adapter 4 处 + callbacks 5 处）  
2. `except Exception: pass` 多（controller 20+）  
3. CardSession 纯内存，重启即丢  
4. 与 hermes stream consumer 集成缺失（见 P1-A）  

**不是「几行 bug」，而是「两套流式体系没对齐」**——这最符合你说的「行数不多但总感觉哪里不对」。

---

## 5. 建议动作（按优先级）

| # | 动作 | 目的 |
|---|------|------|
| 1 | 你在飞书看一条回复：卡片下有没有重复纯文本 | 证实/证伪 P1-A |
| 2 | suppress 路径打 WARNING + 一条实测 | 证实/证伪 P1-B |
| 3 | 评估：封卡后标记 hermes stream 已交付 | 消掉 405 告警与双发风险 |
| 4 | hermes_version 从 git describe / 运行时读 | 观测正确 |
| 5 | 大版本 update 后固定跑：HLS ready + self-check + 一条流卡 | 防再静默挂 |

---

## 6. 验收清单（你点一条消息即可）

- [ ] 出现流式卡片（打字机/面板）  
- [ ] **没有**第二条纯文本重复回复  
- [ ] 封卡有 footer（模型/耗时）  
- [ ] journal：`linear card created` … `card completed`  
- [ ] 无 `CRITICAL patch targets missing`  

若 [2] 失败（有双份），下一步就做 P1-A/B 修复。
