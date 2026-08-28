# =================================================================
# lark-hls-v2 · card/i18n.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（固定四问，问完才算完）
# ① 干什么：飞书卡片的中英双语文本映射，所有 UI 文案的中央仓库
# ② 技术栈：Python dict + config reload 钩子
# ③ 依赖：config.defaults（启动默认值）、config.Config（运行时覆盖）
# ④ 给谁看：card 子包内所有需要 UI 文案的模块（elements、special 等）
# ▍文件从上到下的结构
# _T 字典：所有 UI 文案的中英双语映射（key → (en, zh) 元组）
# _i18n()：构建飞书 i18n_content 格式 {zh_cn: ..., en_us: ...}
# _t()：快捷写法，从 _T 取值并转 i18n 格式
# _reload_custom_texts()：config.yaml 热重载时覆写 _T 中的可自定义项
# ▍修改铁律（都是血泪教训）
# 1. _T 里的 key 必须和 _i18n() / _t() 的调用方保持一致，改 key 前先全局搜。
# 2. _reload_custom_texts() 会直接覆盖 _T 的值——config.yaml 里的 panel_title /
#    loading_text / thinking_text 优先级高于硬编码。改逻辑时注意"谁先谁后"。
# 3. _LOCALES 顺序是 [zh_cn, en_us]，飞书按第一个为默认语言，别改顺序。
# ▍外号表
# "_T" → 全局文案字典（所有 UI 文案的"总账本"）
# "_t()" → _T 的快捷取值函数
# =================================================================
"""飞书卡片 i18n — 中英双语文本映射.

所有卡片 UI 文案集中在这里管理。
改文案直接改 _T 字典；需要运行时自定义的项通过 config.yaml 覆盖。
"""

from __future__ import annotations

from ..config import defaults as _defaults

__all__ = [
    "_LOCALES",
    "_T",
    "_i18n",
    "_t",
]

# ▍飞书支持的语言列表——顺序决定默认语言（第一个 = zh_cn）
_LOCALES = ["zh_cn", "en_us"]

# ▍全局文案字典：key → (英文, 中文) 元组
# ⚠️ 新增 key 时，确保调用方用 _T["new_key"] 或 _t("new_key") 取值
_T: dict[str, tuple[str, str]] = {
    "status_completed": ("Completed", "已完成"),
    "status_error": ("Error", "出错"),
    "status_stopped": ("Stopped", "已停止"),
    "elapsed": ("Elapsed {}", "耗时 {}"),
    "context": ("{}", "{}"),
    "processing": ("Processing...", "处理中..."),
    "thought": ("Thought", "思考"),
    "thinking_panel": ("Thinking", "思考中"),
    "thought_for": ("Thought for {}", "思考了 {}"),
    "api_calls": ("API", "API"),
    "history_offset": ("{}", "{}"),
    "error_panel": ("Error", "错误信息"),
    "interrupt_panel": ("Interrupted", "中断信息"),
    "compression_exhausted": ("⚠ Context Full", "⚠ 上下文已满"),
    "cache": ("{}", "{}"),
    "bg_review_panel": ("Review", "审查"),
    "partial_continues": ("Continues in next message", "内容未完，继续在下一条消息"),
    # ── Context loading hint (first card only, removed on first token) ──
    "loading_context": ("Loading context...", "正在加载上下文..."),
    "thinking": ("Thinking...", "正在思考..."),
    # ── Clarify interactive card (three-state: pending / submitted / confirmed) ──
    "clarify_select_placeholder": ("Quick select...", "快速选择..."),
    "clarify_input_placeholder": ("Type your answer...", "请输入你的回答..."),
    "clarify_selected": ("Selected: {}", "已选择: {}"),
    "clarify_submitted": ("Submitted, awaiting confirmation...", "已提交，等待确认..."),
    "clarify_retry": ("Retry submission", "重试提交"),
    "clarify_confirmed": ("Confirmed", "已确认"),
    "cost_estimated": ("${} (est.)", "${} (估算)"),
    "cost_actual": ("${} (actual)", "${} (实报)"),
    "cost_included": ("Free", "免费"),
    # ── Card-level header ──
    "card_header_title": ("Amateras", "阿玛特拉斯"),
    "card_header_streaming": ("Thinking...", "思考中..."),
    "card_header_completed": ("Completed", "已完成"),
    "card_header_error": ("Error", "出错"),
    "card_header_stopped": ("Stopped", "已停止"),
    # ── Unified panel i18n ──
    "agent_process": ("精神分裂了", "精神分裂了"),
    "rounds": ("{} rounds", "{} 轮"),
    "tools_count": ("{} tools", "{}个工具"),
    "round_n": ("Round {}", "第 {} 轮"),
}


def _i18n(en: str, zh: str) -> dict[str, str]:
    """构建飞书 i18n_content 对象。

    入参：en（英文字符串）、zh（中文字符串）
    返回：{zh_cn: zh, en_us: en} 字典
    副作用：无
    改动影响：所有卡片元素的 i18n_content 都走这里，改格式会全局影响
    """
    return {"zh_cn": zh, "en_us": en}


def _t(key: str) -> dict[str, str]:
    """从 _T 字典快捷取值并转 i18n 格式。

    入参：key（_T 字典的 key）
    返回：_i18n(*_T[key]) 的结果
    副作用：无
    谁调用：elements.py、special.py 等需要多语言文案的地方
    改动影响：如果 key 不存在会抛 KeyError，新增文案时确保 _T 里有对应 key
    """
    return _i18n(*_T[key])


# ▍config 热重载钩子——config.yaml 变化时调用，覆写 _T 中可自定义的项
def _reload_custom_texts() -> None:
    """从 Config 重新加载自定义文案（config reload 时调用）。

    config.yaml 里的 panel_title / loading_text / thinking_text 会覆盖 _T 的硬编码值。
    改这里会改变"用户自定义 vs 硬编码"的优先级关系。
    """
    try:
        from ..config import Config
        cfg = Config()

        # Always override _T from config (config takes precedence over hardcoded _T)
        panel_title = cfg.panel_title
        if panel_title:
            _T["agent_process"] = (panel_title, panel_title)

        loading_text = cfg.loading_text
        if loading_text:
            _T["loading_context"] = (loading_text, loading_text)

        thinking_text = cfg.thinking_text
        if thinking_text:
            _T["thinking"] = (thinking_text, thinking_text)

    except Exception:
        pass


# 模块加载时执行一次热重载，确保 config.yaml 的自定义值生效
_reload_custom_texts()
