# AGENTS.md — lark-hls-v2 代码变更与同步约定

> 给在本仓库改代码的 Agent / 协作者：**先读本文件，再动手。**

---

## 1. 两棵树（2026-10-03 起）

| 树 | 路径 | 角色 |
|----|------|------|
| **WSL 开发/部署树** | `/home/ubuntu/.hermes/profiles/bo/plugins/lark-hls-v2` | **唯一开发源 + 本机运行时**。改代码、回归、git commit 都在这里。gateway 直接加载此目录。 |
| **Prod 部署树** | `49.233.206.54:/home/ubuntu/.hermes/plugins/lark-hls-v2` | 生产 Hermes gateway 加载的插件。**只从 WSL 同步，不在 prod 直接开发。** |

```
WSL 树（开发+本机生效） ──rsync/ssh──► Prod 树 ──restart──► Prod gateway
        │
        └── systemctl --user restart hermes-gateway-bo ──► 本机 gateway
```

**Win11 本地（`D:\Mimo_Desktop\projects\lark-hls-v2`）已废弃，不再作为代码树。**  
不要在那里改代码；若仍看到该目录，只作历史参考，改完也不会生效。

---

## 2. 改代码时必须做到

1. **只在 WSL 树改**：`/home/ubuntu/.hermes/profiles/bo/plugins/lark-hls-v2`（以 `ubuntu` 用户）。
2. **每次改动结束后，回复里写明**：
   - 改了哪些文件（完整路径）
   - commit hash
   - 是否已同步到 Prod（未同步写「仅 WSL，未上 prod」）
   - 本机/prod gateway 是否已重启
3. **提交信息**用 `fix(hls):` / `feat(hls):`，正文写动机。
4. **上 prod / 重启 prod gateway 前必须先征得用户确认**（影响线上飞书卡片）。本机 WSL 重启可直接做。

---

## 3. WSL → Prod 同步

```bash
# 以 ubuntu 用户在 WSL 执行
SRC=/home/ubuntu/.hermes/profiles/bo/plugins/lark-hls-v2
PROD=49.233.206.54
PROD_DIR=/home/ubuntu/.hermes/plugins/lark-hls-v2

rsync -a -e ssh \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.mimocode' \
  "$SRC/" "$PROD:$PROD_DIR/"

ssh "$PROD" 'systemctl --user restart hermes-gateway.service'

# 本机
systemctl --user restart hermes-gateway-bo.service
```

同步后比对核心文件 MD5（WSL vs Prod）：

`controller.py` `card_flow.py` `interceptors/hooks.py` `interceptors/adapter.py`  
`interceptors/__init__.py` `state/text.py` `state/linear.py` `config/schema.py` `plugin.yaml`

---

## 4. 合并红线（历史补丁勿丢）

| 来源 | 必须保留 |
|------|----------|
| hooks v2.2 | tenant user_id 映射（`ou_*` → `72f89f96`），禁止 open_id 写入 `source.user_id` |
| prod ce45f4f | `schema._secret_env` / `agent.secret_scope.get_secret` |
| plugin.yaml | `provides_hooks` 只声明 `pre_gateway_dispatch` |
| phase-1/2 | hooks 入账 bool、吞没修复、空卡 fallback、continuation 同卡追加 |

---

## 5. 回归

```bash
python3 docs/phase2_regression.py
# 期望 RESULT: N passed, 0 failed
```

吞没/封卡相关改动全绿后再同步 prod。

---

## 6. 相关文档

- 审计：`docs/phase1-card-audit.md`
- 回归：`docs/phase2_regression.py`
- 架构：`ARCHITECTURE.md`
