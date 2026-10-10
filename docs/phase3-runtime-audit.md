# lark-hls-v2 Phase-3 深度审计报告（代码 + 运行时）

> 范围：WSL 真源 `~/.hermes/profiles/bo/plugins/lark-hls-v2`（`a20f14c`）+ WSL/Prod gateway 运行时  
> 日期：2026-10-10  
> 前置：phase-1 吞没修复、phase-2 兑底/防分裂、三方 MD5 对齐

---

## 0. 运行时健康总览

| 项 | WSL (`hermes-gateway-bo`) | Prod (`hermes-gateway`) |
|----|---------------------------|-------------------------|
| 进程 | active 7h+ | active |
| HLS 近 24h 日志 | 77 条，**几乎全是 gateway card 失败** | 有真实消息流量 |
| 流式生命周期日志 | **7 天内 0 条**（无 phase transition / card created） | 有消息进出，但 **FeishuIdentity 每条报错** |
| patch self-check | journal 未见 | journal 未见 |

---

## 1. P0 — 运行时故障

### 1.1 Home 频道 bot 不在会话（230002）· WSL 全量失败

```
FeishuAPIError: send_card_to_chat: code=230002
msg=Bot/User can NOT be out of the chat.
target feishu:oc_a029256e7c720452de255b2ae45fa85e
```

- **现象**：启动通知、cron 投递、gateway 卡片全部失败；24h 内反复 Traceback  
- **影响**：home 频道所有系统卡片/定时任务静默丢失（仅日志）  
- **根因**：bot 被移出 `oc_a029256e…` 或权限变更  
- **建议**：
  1. 把 bot 拉回该群，或改 home channel  
  2. HLS 对 230002 做**终态缓存**，同一 chat 短期内不再狂刷 Traceback（现在每条 cron 都打全栈）

### 1.2 FeishuIdentity 路径错误 → 权限注入全灭 · Prod

**证据**（Prod journal 高频）：

```
[FeishuIdentity] Failed to inject system_role:
'_FeishuIdentity' object has no attribute 'auto_set_role_from_chat'
```

**根因**（已复现验证）：

| 位置 | 值 |
|------|-----|
| Prod 进程 `HERMES_HOME` | `/home/ubuntu/.hermes` |
| `hooks.py` 推导 scripts | `/home/ubuntu/.hermes/scripts` ← **不存在** |
| 真实模块 | `/home/ubuntu/.hermes/profiles/bo/scripts/feishu_identity.py` |
| 行为 | `import feishu_identity` 失败 → 落入 stub `_FeishuIdentity` |
| stub 缺方法 | `auto_set_role_from_chat` / `get_role` |

- **影响**：每条消息 role 注入失败；DM 自动 admin、群角色、权限前缀全部失效  
- **修复**（待做）：
  1. `_scripts_dir` 探测顺序：`HERMES_HOME/scripts` → `HERMES_HOME/profiles/*/scripts` → `~/.hermes/profiles/bo/scripts`  
  2. stub 补齐 no-op：`auto_set_role_from_chat` / `get_role` / `set_role`，避免 AttributeError  
  3. `except ImportError` 扩成 `except Exception`，import 失败也落 stub 并打 WARNING  

### 1.3 流式卡片生命周期日志为空

- journal 无 `phase transition` / `linear card created` / seal  
- 可能原因：`lark_hls_v2` logger 级别 ≥ WARNING（只看到 WARNING 的 delivery failed）  
- **风险**：线上是否真正创建流卡**无法从日志确认**  
- **建议**：apply_patches 成功时强制 `WARNING` 级输出「HLS ready」；关键路径（create/seal）保留 INFO 并确认 hermes logger 配置

---

## 2. 代码审计（相对 phase-2 后的残留）

### 2.1 仍可能吞没 / 不可观测

| 位置 | 问题 | 级别 |
|------|------|------|
| `hooks.py` stub | 缺方法 → 运行时 AttributeError，role 注入静默降级 | **P0** |
| `hooks.py` path | `HERMES_HOME` 与 `profiles/bo` 不一致 | **P0** |
| `controller._do_gateway_deliver` | 230002 打全栈 Traceback，无错误码降噪 | P1 |
| `flush.schedule_update` | `mark_completed` 后静默丢弃；COMPLETING 窗口靠 finalize 整文重写 | P1 |
| 多处 `except Exception: pass` | controller 20+ / adapter 若干，失败不可见 | P1 |
| `card_flow` schema 分支 | Phase3 仍可能清 `panel_dirty`（answer 有 fallback） | P2 |

### 2.2 Phase-1/2 修复状态（代码层，回归 29/29 仍绿）

| 项 | 状态 |
|----|------|
| hooks 入账 bool | 已合入 |
| strip 混排保答案 / thinking 成对块 | 已合入 |
| 200860 流式/drain | 已合入 |
| seal drain 保留 dirty | 已合入 |
| 空卡放行文本 | 已合入 |
| continuation 同卡追加 | 已合入 |
| schema get_secret | 已合入 |
| tenant user_id 映射 | 已合入 |

### 2.3 并发 / 生命周期（逻辑风险，未见线上证据）

1. **COMPLETING 窗口**：`_do_unified_flush` 对 COMPLETING 直接 return；依赖 finalize 整文重写。若 finalize 的 dirty 检查后仍有 delta，会丢（窗口极短）。  
2. **`_fire_and_forget(loop=None)`**：`_get_loop()` 失败时可能 NPE（有部分调用未判空）。  
3. **CardSession 纯内存**：gateway 重启丢 session（已知，靠 orphan/静态路径兜底）。  
4. **TTL prune seal**：idle>TTL 才封卡；长任务仍可能与 completion 竞态（有 footer 合并补丁）。

---

## 3. 配置 / 部署

| 项 | 值 |
|----|-----|
| WSL 运行时 | `~/.hermes/profiles/bo/plugins/lark-hls-v2` = `a20f14c` |
| Prod 运行时 | `~/.hermes/plugins/lark-hls-v2`（md5 对齐 phase-2 后） |
| Git 真源 | Gitea `bcubbo/lark-hls-v2`（main=细粒度，stable→GitHub） |
| plugin.yaml | `version: 2.0.9.0`，`provides_hooks: [pre_gateway_dispatch]` |
| 启用条件 | `cfg.enabled && (feishu_app_id \|\| env_app_id)` |

---

## 4. 优先修复清单

| 优先级 | 项 | 动作 |
|--------|-----|------|
| **P0** | home 群 230002 | 运维：拉 bot 回群或改 home channel |
| **P0** | FeishuIdentity 路径 + stub | 代码：多路径探测 + stub 补方法 + 宽异常 |
| **P1** | 230002 日志降噪 | 代码：终态缓存 / 单行 WARN |
| **P1** | 可观测性 | 代码：HLS ready / create / seal 用 WARNING 保底 |
| **P2** | COMPLETING 尾巴 | 代码：finalize 前二次 drain |
| **P2** | except pass 清理 | 代码：至少 debug 日志 |

---

## 5. 验证建议

1. **Identity**：修路径后 journal 不再出现 `auto_set_role_from_chat`；群消息 role 注入成功日志  
2. **Home 群**：cron / 启动通知不再 230002  
3. **流式**：在有 bot 的群发消息，确认 journal 出现 `linear card created` → `phase transition` → seal  
4. **回归**：`python3 docs/phase2_regression.py` 仍 29/29  

---

## 6. 结论

- **卡片吞没路径** phase-1/2 代码层已修，回归绿。  
- **当前最大风险在运行时**：① home 群 230002 导致系统卡片全丢；② prod `HERMES_HOME` 导致 FeishuIdentity 桩类，权限注入全灭。  
- 流式主路径是否在生产正常建卡，**日志不可观测**，需先恢复 identity/日志再实测。
