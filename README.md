# lark-hls-v2

飞书（Lark）CardKit v2.0 流式卡片插件，为 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 提供实时 AI 回复的流式展示效果。

> 本项目 fork 自 [hermes-lark-streaming](https://github.com/NousResearch/hermes-agent/tree/main/plugins/hermes-lark-streaming)（作者：[Aowen-Nowor](https://github.com/Aowen-Nowor)），在其基础上进行了深度重构、功能扩展和个性化定制。向原作者的开创性工作致敬。

---

## 功能特性

### 核心

- 🎨 **流式卡片** — 实时打字效果，逐字输出 AI 回复
- 🧠 **推理面板** — 可折叠面板展示 AI 思考过程和工具调用，text_tag 彩色徽章（轮数蓝色、工具数紫色）
- 📊 **Footer 信息** — 3 行布局：状态/耗时/模型/API 调用、token/上下文/缓存/偏移、成本/上下文溢出
- 🌐 **中英双语** — 完整 i18n 支持，飞书自动切换语言
- ⚡ **智能节流** — 可配置刷新间隔（70-2000ms），打字速度曲线（flat/answer_fast）
- 🛡️ **健壮容错** — 卡片不可用时自动降级为文本回复

### v2.2.0 新增

- 🏷️ **Card Header** — 卡片顶部彩色横幅，标题加粗，支持 12 种背景色模板
  - 流式卡片：橙色 header，显示「**阿玛特拉斯**」
  - Clarify 卡片：蓝色 header，按状态显示「**需确认**」/「**已提交**」/「**已确认**」
- 📌 **text_tag 徽章** — 面板标题中轮数（蓝色）和工具数（紫色）用彩色标签显示
- 💬 **quote_block** — 引用块组件，高亮关键信息
- ➖ **彩色分割线** — 支持 12 种颜色的 `<hr>` 分割线
- 🎛️ **配置补全** — 33 个可配置项，全部支持 config.yaml 覆盖

### 代码质量

- 🧹 **P0 修复** — 裸 except 全部补日志，消除静默失败
- 🔧 **P1 修复** — 31 处 `(ImportError, Exception)` 反模式修复
- 🗑️ **死代码清理** — 删除 lifecycle/ 目录、未使用函数、废弃 i18n key、重复常量
- 📏 **魔法数字提取** — `SUMMARY_MAX_LENGTH`、`_DRAIN_ROUNDS_MAX` 等提取为常量

---

## 安装

### 前置条件

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 已安装并运行
- 飞书应用已配置（App ID + App Secret）

### 安装步骤

1. 克隆到 Hermes 插件目录：

```bash
cd ~/.hermes/profiles/bo/plugins/
git clone https://github.com/BcubBo/lark-hls-v2.git
```

2. 在 `config.yaml` 中启用插件：

```yaml
plugins:
  enabled:
    - lark-hls-v2
  disabled:
    - hermes-lark-streaming  # 禁用旧版（如有）

lark_hls_v2:
  # ── 插件核心 ──
  enabled: true
  linear: true
  gateway_cards: true
  # ── 流式/打印 ──
  print_strategy: delay        # "fast" 或 "delay"
  print_step: 5                # 打字速度（1-10 字符/tick）
  flush_interval_ms: 180       # 流式更新间隔（70-2000ms）
  card_ttl_sec: 600            # 卡片存活时间（秒）
  speed_curve: flat            # "flat" 或 "answer_fast"
  answer_fast_stream_ms: 150   # 回答阶段加速间隔
  # ── Panel 面板 ──
  panel_expanded: true
  streaming_panel_expanded: false
  auto_collapse_threshold: 10
  panel_border_color: green    # grey/blue/green/orange/red
  panel_header_color: green    # grey/blue/green/orange/red
  # ── 推理 ──
  show_reasoning: true
  max_tool_steps: 20
  max_reasoning_rounds: 20
  # ── 个性化文本 ──
  panel_title: "Agent"
  loading_text: "正在准备..."
  thinking_text: "思考中..."
  # ── Footer ──
  footer:
    show_label: true
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, cache, history_offset]
      - [cost, compression_exhausted]
  # ── Card Header（顶部横幅）──
  card_header:
    title: "Agent"
    subtitle: ""                    # 留空则不显示
    icon: info_outlined             # 飞书标准图标 token
    template: orange                # 背景色（12 种可选）
```

3. 重启 Gateway：

```bash
systemctl --user restart hermes-gateway-bo.service
```

---

## 完整配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 启用插件 |
| `linear` | `true` | 线性模式（单卡片流式） |
| `gateway_cards` | `true` | Gateway 卡片投递 |
| `print_strategy` | `delay` | 打字策略："fast" 或 "delay" |
| `print_step` | `5` | 每次输出字符数（1-10） |
| `flush_interval_ms` | `180` | 流式更新间隔（70-2000ms） |
| `card_ttl_sec` | `600` | 卡片存活时间（秒） |
| `speed_curve` | `flat` | 打字速度曲线："flat" 或 "answer_fast" |
| `answer_fast_stream_ms` | `150` | 回答阶段加速间隔 |
| `panel_expanded` | `true` | 面板默认是否展开 |
| `streaming_panel_expanded` | `true` | 流式时面板是否展开 |
| `auto_collapse_threshold` | `10` | 子元素超此数自动折叠（0=不折叠） |
| `panel_border_color` | `green` | 面板边框颜色 |
| `panel_header_color` | `green` | 面板标题颜色 |
| `show_reasoning` | `true` | 是否显示推理过程 |
| `max_tool_steps` | `20` | 最大工具步数 |
| `max_reasoning_rounds` | `20` | 最大推理轮数 |
| `panel_title` | `agent loop` | 面板标题 |
| `loading_text` | `正在加载上下文...` | 加载提示文案 |
| `thinking_text` | `正在思考...` | 思考提示文案 |
| `footer.show_label` | `true` | 是否显示字段标签 |
| `footer.fields` | 3 行布局 | Footer 字段布局（2D 数组） |
| `card_header.title` | `""` | Card Header 标题 |
| `card_header.subtitle` | `""` | Card Header 副标题 |
| `card_header.icon` | `info_outlined` | Header 图标 token |
| `card_header.template` | `green` | Header 背景色（12 种可选） |

### Card Header 颜色模板

`blue` / `green` / `orange` / `red` / `purple` / `indigo` / `turquoise` / `yellow` / `grey` / `violet` / `wathet` / `carmine`

### 面板边框/标题颜色

`grey` / `blue` / `green` / `orange` / `red`

---

## 架构

```
lark-hls-v2/
├── __init__.py          # 插件入口
├── plugin.yaml          # 插件元数据
├── plugin/              # register/unregister
├── config/              # defaults.py + schema.py（配置单例）
│   ├── defaults.py      # 所有默认值的单一真相源
│   └── schema.py        # Config 单例，读 config.yaml → fallback defaults
├── controller.py        # 核心控制器（会话生命周期）
├── linear_mixin.py      # 卡片生命周期（创建→流式→密封）
├── card/                # 卡片渲染
│   ├── elements.py      # UI 元素构建器（panel/footer/header/quote/divider）
│   ├── builder.py       # 卡片组装
│   ├── special.py       # 特殊卡片（Gateway/Cron/Clarify）
│   ├── i18n.py          # 中英双语
│   ├── md.py            # Markdown 处理
│   └── styles.py        # 样式配置
├── interceptors/        # Monkey-patch 注入层
│   ├── gateway.py       # GatewayRunner 拦截
│   ├── adapter.py       # FeishuAdapter 拦截（消息发送抑制）
│   ├── callbacks.py     # Agent 流式回调
│   └── hooks.py         # Hook 函数
├── state/               # 状态管理
│   ├── phase.py         # 状态机
│   ├── session.py       # 会话数据
│   ├── linear.py        # 统一线性状态
│   ├── text.py          # 文本累积
│   └── tooluse.py       # 工具追踪
├── feishu/              # 飞书 API 客户端
├── flush/               # 节流控制器
└── aowen/               # /aowen 命令处理
```

---

## 开发

```bash
# 开发目录
cd /home/ubuntu/workspace/hermes-lark-streaming-v2

# 语法检查
python3 -m py_compile card/elements.py

# 部署到插件目录
rsync -av --delete --exclude='__pycache__' --exclude='.git' --exclude='tests/' \
  ./ ~/.hermes/profiles/bo/plugins/lark-hls-v2/

# 重启 Gateway
systemctl --user restart hermes-gateway-bo.service
```

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v2.2.0 | 2026-08-11 | Card Header（橙色/蓝色）、Clarify 卡片状态标题、text_tag 徽章、quote_block、彩色分割线、P0-P2 审计修复、配置补全 |
| v2.1.0 | 2026-08-10 | 重命名为 lark-hls-v2、代码审查、6 方法重命名、P0/P1 修复、v1.7.0 同步 |
| v2.0.0 | 2026-08-08 | 从 hermes-lark-streaming fork，初始重构 |

---

## 致谢

- **[hermes-lark-streaming](https://github.com/NousResearch/hermes-agent/tree/main/plugins/hermes-lark-streaming)** — 原始插件，作者 [Aowen-Nowor](https://github.com/Aowen-Nowor)。本项目在其架构和代码基础上进行了深度重构和扩展。没有原作者的开创性工作，就没有这个项目。
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — Nous Research
- **[飞书开放平台](https://open.feishu.cn/)** — CardKit 2.0 API

---

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。
