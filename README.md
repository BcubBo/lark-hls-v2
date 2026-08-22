# lark-hls-v2 — 飞书 CardKit 流式卡片插件

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-%3E%3D0.20.0-blue.svg)](https://hermes-agent.nousresearch.com)

Hermes Agent 的飞书流式卡片插件。将 agent 的文本回复转为 CardKit 2.0 流式卡片，支持实时更新、动态台词、思考动画、工具调用面板。

## 功能

### 流式卡片
- 实时 token 流式更新（`stream_element` API）
- 思考过程展示（reasoning rounds + 耗时）
- 工具调用面板（折叠/展开）
- 封卡摘要（answer footer + 模型/耗时信息）
- 元素数量自动降级（表格→文本，防止超 200 上限）

### 动态台词系统
- Fisher-Yates 洗牌队列：一轮不重复，同源间隔 ≥ 5
- 场景检测：greeting / thinking / battle / victory / defeat / seal
- 语气词（panel 标题）+ 封印结束语
- 台词库：`card/quotes_data.json`

### 静态卡片
- Cron 卡片：定时任务输出，footer 显示任务名称
- Gateway 卡片：系统消息自动分类（auth/error/session/slash）
- Clarify 卡片：交互式选择

### Adapter 拦截
- `FeishuAdapter.send` 拦截：文本→卡片转换
- `FeishuAdapter.edit_message` 拦截：卡片更新
- `FeishuAdapter.send_clarify` 拦截：clarify 卡片
- 重复抑制：`_msg_ctx` 上下文 + 兜底卡片会话检查（`_sess_items_snapshot`）

### 中断处理
- 新消息中断旧卡片（abort + 新卡片继续）
- `/stop` 命令停止流式
- 子/父消息上下文切换

### 群成员管理（v2.9.0+）
- **自动入库**：群消息到达时自动将发送者写入 `feishu_users` + `group_members` 表
- **飞书 API 同步**：每 5 分钟拉取群成员列表，补齐 `feishu_role`（owner/admin/member）和 `chat_id`
- **按群隔离**：`group_members` 关联表精确匹配 `(open_id, chat_id)`，支持同一用户在不同群有不同角色
- **open_id↔user_id 自动关联**：同名记录自动互绑 `linked_id`
- **新成员感知**：全量同步时自动发现并入库新成员
- **延迟初始化**：纯私聊场景不创建数据库文件，首次群消息时才创建
- **限流保护**：每个 chat_id 每 5 分钟最多同步一次，失败后 10 分钟重试

### 用户权限系统
- **系统角色注入**：`admin:何博洋` 格式注入 `source.user_name`，AI 可直接识别权限
- **SQLite 缓存**：`feishu_users` 表缓存 open_id→name 映射，contact API 失败时 fallback
- **三级优先级**：`manual > auto > api`，手动设置不被自动覆盖

## 文件结构

```
lark-hls-v2/
├── plugin.yaml                 # 插件元数据
├── __init__.py                 # 版本 + apply_patches 入口
├── controller.py               # StreamCardController（卡片生命周期管理）
├── card_flow.py                # 流式卡片核心：flush/finalize/complete
├── card/
│   ├── builder.py              # CardKit schema 构建
│   ├── elements.py             # header/footer/loading/error 元素
│   ├── quotes.py               # 动态台词系统（Fisher-Yates 洗牌）
│   ├── quotes_data.json        # 台词库
│   ├── special.py              # cron/gateway/clarify 静态卡片
│   ├── md.py                   # Markdown 优化
│   └── quotes/                 # 台词资源目录
├── interceptors/
│   ├── __init__.py             # 共享状态 + patch 管理
│   ├── adapter.py              # FeishuAdapter 方法拦截
│   ├── gateway.py              # GatewayRunner 方法包装
│   ├── hooks.py                # 群成员自动入库 + 角色注入 + 钩子
│   ├── callbacks.py            # 流式回调（delta/thinking/tool）
│   └── hermes_compat.py        # Hermes 版本兼容
├── feishu/
│   ├── client.py               # 飞书 API 客户端
│   ├── user_cache.py           # 用户缓存 + group_members 关联表
│   └── guard.py                # 错误码判断
├── flush/
│   └── controller.py           # Flush 控制器
├── state/
│   ├── session.py              # CardSession 状态管理
│   └── ...                     # 其他状态模块
├── config/
│   ├── defaults.py             # 默认配置
│   └── schema.py               # 配置 schema
├── aowen/
│   └── __init__.py             # 管理命令（/help /status /reset 等）
└── README.md
```

**规模：35 个 Python 文件，~18,000 行代码**

## 数据库

### feishu_users 表
```sql
CREATE TABLE feishu_users (
    open_id     TEXT PRIMARY KEY,    -- 飞书 open_id（ou_xxx）
    name        TEXT NOT NULL,       -- 显示名
    feishu_role TEXT DEFAULT 'member', -- 飞书群内角色（已废弃，迁移到 group_members）
    role        TEXT NOT NULL DEFAULT 'member', -- 系统权限：admin/moderator/member
    permissions TEXT DEFAULT '{}',   -- JSON 权限字典
    source      TEXT NOT NULL,       -- 来源：manual/auto/api
    linked_id   TEXT,                -- 关联的另一种 ID 格式
    chat_id     TEXT,                -- 旧字段（已废弃，迁移到 group_members）
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### group_members 表（v2.9.0+）
```sql
CREATE TABLE group_members (
    open_id     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    feishu_role TEXT DEFAULT 'member', -- owner/admin/member
    joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (open_id, chat_id)
);
```

## 安装

```bash
# 克隆到 Hermes 插件目录
git clone https://github.com/BcubBo/lark-hls-v2.git \
  ~/.hermes/profiles/<profile>/plugins/lark-hls-v2

# 重启 Gateway
hermes gateway restart --profile <profile>
```

## 配置

在 `config.yaml` 的 `plugins.lark_hls_v2` 下：

```yaml
plugins:
  lark_hls_v2:
    enabled: true
    dynamic_quotes_enabled: true      # 动态台词（默认 true）
    streaming_panel_expanded: false   # 工具面板默认折叠
    gateway_cards: true               # 系统消息转卡片
```

## Cron 卡片

定时任务输出自动转为 CardKit 卡片，footer 显示任务名称：

```
📌 定时任务 · 服务器健康巡检 · 14:30
```

## 已知限制

- 飞书 CardKit 2.0 单卡片元素上限 200 个
- 流式更新频率受飞书 API 限流（~5 次/秒）
- 表格渲染在元素过多时自动降级为文本

## 许可证

MIT License

## 作者

boyang
