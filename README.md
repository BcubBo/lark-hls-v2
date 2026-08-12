# lark-hls-v2

飞书（Lark）CardKit v2.0 流式卡片插件，为 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 提供实时 AI 回复的流式展示效果。

## 功能特性

- 🎨 **流式卡片** — 实时打字效果，逐字输出 AI 回复
- 🧠 **推理面板** — 可折叠面板展示 AI 思考过程和工具调用
- 📊 **Footer 信息** — 状态、耗时、模型、token 用量、API 调用、上下文占比等
- 🌐 **中英双语** — 完整 i18n 支持，飞书自动切换语言
- ⚡ **智能节流** — 100ms 刷新间隔，200ms 面板跳动优化
- 🔧 **高度可配置** — 颜色、文案、布局、行为全部通过 config.yaml 控制
- 🛡️ **健壮容错** — 卡片不可用时自动降级为文本回复

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
  panel_title: "Agent"
  panel_border_color: green
  panel_header_color: green
  loading_text: "正在准备..."
  thinking_text: "思考中..."
  panel_expanded: true
  streaming_panel_expanded: true
  show_reasoning: true
  flush_interval_ms: 180
  print_step: 5
  auto_collapse_threshold: 10
  footer:
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, cache, history_offset]
      - [cost, compression_exhausted]
    show_label: true
```

3. 重启 Gateway：

```bash
systemctl --user restart hermes-gateway-bo.service
```

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `panel_title` | `agent loop` | 面板标题 |
| `panel_border_color` | `grey` | 面板边框颜色 |
| `panel_header_color` | `grey` | 面板标题颜色 |
| `loading_text` | `正在加载上下文...` | 加载提示文案 |
| `thinking_text` | `正在思考...` | 思考提示文案 |
| `panel_expanded` | `false` | 面板默认是否展开 |
| `streaming_panel_expanded` | `false` | 流式时面板是否展开 |
| `show_reasoning` | `false` | 是否显示推理过程 |
| `flush_interval_ms` | `200` | 刷新间隔（毫秒） |
| `print_step` | `4` | 每次输出字符数 |
| `auto_collapse_threshold` | `0` | 子元素超此数自动折叠（0=不折叠） |
| `footer.fields` | 3行布局 | Footer 字段布局（2D 数组） |
| `footer.show_label` | `true` | 是否显示字段标签 |

## 架构

```
lark-hls-v2/
├── __init__.py          # 插件入口
├── plugin.yaml          # 插件元数据
├── plugin/              # register/unregister
├── config/              # defaults.py + schema.py（配置单例）
├── controller.py        # 核心控制器（会话生命周期）
├── linear_mixin.py      # 卡片生命周期（创建→流式→密封）
├── card/                # 卡片渲染
│   ├── elements.py      # UI 元素构建器
│   ├── footer.py        # Footer 渲染
│   ├── builder.py       # 卡片组装
│   ├── special.py       # 特殊卡片（Gateway/Cron/Clarify）
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
├── aowen/               # /aowen 命令处理
└── lifecycle/           # 生命周期管理（预留）
```

## 开发

```bash
# 开发目录
cd /home/ubuntu/workspace/hermes-lark-streaming-v2

# 语法检查
python3 -c "import py_compile; py_compile.compile('your_file.py', doraise=True)"

# 部署到插件目录
rsync -av --exclude='tests/' --exclude='__pycache__' ./ ~/.hermes/profiles/bo/plugins/lark-hls-v2/

# 重启 Gateway
systemctl --user restart hermes-gateway-bo.service
```

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。

## 致谢

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — Nous Research
- [飞书开放平台](https://open.feishu.cn/) — CardKit 2.0 API
- 原版 [hermes-lark-streaming](https://github.com/NousResearch/hermes-agent/tree/main/plugins) 插件
