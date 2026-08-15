# lark-hls-v2

飞书（Lark）CardKit v2.0 流式卡片插件，为 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 提供实时 AI 回复的流式展示效果。

> 本项目 fork 自 [hermes-lark-streaming](https://github.com/NousResearch/hermes-agent/tree/main/plugins/hermes-lark-streaming)（作者：[Aowen-Nowor](https://github.com/Aowen-Nowor)），在其基础上进行了深度重构、功能扩展和个性化定制。向原作者的开创性工作致敬。

---

## 功能特性

### 核心

- 🎨 **流式卡片** — 实时打字效果，逐字输出 AI 回复
- 🧠 **推理面板** — 可折叠面板展示 AI 思考过程和工具调用，text_tag 彩色徽章
- 📊 **Footer 信息** — 3 行布局：状态/耗时/模型/API 调用、token/上下文/缓存/偏移、成本/上下文溢出
- 🌐 **中英双语** — 完整 i18n 支持，飞书自动切换语言
- ⚡ **智能节流** — 可配置刷新间隔（70-2000ms），打字速度曲线（flat/answer_fast）
- 🛡️ **健壮容错** — 卡片不可用时自动降级为文本回复

### Card Header

- 🏷️ **彩色横幅** — 卡片顶部标题栏，支持 12 种背景色模板
- 🎭 **动态台词** — 标题随场景自动切换二次元台词（544 条，57 部动漫，同源间隔保证）
  - 流式卡片与 Clarify 卡片各自独立配置
  - Clarify 卡片按状态显示不同标题（待确认/已提交/已确认）

### Panel 面板

- 📌 **text_tag 徽章** — 轮数蓝色、工具数紫色
- 🎭 **动态语气词** — 面板标题随场景切换短语气词（177 条）
  - 推理中："想想" / "灵感来了" / "有思路了"
  - 工具调用："冲啊" / "开干" / "火力全开"
  - 完成："搞定" / "拿下了" / "完美"
  - 封印时随机结束语（96 条）
- 💬 **quote_block** — 引用块组件，高亮关键信息
- ➖ **彩色分割线** — 支持 12 种颜色

### 代码质量

- 裸 except 全部补日志，消除静默失败
- 反模式修复，异常捕获规范化
- 死代码清理，废弃函数和未使用模块移除
- 魔法数字提取为命名常量

---

## 安装

### 前置条件

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 已安装并运行
- 飞书应用已配置（App ID + App Secret）

### 安装步骤

#### 1. 克隆到插件目录

> **⚠️ 注意：** Hermes 使用 profile 机制时，插件目录是 `~/.hermes/profiles/<name>/plugins/`，不是 `~/.hermes/plugins/`。用错目录会导致插件不加载。

先确认你的 profile 名称（通常在 `~/.hermes/profiles/` 下），然后克隆：

```bash
# 查看当前有哪些 profile
ls ~/.hermes/profiles/

# 克隆到你的 profile 的 plugins 目录（将 <profile> 替换为你的 profile 名）
cd ~/.hermes/profiles/<profile>/plugins/
git clone https://github.com/BcubBo/lark-hls-v2.git
```

如果你不确定用哪个 profile，可以用 `hermes config get profile` 或检查 systemd service 文件中的 `--profile` 参数。

#### 2. 禁用旧版流式卡片插件

如果之前用过 `hermes-lark-streaming`，必须禁用它，否则两个插件会冲突：

```yaml
# 在 config.yaml 中添加
plugins:
  disabled:
    - hermes-lark-streaming
```

> **⚠️ 两份 config.yaml 都要改！** Hermes 有全局和 profile 两份配置：
> - `~/.hermes/config.yaml`（全局）
> - `~/.hermes/profiles/<name>/config.yaml`（profile）
>
> 两处的 `plugins.disabled` 都要包含 `hermes-lark-streaming`，否则可能导致加载冲突。

#### 3. 添加插件配置

在 `config.yaml` 中添加 `lark_hls_v2` section（可选，不加则使用默认值）：

```yaml
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
  loading_text: "Loading..."
  thinking_text: "Thinking..."
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
    dynamic_quotes_enabled: true    # 动态台词开关
    dynamic_quotes_cooldown: 2.0    # 台词切换冷却（秒）
```

#### 4. 重启 Gateway

```bash
hermes gateway restart
```

#### 5. 验证安装

```bash
# 确认插件已加载（应看到 lark-hls-v2 在列表中）
hermes plugins list

# 检查日志无报错
grep -i "lark-hls-v2\|plugin.*load" ~/.hermes/profiles/<profile>/logs/gateway.log | tail -10
```

### 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| 插件没加载，日志无记录 | clone 到了 `~/.hermes/plugins/` 而非 profile 目录 | 移动到 `~/.hermes/profiles/<name>/plugins/` |
| 插件加载了但卡片还是旧版 | `hermes-lark-streaming` 没禁用 | 在两份 config.yaml 的 `plugins.disabled` 中添加 |
| 改了配置没生效 | 只改了全局或只改了 profile 的 config.yaml | 两份都要改，然后重启 gateway |
| `systemctl restart` 后 PID 没变 | systemd 没有真正重启进程 | 用 `kill -9 $(pgrep -f hermes)` 然后 `hermes gateway start` |

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
| `card_header.dynamic_quotes_enabled` | `true` | 动态台词开关 |
| `card_header.dynamic_quotes_cooldown` | `2.0` | 台词切换冷却（秒） |

---

## 动态台词系统

插件内置二次元台词库，根据对话场景自动切换卡片标题和面板标题。

### 工作原理

| 组件 | 效果 | 示例 |
|------|------|------|
| **Card Header** | 完整台词+角色+作品 | "说到做到，这就是我的忍道！ —— 鸣人「火影忍者」" |
| **Panel Title** | 短语气词+统计 | "冲啊 · 2 轮 · 用了 3 个工具 · 1.2s" |
| **Seal 结束语** | 封印时随机结束语 | "收工" / "溜了溜了" / "编不下去了" |

### 场景检测

| 场景 | 触发条件 | 示例 |
|------|----------|------|
| `greeting` | 新会话 | "来了" / "上吧" |
| `thinking` | 推理中 | "想想" / "灵感来了" |
| `battle` | 工具调用中 | "冲啊" / "开干" |
| `victory` | 完成 | "搞定" / "拿下了" |
| `defeat` | 出错 | "翻车了" / "寄了" |
| `eating` | 等待中 | "稍等" / "泡杯茶" |
| `casual` | 普通对话 | "嘛" / "嗯" |
| `seal` | 卡片封印 | "收工" / "溜了" / "臣告退" |

### 自定义台词

台词库文件：`card/quotes_data.json`

```json
{
  "scenes": {
    "greeting": [
      {"text": "台词原文", "character": "角色名", "source": "作品名"}],
    "thinking": [], "battle": [], "victory": [],
    "defeat": [], "eating": [], "casual": []
  },
  "mood_expressions": {
    "greeting": [], "thinking": [], "battle": [],
    "victory": [], "defeat": [], "eating": [], "casual": []
  },
  "seal_endings": []
}
```

- `scenes` — 完整台词，用于 Card Header（带角色和作品出处）
- `mood_expressions` — 短语气词，用于 Panel Title（2-4 字）
- `seal_endings` — 封印结束语（随机选取）
- 修改后 5 分钟内自动热重载，无需重启
- Card Header 台词自动保证同源间隔（同一部动漫的台词至少间隔 5 次才会再次出现）

---

## 颜色配置

### Card Header 背景色

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
├── card_flow.py      # 卡片生命周期（创建→流式→密封）
├── card/                # 卡片渲染
│   ├── elements.py      # UI 元素构建器（panel/footer/header/quote/divider）
│   ├── builder.py       # 卡片组装
│   ├── special.py       # 特殊卡片（Gateway/Cron/Clarify）
│   ├── quotes.py        # 动态台词系统（场景检测 + 随机抽取）
│   ├── quotes_data.json # 台词库（544 条台词 + 177 条语气词 + 96 条结束语）
│   ├── i18n.py          # 中英双语
│   ├── md.py            # Markdown 处理
│   └── styles.py        # 样式配置
├── interceptors/        # Monkey-patch 注入层
│   ├── gateway.py       # GatewayRunner 拦截
│   ├── adapter.py       # FeishuAdapter 拦截
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

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v2.9.0 | 2026-08-15 | feishu_role 修复（API缓存只写 feishu_role）、静态卡片统一 header+footer、/ping 等 gateway 消息自动检测 category、去掉 header 默认 icon |
| v2.7.0 | 2026-08-13 | linear_mixin.py → card_flow.py 重命名，streaming_panel_expanded 默认改为 false，新增 card_ttl_sec/max_tool_steps/max_reasoning_rounds 配置说明 |
| v2.6.0 | 2026-08-12 | 全量功能代码同步，部署目录与 GitHub 统一，README 重写 |
| v2.5.0 | 2026-08-12 | 台词库扩充至 1025 条（544 台词 + 177 语气词 + 96 结束语），新增死神/火影/转生史莱姆条目，修复 Unicode 乱码，同源间隔算法，header icon 可选化 |
| v2.3.0 | 2026-08-11 | 动态台词系统（Card Header 完整台词 + Panel 语气词 + Seal 结束语），323 条台词 + 107 条语气词 + 56 条结束语，7 种场景自动检测 |
| v2.2.0 | 2026-08-11 | Card Header、Clarify 卡片状态标题、text_tag 徽章、quote_block、彩色分割线、代码质量改进、配置补全 |
| v2.1.0 | 2026-08-10 | 重命名为 lark-hls-v2、代码审查、方法重命名、v1.7.0 同步 |
| v2.0.0 | 2026-08-08 | 从 hermes-lark-streaming fork，初始重构 |

---

## 致谢

- **[hermes-lark-streaming](https://github.com/NousResearch/hermes-agent/tree/main/plugins/hermes-lark-streaming)** — 原始插件，作者 [Aowen-Nowor](https://github.com/Aowen-Nowor)。本项目在其架构和代码基础上进行了深度重构和扩展。没有原作者的开创性工作，就没有这个项目。
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — Nous Research
- **[飞书开放平台](https://open.feishu.cn/)** — CardKit 2.0 API

---

## 许可证

[MIT License](LICENSE)
