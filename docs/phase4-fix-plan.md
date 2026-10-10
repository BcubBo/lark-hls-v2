# Phase-4 修复计划（2026-10-10）

> 依据：`docs/phase3-runtime-audit.md`  
> 真源：WSL `~/.hermes/profiles/bo/plugins/lark-hls-v2`  
> 流程：改 WSL → 回归 → commit Gitea →（用户确认后）同步 prod + 重启

## 目标

1. 恢复 prod 权限注入（FeishuIdentity 可加载 + stub 不抛错）  
2. 消化 230002 日志风暴，失败可观测但不刷屏  
3. 流卡生命周期在 journal 可见（ready/create/seal）  
4. 补 COMPLETING 尾部 drain，降低丢尾风险  

## 任务

| # | 任务 | 文件 | 验收 |
|---|------|------|------|
| T4.1 | identity 多路径探测 + stub 补齐 + 宽异常 | `interceptors/hooks.py` | prod 无 `auto_set_role_from_chat` 报错；import 成功 |
| T4.2 | 230002/终态 API 错误降噪 + 按 chat 缓存 | `controller.py` `feishu/client.py` | 同一 chat 230002 只打 1 次 WARN |
| T4.3 | 可观测：ready/create/seal/completed 用 WARNING | `card_flow.py` `interceptors/__init__.py` | journal 能看到完整流卡生命周期 |
| T4.4 | COMPLETING 前二次 drain | `card_flow.py` | finalize 前 answer_text 再刷一次 |
| T4.5 | 回归 + 同步 WSL 自测；prod 同步待确认 | `docs/phase2_regression.py` | 29/29 绿 |

## 不做（本阶段）

- 运维：bot 拉回 home 群（需人工）  
- 大范围 except 清理  
- continuation 单卡策略重做  

## 发布

1. WSL commit → `git push gitea main`  
2. 自测 / 你看一眼  
3. `rsync` → prod + `systemctl --user restart hermes-gateway`（**上 prod 前再确认**）  
