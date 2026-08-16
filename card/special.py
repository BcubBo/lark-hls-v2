# =================================================================
# lark-hls-v2 · card/special.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：特殊用途卡片构建器 — cron定时推送、gateway内部消息、clarify澄清交互
# ② 技术栈：飞书 CardKit 2.0 JSON schema
# ③ 依赖：card/elements.py（_escape_md, build_card_header）、card/md.py（优化/降级）、card/i18n.py
# ④ 给谁看：改卡片样式、新增特殊卡片类型的开发者
# ▍文件从上到下的结构
# ① clarify 选项标准化（_normalize_choice → _extract_readable_from_dict → normalize_clarify_choices）
# ② build_cron_card — 极简静态卡片，仅 markdown
# ③ build_gateway_card — gateway 内部消息卡片（slash 命令、auth、session、error）
# ④ build_clarify_card — 三态澄清卡片：待选择 → 已提交 → 已确认
# ▍修改铁律
# 1. clarify 选项必须走 normalize_clarify_choices，别直接塞原始 choices — 用户输入五花八门，dict/list/乱码都可能来。
# 2. select_static 的 options 用 plain_text（不用 lark_md）— 飞书下拉框不渲染 markdown。
# 3. build_cron_card 的 summary 截断长度走 _def.SUMMARY_MAX_LENGTH，别硬编码。
# ▍更新记录
# *更新：2026-08-13 · 添加龙崎注释风格*
# =================================================================

from __future__ import annotations

import ast
from typing import Any

from .i18n import _LOCALES, _T, _i18n, _t
from .elements import _escape_md, build_card_header, _build_static_footer
from .md import (
    _MAX_CRON_TABLES,
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)

__all__ = [
    'build_cron_card',
    'build_gateway_card',
    'build_clarify_card',
    'build_clarify_submitted_card',
    'build_clarify_confirmed_card',
    'normalize_clarify_choices',
]


# ================================================================
# ▍clarify 选项标准化 — 防御 LLM 和用户的各种奇葩输入
# 输入可能是纯字符串、dict repr 字符串、真正的 dict、list、None。
# 全部收敛成干净的 str，超长截断。不改这里会出乱码或空白选项。
# ================================================================

_CLARIFY_DICT_FIELD_PRIORITY = (
    "label", "description", "text", "title",
    "name", "path", "value", "id",
)

_CLARIFY_MAX_CHOICE_LEN = 80

def _normalize_choice(choice: Any) -> str:
    """_normalize_choice()：契约
    入参：choice（Any）— 任意类型的选项原始值
    返回：str — 可读的选项文本，空字符串表示无效
    副作用：无
    谁调用：normalize_clarify_choices() 逐项调用
    改动影响：改字段优先级会改变 dict 类选项的显示文案
    """
    if choice is None:
        return ""
    if not isinstance(choice, str):
        if isinstance(choice, dict):
            return _extract_readable_from_dict(choice)
        if isinstance(choice, (list, tuple)):
            parts = [_normalize_choice(x) for x in choice]
            return " ".join(p for p in parts if p)[:_CLARIFY_MAX_CHOICE_LEN]
        choice = str(choice)

    text = choice.strip()
    if not text:
        return ""

    # ⚠️ dict repr 字符串：LLM 有时返回 "{'label': 'xxx'}" 这种
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            extracted = _extract_readable_from_dict(parsed)
            if extracted:
                text = extracted

    if len(text) > _CLARIFY_MAX_CHOICE_LEN:
        text = text[: _CLARIFY_MAX_CHOICE_LEN - 1] + "..."

    return text

def _extract_readable_from_dict(d: dict) -> str:
    """从 dict 中按优先级提取可读字段（只认字符串值）."""
    for field in _CLARIFY_DICT_FIELD_PRIORITY:
        val = d.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""

def normalize_clarify_choices(choices: list[str] | None) -> list[str]:
    """normalize_clarify_choices()：契约
    入参：choices（list[str] | None）— 原始选项列表
    返回：list[str] — 标准化后的选项，过滤空值
    副作用：无
    谁调用：build_clarify_card()、外部 adapter
    改动影响：改这里会影响所有 clarify 卡片的选项显示
    """
    if not choices:
        return []
    normalized = []
    for c in choices:
        n = _normalize_choice(c)
        if n:
            normalized.append(n)
    return normalized


# ================================================================
# ▍build_cron_card — 定时推送卡片
# markdown 内容 + 美化 header，无交互元素。
# ================================================================

def build_cron_card(content: str, *, title: str = "⏰ 定时任务", job_name: str = "") -> dict[str, Any]:
    """build_cron_card()：契约
    入参：content（str）— markdown 格式的推送内容；title（str）— header 标题；job_name（str）— 任务名称
    返回：dict — CardKit 2.0 schema 卡片 JSON
    副作用：无
    谁调用：controller._do_cron_deliver()
    改动影响：改 schema 版本会影响飞书渲染
    """
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"locales": _LOCALES},
        "header": build_card_header(title=title, template="green"),
        "body": {"elements": []},
    }
    if not content.strip():
        return card
    from ..config import defaults as _def
    summary = content[:_def.SUMMARY_MAX_LENGTH].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    for chunk in _split_long_text(_downgrade_tables(optimize_markdown_style(content), limit=_MAX_CRON_TABLES)):
        if chunk.strip():
            card["body"]["elements"].append({"tag": "markdown", "content": chunk})
    card["body"]["elements"].extend(_build_static_footer("定时任务", extra_info=job_name))
    return card


# ================================================================
# ▍build_gateway_card — gateway 内部消息卡片
# 用于 slash 命令回复、auth、session、error 等轻量场景。
# category 保留给 reaction routing 用。
# ================================================================

def build_gateway_card(content: str, *, category: str = "", status_label: str = "", status_emoji: str = "", title: str = "⚙️ 系统消息") -> dict[str, Any]:
    """build_gateway_card()：契约
    入参：content（str）— markdown 内容；category/status_label/status_emoji — 可选装饰；title（str）— header 标题
    返回：dict — CardKit 2.0 schema 卡片 JSON
    副作用：无
    谁调用：controller._do_gateway_deliver(), controller._do_gateway_card_update()
    改动影响：改卡片结构会影响所有 gateway 消息的外观
    """
    elements: list[dict] = []

    if status_label and status_emoji:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"{status_emoji} {status_label}",
                "text_color": "turquoise",
                "text_size": "notation",
            },
        })

    if content.strip():
        for chunk in _split_long_text(_downgrade_tables(optimize_markdown_style(content), limit=_MAX_CRON_TABLES)):
            if chunk.strip():
                elements.append({"tag": "markdown", "content": chunk})

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"locales": _LOCALES},
        "header": build_card_header(title=title, template="blue"),
        "body": {"elements": elements},
    }

    from ..config import defaults as _def
    summary = content[:_def.SUMMARY_MAX_LENGTH].replace("\n", " ").replace("```", "").strip() if content.strip() else ""
    if summary:
        card["config"]["summary"] = {"content": summary}
    card["body"]["elements"].extend(_build_static_footer("系统消息"))

    return card


# ================================================================
# ▍build_clarify 系列 — 三态澄清交互卡片
# 状态机：待选择(Pending) → 已提交(Soft Lock) → 已确认(Hard Lock)
# 每个状态对应一个独立的构建函数，卡片外观和交互元素不同。
# ================================================================

def build_clarify_card(*, question: str, choices: list[str] | None = None, clarify_id: str = "") -> dict[str, Any]:
    """build_clarify_card()：契约 — 待选择态(State 1: Pending)
    入参：question — 澄清问题；choices — 可选项列表；clarify_id — 唯一标识
    返回：dict — 含 select_static 下拉 + input 自由输入的交互卡片
    副作用：无
    谁调用：gateway clarify handler
    改动影响：改 behaviors 里的 callback value 会影响后端回调解析
    """
    elements: list[dict] = []

    elements.append({
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "info_outlined",
            "size": "20px 20px",
            "color": "blue",
        },
        "text": {
            "tag": "lark_md",
            "content": f"**{_escape_md(question)}**",
        },
    })

    # ⚠️ 防御性双重标准化：adapter 层也做，但卡片构建必须自保
    normalized_choices = normalize_clarify_choices(choices)

    if normalized_choices:
        option_lines = []
        for i, choice in enumerate(normalized_choices):
            label = chr(ord("A") + i) if i < 26 else str(i + 1)
            option_lines.append(f"{label}. {_escape_md(choice)}")
        options_md = "\n".join(option_lines)
        elements.append({
            "tag": "markdown",
            "content": options_md,
        })

        # ⚠️ select_static 用 plain_text（不用 lark_md）— 飞书下拉框不渲染 markdown
        options: list[dict] = []
        for i, choice in enumerate(normalized_choices):
            label = chr(ord("A") + i) if i < 26 else str(i + 1)
            options.append({
                "text": {"tag": "plain_text", "content": f"{label}. {choice}"},
                "value": str(i),
            })

        en_placeholder, zh_placeholder = _T["clarify_select_placeholder"]
        select_el: dict[str, Any] = {
            "tag": "select_static",
            "element_id": "clarify_select",
            "placeholder": {
                "tag": "plain_text",
                "content": zh_placeholder,
                "i18n_content": _i18n(en_placeholder, zh_placeholder),
            },
            "options": options,
            "behaviors": [{
                "type": "callback",
                "value": {
                    "hermes_clarify_action": "select",
                    "clarify_id": clarify_id,
                },
            }],
        }
        elements.append(select_el)

    en_input_ph, zh_input_ph = _T["clarify_input_placeholder"]
    input_el: dict[str, Any] = {
        "tag": "input",
        "element_id": "clarify_input",
        "placeholder": {
            "tag": "plain_text",
            "content": zh_input_ph,
            "i18n_content": _i18n(en_input_ph, zh_input_ph),
        },
        "max_length": 500,
        "name": "clarify_input",
        "behaviors": [{
            "type": "callback",
            "value": {
                "hermes_clarify_action": "input_submit",
                "clarify_id": clarify_id,
            },
        }],
    }
    elements.append(input_el)

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "header": build_card_header(title="需确认", template="blue"),
        "body": {"elements": elements},
    }
    return card

def build_clarify_submitted_card(*, question: str, selected: str, clarify_id: str = "") -> dict[str, Any]:
    """build_clarify_submitted_card()：契约 — 已提交态(State 2: Soft Lock)
    入参：question — 原始问题；selected — 用户选择；clarify_id — 唯一标识
    返回：dict — 含锁图标 + 已选择 + 重试按钮的静态卡片
    副作用：无
    谁调用：gateway clarify handler（用户提交后）
    改动影响：改 retry 按钮的 callback value 会影响重试逻辑
    """
    safe_selected = _escape_md(selected)
    en_selected, zh_selected = _T["clarify_selected"]
    en_sel_label = en_selected.format(safe_selected)
    zh_sel_label = zh_selected.format(safe_selected)

    en_submitted, zh_submitted = _T["clarify_submitted"]
    en_retry, zh_retry = _T["clarify_retry"]

    elements: list[dict] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "lock_outlined",
                "size": "20px 20px",
                "color": "orange",
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(question)}**",
            },
        },
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "lock_outlined",
                "size": "16px 16px",
                "color": "orange",
            },
            "text": {
                "tag": "lark_md",
                "content": zh_sel_label,
                "i18n_content": _i18n(en_sel_label, zh_sel_label),
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"*{en_submitted}*",
                "i18n_content": _i18n(f"*{en_submitted}*", f"*{zh_submitted}*"),
            },
        },
        {
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": zh_retry,
                    "i18n_content": _i18n(en_retry, zh_retry),
                },
                "type": "primary",
                "behaviors": [{
                    "type": "callback",
                    "value": {
                        "hermes_clarify_action": "retry_submit",
                        "clarify_id": clarify_id,
                    },
                }],
            }],
        },
    ]

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "header": build_card_header(title="已提交", template="blue"),
        "body": {"elements": elements},
    }
    card["body"]["elements"].extend(_build_static_footer("已提交"))
    return card

def build_clarify_confirmed_card(*, question: str, selected: str) -> dict[str, Any]:
    """build_clarify_confirmed_card()：契约 — 已确认态(State 3: Hard Lock)
    入参：question — 原始问题；selected — 最终选择
    返回：dict — 绿色确认图标 + 选择结果的只读卡片
    副作用：无
    谁调用：gateway clarify handler（确认完成后）
    改动影响：无交互元素，改这里只影响视觉
    """
    safe_selected = _escape_md(selected)
    en_selected, zh_selected = _T["clarify_selected"]
    en_sel_label = en_selected.format(safe_selected)
    zh_sel_label = zh_selected.format(safe_selected)

    en_confirmed, zh_confirmed = _T["clarify_confirmed"]

    elements: list[dict] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "resolve_filled",
                "size": "20px 20px",
                "color": "green",
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(question)}**",
            },
        },
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "resolve_filled",
                "size": "16px 16px",
                "color": "green",
            },
            "text": {
                "tag": "lark_md",
                "content": zh_sel_label,
                "i18n_content": _i18n(en_sel_label, zh_sel_label),
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": zh_confirmed,
                "i18n_content": _i18n(en_confirmed, zh_confirmed),
            },
        },
    ]

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "header": build_card_header(title="已确认", template="blue"),
        "body": {"elements": elements},
    }
    card["body"]["elements"].extend(_build_static_footer("已确认"))
    return card
