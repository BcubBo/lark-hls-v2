# Phase-6 计划：单卡生命周期（永久解决 10min 新卡/丢思考/截断）

> 产品决策（已确认）：**默认同卡续写**；仅飞书永久拒绝才开新卡。  
> 红线：正文不丢；思考不静默丢弃。

## 目标

1. TTL 到期只做视觉收束，**不**把仍在跑的 session 打成终态  
2. 后续 token 一律回**同一 card_id**（partial_update / add_elements）  
3. 开新卡仅当：卡删除、200860 无法处理等**永久拒绝**  
4. 思考进当前卡 panel；新卡必须带 answer **全量** + 「续」标记  

## 任务

| # | 任务 | 文件 |
|---|------|------|
| T6.1 | TTL prune 改为视觉 seal，STREAMING 不 COMPLETED | controller.py |
| T6.2 | 同卡续写默认；新卡仅永久拒绝 | controller.py card_flow.py |
| T6.3 | 新卡/orphan 带全量 answer + 思考策略 | controller.py |
| T6.4 | **严谨测试** `docs/test_phase6_single_card.py` | docs/ |
| T6.5 | 回归 + Gitea；prod 同步待确认 | |

## 测试用例（必须全绿）

### A. 同卡续写
1. STREAMING + streaming_closed + 新 answer → **不**新卡，partial_update 同卡  
2. 终态前 10min idle → 视觉 seal，session 仍可收 token  
3. 同上再收 thinking → 进同一 panel（不丢）

### B. 新卡门槛
4. 卡删除（1000023）→ 允许新卡  
5. 200860 → 截断/拆分，**不**新卡  
6. 无 card_id 且非终态 → orphan 新卡带**全量** answer

### C. 内容完整
7. 先答案后 thinking 再答案 → 全文在卡，思考在 panel  
8. merge 失败 → 文本单独发出（不丢）  
9. Recovered body → 并卡或单独发，**可见**

### D. 与 phase-2 兼容
10. 空卡仍放行文本  
11. 回归 29/29 不红  

## 发布

WSL 改 → 测试 → Gitea →（确认后）prod + `systemctl --user restart hermes-gateway.service`
