# =================================================================
# lark-hls-v2 · card/elements.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：CardKit 2.0 原始积木构建器 — panel、footer、loading、error 等基础元素
# ② 技术栈：飞书 CardKit 2.0 JSON schema，递归元素计数
# ③ 依赖：card/i18n.py、card/md.py（markdown 优化/降级/拆分）、config/defaults.py
# ④ 给谁看：改卡片元素样式、新增元素类型的开发者
# ▍文件从上到下的结构
# ① 常量区 — element ID、正则、图片 key
# ② 图片提取 — _extract_images_from_markdown（markdown img → Card 2.0 img 元素）
# ③ 基础积木 — _collapsible_panel / _streaming_element / _loading_* / _count_tag_objects
# ④ Panel 组装 — build_panel_header / build_panel_children / build_unified_panel
# ⑤ 工具/推理渲染 — _build_tool_step_* / _build_reasoning_round_title / _tool_status_info
# ⑥ 辅助渲染 — _format_code_block / _escape_md / _format_elapsed / _compact
# ⑦ 特殊面板 — _build_error_panel / _build_background_review_panel
# ⑧ Footer — _build_footer_elements / _render_footer_field
# ⑨ 顶层元素 — build_card_header / build_quote_block / build_colored_divider
# ⑩ 封卡 actions — build_seal_actions
# ▍修改铁律
# 1. element ID（STREAMING_ELEMENT_ID 等）是跨文件契约，改了 card_flow.py 和 builder.py 会全部炸。
# 2. _count_tag_objects 是 200 元素上限的核心检测，改递归逻辑会导致超限或误裁。
# 3. _collapsible_panel 的结构被 builder.py 和 card_flow.py 依赖，改 key 名会 schema error。
# 4. _render_footer_field 的返回值是 (en, zh) 元组，改格式会影响 footer 渲染。
# ▍外号表
# "loading hint" → _LOADING_HINT_ELEMENT_ID（"正在加载上下文..."占位元素）
# "loading icon" → _LOADING_ELEMENT_ID（底部旋转 loading）
# "panel" → UNIFIED_PANEL_ELEMENT_ID（推理+工具折叠面板）
#
# ▍更新记录
# *更新：2026-08-13 · 添加龙崎注释风格*
# =================================================================
"""CardKit v2.0 — Primitive element builders: panels, footers, helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .i18n import _LOCALES, _T, _i18n, _t
# ================================================================
# ▍markdown 优化依赖 — 表格降级、长文本拆分、样式优化
# ================================================================
from .md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)

# ================================================================
# ▍Config 驱动的颜色配置 — 面板边框和标题栏颜色
# 导入时从 defaults 读取（零开销），config.yaml 有覆盖时才走 Config。
# ================================================================
from ..config import defaults as _defaults

_panel_border_color: str = _defaults.PANEL_BORDER_COLOR
_panel_header_color: str = _defaults.PANEL_HEADER_COLOR

def _reload_panel_colors() -> None:
    """Reload panel colors from Config (called on config reload)."""
    global _panel_border_color, _panel_header_color
    try:
        from ..config import Config
        cfg = Config()
        _panel_border_color = cfg.panel_border_color
        _panel_header_color = cfg.panel_header_color
    except Exception:
        pass

# Apply config overrides on first import (non-blocking, cached)
_reload_panel_colors()

__all__ = [
    'STREAMING_ELEMENT_ID',
    'ANSWER_ELEMENT_ID',
    'UNIFIED_PANEL_ELEMENT_ID',
    '_LOADING_ELEMENT_ID',
    '_LOADING_HINT_ELEMENT_ID',
    '_LOADING_IMG_KEY',
    '_IMG_MD_PATTERN',
    '_extract_images_from_markdown',
    '_collapsible_panel',
    '_streaming_element',
    '_loading_element',
    '_loading_hint_element',
    '_loading_hint_thinking_element',
    '_build_tool_step_elements',
    '_build_tool_step_title',
    '_build_reasoning_round_title',
    '_build_tool_step_detail',
    '_build_tool_step_output',
    '_tool_status_info',
    '_format_code_block',
    '_longest_backtick_run',
    '_escape_md',
    '_build_error_panel',
    '_build_background_review_panel',
    '_build_footer_elements',
    'build_seal_actions',
    '_render_footer_field',
    '_build_static_footer',
    '_compact',
    '_format_elapsed',
    '_build_unified_panel_placeholder',
    'build_unified_panel',
    'build_panel_header',
    'build_panel_children',
    '_count_tag_objects',
    'build_card_header',
    'build_quote_block',
    'build_colored_divider',
]

# ================================================================
# ▍正则常量 — 图片提取、多空行清理、反引号检测、markdown 特殊字符转义
# ================================================================
_IMG_MD_PATTERN = re.compile(r"!\[([^\]]*)\]\((img_[^)\s]+)\)")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
_RE_BACKTICK_RUN = re.compile(r"`+")
_RE_MD_SPECIAL = re.compile(r"([`*_{}\[\]<>])")

def _extract_images_from_markdown(text: str) -> tuple[str, list[dict]]:
    """提取飞书图片为独立 Card 2.0 img 元素，返回 (清理后的文本, img元素列表)."""
    images: list[dict] = []

    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        img_key = m.group(2)
        images.append({
            "tag": "img",
            "img_key": img_key,
            "scale_type": "fit_horizontal",
            "alt": {"tag": "plain_text", "content": alt},
            "corner_radius": "8px",
            "preview": True,
        })
        return ""

    cleaned = _IMG_MD_PATTERN.sub(_replace, text)
    cleaned = _RE_MULTI_NEWLINE.sub("\n\n", cleaned).strip()
    return cleaned, images

if TYPE_CHECKING:
    from ..state.linear import ReasoningRound

# ================================================================
# ▍Element ID 常量 — ★核心★ 跨文件契约，改了全炸
# card_flow.py / builder.py / controller.py 都依赖这些 ID
# ================================================================
STREAMING_ELEMENT_ID = "streaming_content"
ANSWER_ELEMENT_ID = "answer_content"
UNIFIED_PANEL_ELEMENT_ID = "agent_process_panel"
_LOADING_ELEMENT_ID = "loading_icon"
_LOADING_HINT_ELEMENT_ID = "context_loading_hint"
_LOADING_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

# ================================================================
# ▍_count_tag_objects — 递归计数含 tag 键的 JSON 对象
# 飞书 Card 2.0 的 200 元素上限检测核心，被 builder.py 和 card_flow.py 调用。
# 【不】改递归逻辑 — 会导致超限误判或漏判。
# ================================================================
def _count_tag_objects(obj: Any) -> int:
    """Recursively count JSON objects with tag key. Feishu Card 2.0 caps at 200 elements."""
    count = 0
    if isinstance(obj, dict):
        if "tag" in obj:
            count += 1
        for v in obj.values():
            count += _count_tag_objects(v)
    elif isinstance(obj, list):
        for item in obj:
            count += _count_tag_objects(item)
    return count

# ================================================================
# ▍_collapsible_panel — 可折叠面板积木
# 所有折叠面板（推理、工具、error、bg review）的基础结构。
# 返回的 dict 结构被 build_unified_panel / _build_error_panel 等依赖。
# ================================================================
def _collapsible_panel(
    *,
    expanded: bool,
    title_el: dict,
    elements: list[dict],
    vertical_spacing: str = "4px",
    icon_position: str = "right",
    border_color: str = "grey",
    header_color: str = "grey",
) -> dict:
    icon_el = {
        "tag": "standard_icon",
        "token": "down-small-ccm_outlined",
        "size": "16px 16px",
    }
    if icon_position == "right":
        icon_el["color"] = header_color
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": title_el,
            "vertical_align": "center",
            "icon": icon_el,
            "icon_position": icon_position,
            "icon_expanded_angle": -180,
        },
        "border": {"color": border_color, "corner_radius": "5px"},
        "vertical_spacing": vertical_spacing,
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    }

# ================================================================
# ▍流式元素 + Loading 元素 — 卡片的基础占位积木
# ================================================================
def _streaming_element(content: str = "", *, element_id: str = STREAMING_ELEMENT_ID) -> dict:
    return {
        "tag": "markdown",
        "content": content,
        "element_id": element_id,
        "text_size": "normal",
    }

def _loading_element() -> dict:
    """Loading spinner element (div with icon — div natively supports icon, markdown varies)."""
    return {
        "tag": "div",
        "icon": {
            "tag": "custom_icon",
            "img_key": _LOADING_IMG_KEY,
            "size": "16px 16px",
        },
        "text": {
            "tag": "plain_text",
            "content": " ",
        },
        "element_id": _LOADING_ELEMENT_ID,
    }

def _loading_hint_element() -> dict:
    """上下文加载占位元素 — 首卡创建后插入，首字即显时删除."""
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "time_outlined",
            "size": "16px 16px",
        },
        "text": {
            "tag": "lark_md",
            "content": _T["loading_context"][0],
            "i18n_content": _t("loading_context"),
        },
        "element_id": _LOADING_HINT_ELEMENT_ID,
    }


def _loading_hint_thinking_element() -> dict:
    """思考阶段加载占位元素 — context已加载但首个token未到时使用."""
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "time_outlined",
            "size": "16px 16px",
        },
        "text": {
            "tag": "lark_md",
            "content": _T["thinking"][0],
            "i18n_content": _t("thinking"),
        },
        "element_id": _LOADING_HINT_ELEMENT_ID,
    }

# ================================================================
# ▍Unified Panel 占位 — 首卡的空 panel，首字即显时才填充内容
# ================================================================
def _build_unified_panel_placeholder(*, expanded: bool = False) -> dict:
    """Build empty unified panel placeholder for initial streaming card."""
    en_title, zh_title = _T["agent_process"]
    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": en_title,
            "i18n_content": _i18n(en_title, zh_title),
            "text_size": "notation",
        },
        elements=[{"tag": "markdown", "content": " "}],
        border_color=_panel_border_color,
        header_color=_panel_header_color,
    )
    panel["element_id"] = UNIFIED_PANEL_ELEMENT_ID
    return panel

# ================================================================
# ▍Panel Header / Children / Unified Panel — ★核心★ 面板组装三件套
# build_panel_header → 标题（轮次/工具数/耗时）
# build_panel_children → 子元素（推理/工具步骤，时间线或顺序渲染）
# build_unified_panel → 组装完整折叠面板
# ================================================================
def build_panel_header(*, reasoning_rounds: list, current_reasoning_text: str = "", tool_steps: list[dict], tool_elapsed_ms: float = 0, show_reasoning: bool = True, panel_quote: str = "") -> dict:
    """Build header dict for unified panel. Title computed from state (rounds/tools/elapsed)."""
    en_title, zh_title = _T["agent_process"]
    # Use dynamic quote as title prefix if provided, otherwise use default
    if panel_quote:
        en_parts: list[str] = [panel_quote]
        zh_parts: list[str] = [panel_quote]
    else:
        en_parts: list[str] = [en_title]
        zh_parts: list[str] = [zh_title]

    has_reasoning = show_reasoning and (
        reasoning_rounds or current_reasoning_text
    )
    num_rounds = len(reasoning_rounds) + (1 if current_reasoning_text else 0)

    if has_reasoning and num_rounds > 0:
        en_rounds, zh_rounds = _T["rounds"]
        en_parts.append(en_rounds.format(f'<text_tag color="blue">{num_rounds}</text_tag>'))
        zh_parts.append(zh_rounds.format(f'<text_tag color="blue">{num_rounds}</text_tag>'))

    if tool_steps:
        en_tools, zh_tools = _T["tools_count"]
        en_parts.append(en_tools.format(f'<text_tag color="purple">{len(tool_steps)}</text_tag>'))
        zh_parts.append(zh_tools.format(f'<text_tag color="purple">{len(tool_steps)}</text_tag>'))

    reasoning_elapsed_ms = sum(r.elapsed_ms for r in reasoning_rounds)
    total_elapsed_ms = reasoning_elapsed_ms + tool_elapsed_ms
    if total_elapsed_ms > 0 and (has_reasoning or tool_steps):
        elapsed_str = _format_elapsed(total_elapsed_ms)
        en_parts.append(elapsed_str)
        zh_parts.append(elapsed_str)

    en_full = " · ".join(en_parts)
    zh_full = " · ".join(zh_parts)

    title_el = {
        "tag": "lark_md",
        "content": en_full,
        "i18n_content": _i18n(en_full, zh_full),
        "text_size": "notation",
    }

    # Header structure (matches _collapsible_panel; icon_position=right → grey).
    icon_el = {
        "tag": "standard_icon",
        "token": "down-small-ccm_outlined",
        "size": "16px 16px",
        "color": "grey",
    }
    return {
        "title": title_el,
        "vertical_align": "center",
        "icon": icon_el,
        "icon_position": "right",
        "icon_expanded_angle": -180,
    }

# ⚠️ 单条推理文本最大显示 2000 字 — 超长推理会导致卡片渲染卡顿
_REASONING_DISPLAY_LIMIT = 2000

def _truncate_reasoning(text: str) -> str:
    """截断过长推理文本至 _REASONING_DISPLAY_LIMIT."""
    if len(text) <= _REASONING_DISPLAY_LIMIT:
        return text
    suffix = "\n\n... (已截断，共 {} 字)".format(len(text))
    return text[:_REASONING_DISPLAY_LIMIT - len(suffix)] + suffix



def build_panel_children(*, reasoning_rounds: list, current_reasoning_text: str = "", tool_steps: list[dict], show_reasoning: bool = True, panel_events: list[tuple[str, int]] | None = None, max_tool_steps: int = 20, max_reasoning_rounds: int = 20, reasoning_batch_size: int = 10) -> list[dict]:
    """Build child elements for unified panel body. Renders chronologically (panel_events)
    or sequentially (fallback). Trims to max_* limits (Feishu 200-element cap).
    Reasoning rounds are rendered flat (title div + markdown), no nested panels."""
    trimmed_rounds = 0
    trimmed_tools = 0

    if len(reasoning_rounds) > max_reasoning_rounds:
        trimmed_rounds = len(reasoning_rounds) - max_reasoning_rounds
        reasoning_rounds = reasoning_rounds[-max_reasoning_rounds:]

    if len(tool_steps) > max_tool_steps:
        trimmed_tools = len(tool_steps) - max_tool_steps
        tool_steps = tool_steps[-max_tool_steps:]

    num_rounds = len(reasoning_rounds) + (1 if current_reasoning_text else 0)

    # Filter panel_events to match trimmed items (offset mapping).
    if panel_events and (trimmed_rounds > 0 or trimmed_tools > 0):
        round_offset = trimmed_rounds
        tool_offset = trimmed_tools
        filtered_events: list[tuple[str, int]] = []
        for kind, idx in panel_events:
            if kind == "reasoning":
                if idx >= round_offset:
                    filtered_events.append((kind, idx - round_offset))
            elif kind == "tool":
                if idx >= tool_offset:
                    filtered_events.append((kind, idx - tool_offset))
        panel_events = filtered_events if filtered_events else None

    children: list[dict] = []

    if trimmed_rounds > 0 or trimmed_tools > 0:
        collapse_parts: list[str] = []
        if trimmed_rounds > 0:
            collapse_parts.append(f"{trimmed_rounds} 轮早期推理")
        if trimmed_tools > 0:
            collapse_parts.append(f"{trimmed_tools} 步早期操作")
        collapse_text = "⚡ 还有 " + "、".join(collapse_parts) + "已折叠"
        children.append({
            "tag": "markdown",
            "content": collapse_text,
            "text_size": "notation",
        })

    if panel_events:
        rendered_tools: set[int] = set()

        for kind, idx in panel_events:
            if kind == "reasoning" and show_reasoning and idx < len(reasoning_rounds):
                r = reasoning_rounds[idx]
                title_div = _build_reasoning_round_title(r.index, r.elapsed_ms, finalized=True)
                children.append(title_div)
                preview = r.text.strip() if r.text.strip() else " "
                children.append({"tag": "markdown", "content": _truncate_reasoning(preview), "text_size": "notation"})
            elif kind == "tool" and idx < len(tool_steps):
                if idx not in rendered_tools:
                    step = tool_steps[idx]
                    children.extend(_build_tool_step_elements(step))
                    rendered_tools.add(idx)

        # In-progress reasoning.
        if current_reasoning_text and show_reasoning:
            in_progress_idx = num_rounds
            title_div = _build_reasoning_round_title(in_progress_idx, 0, finalized=False)
            children.append(title_div)
            children.append({"tag": "markdown", "content": _truncate_reasoning(current_reasoning_text), "text_size": "notation"})

        # Remaining tool steps not in panel_events.
        for i, step in enumerate(tool_steps):
            if i not in rendered_tools:
                children.extend(_build_tool_step_elements(step))

    else:
        # No timeline, render sequentially.
        if show_reasoning:
            for r in reasoning_rounds:
                title_div = _build_reasoning_round_title(r.index, r.elapsed_ms, finalized=True)
                children.append(title_div)
                preview = r.text.strip() if r.text.strip() else " "
                children.append({"tag": "markdown", "content": _truncate_reasoning(preview), "text_size": "notation"})
            if current_reasoning_text:
                in_progress_idx = num_rounds
                title_div = _build_reasoning_round_title(in_progress_idx, 0, finalized=False)
                children.append(title_div)
                children.append({"tag": "markdown", "content": _truncate_reasoning(current_reasoning_text), "text_size": "notation"})

        # Tool steps.
        for step in tool_steps:
            children.extend(_build_tool_step_elements(step))

    if not children:
        children.append({"tag": "markdown", "content": " "})

    return children

def build_unified_panel(*, reasoning_rounds: list, current_reasoning_text: str = "", tool_steps: list[dict], tool_elapsed_ms: float = 0, show_reasoning: bool = True, expanded: bool = False, element_id: str | None = None, panel_events: list[tuple[str, int]] | None = None, max_tool_steps: int = 20, max_reasoning_rounds: int = 20, auto_collapse_threshold: int = 0, panel_quote: str = "", reasoning_batch_size: int = 10) -> dict:
    """Build full unified panel. Thin assembler over build_panel_header/children."""
    header = build_panel_header(
        reasoning_rounds=reasoning_rounds,
        current_reasoning_text=current_reasoning_text,
        tool_steps=tool_steps,
        tool_elapsed_ms=tool_elapsed_ms,
        show_reasoning=show_reasoning,
        panel_quote=panel_quote,
    )
    children = build_panel_children(
        reasoning_rounds=reasoning_rounds,
        current_reasoning_text=current_reasoning_text,
        tool_steps=tool_steps,
        show_reasoning=show_reasoning,
        panel_events=panel_events,
        max_tool_steps=max_tool_steps,
        max_reasoning_rounds=max_reasoning_rounds,
        reasoning_batch_size=reasoning_batch_size,
    )
    
    # Auto-collapse if threshold exceeded
    if auto_collapse_threshold > 0 and len(children) > auto_collapse_threshold:
        expanded = False
    
    panel = {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": header,
        "border": {"color": _panel_border_color, "corner_radius": "5px"},
        "vertical_spacing": "4px",
        "padding": "8px 8px 8px 8px",
        "elements": children,
    }
    panel["element_id"] = element_id or UNIFIED_PANEL_ELEMENT_ID
    return panel

# ================================================================
# ▍工具步骤渲染 — 单个工具步骤的标题/详情/输出组装
# ================================================================
def _build_tool_step_elements(step: dict) -> list[dict]:
    elements: list[dict] = [_build_tool_step_title(step)]
    detail = _build_tool_step_detail(step)
    if detail:
        elements.append(detail)
    output = _build_tool_step_output(step)
    if output:
        elements.append(output)
    return elements

def _build_tool_step_title(step: dict) -> dict:
    status = step.get("status", "running")
    status_info = _tool_status_info(status)
    title = step.get("title", step.get("name", "tool"))
    content = f"<font color='{status_info['color']}'>**{_escape_md(title)}**</font>"
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": step.get("icon", "tool_02"),
            "color": "grey",
        },
        "text": {
            "tag": "lark_md",
            "content": content,
            "text_size": "notation",
        },
    }

def _build_reasoning_round_title(round_index: int, elapsed_ms: float, finalized: bool, failed: bool = False) -> dict:
    """构建推理轮次标题 div. Colors: 进行中 orange-300, 已完成 green, 失败 red."""
    if failed:
        color = "red"
    elif finalized:
        color = "green"
    else:
        color = "orange-300"

    en_label, zh_label = _T["round_n"]
    text = zh_label.format(round_index)  # 用中文格式
    elapsed = _format_elapsed(elapsed_ms) if elapsed_ms > 0 else ""
    if elapsed:
        text += f" · {elapsed}"

    content = f"<font color='{color}'>**{text}**</font>"
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "time_outlined",
            "size": "16px 16px",
            "color": color,
        },
        "text": {
            "tag": "lark_md",
            "content": content,
            "text_size": "notation",
        },
    }

def _build_tool_step_detail(step: dict) -> dict | None:
    detail = step.get("detail", "").strip()
    if not detail:
        return None
    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "plain_text",
            "content": detail,
            "text_size": "notation",
        },
    }

def _build_tool_step_output(step: dict) -> dict | None:
    error_block = step.get("error_block")
    result_block = step.get("result_block")

    lines: list[str] = []
    if error_block:
        lines.append("**Error**")
        lines.append(
            error_block.get("fenced")
            or _format_code_block(error_block.get("content", ""), error_block.get("language", "text"))
        )
    elif result_block:
        lines.append("**Result**")
        lines.append(
            result_block.get("fenced")
            or _format_code_block(result_block.get("content", ""), result_block.get("language", "json"))
        )

    if not lines:
        return None

    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "lark_md",
            "content": "\n".join(lines),
            "text_size": "notation",
        },
    }

# ================================================================
# ▍辅助渲染函数 — 状态颜色、代码块、markdown 转义
# ================================================================
def _tool_status_info(status: str) -> dict[str, str]:
    return {
        "running": {"label": "", "color": "orange-300"},
        "success": {"label": "", "color": "green"},
        "error": {"label": "", "color": "red"},
    }.get(status, {"label": "", "color": "grey"})

def _format_code_block(content: str, language: str) -> str:
    normalized = content.replace("\r\n", "\n").strip()
    fence = "`" * max(3, _longest_backtick_run(normalized) + 1)
    return f"{fence}{language}\n{normalized}\n{fence}"

def _longest_backtick_run(value: str) -> int:
    matches = _RE_BACKTICK_RUN.findall(value)
    return max((len(m) for m in matches), default=0)

def _escape_md(value: str) -> str:
    return _RE_MD_SPECIAL.sub(r"\\\1", value.replace("\\", "\\\\"))

# ================================================================
# ▍特殊面板 — 错误面板 / 后台审查面板
# error panel 用红色边框，interrupt panel 用橙色边框。
# ================================================================
def _build_error_panel(error_message: str, *, is_aborted: bool = False, expanded: bool = True, card_trace_id: str = "") -> dict:
    """Build collapsible error/interrupt panel. Error: red border. Interrupt: orange."""
    if is_aborted:
        en_label, zh_label = _T["interrupt_panel"]
        border_color = "orange"
        body_content = error_message
        body_i18n = None  # 中断消息无需 i18n
    else:
        en_label, zh_label = _T["error_panel"]
        border_color = "red"
        friendly_en = "AI encountered an error while replying. Please try again."
        friendly_zh = "AI 回复时出现错误，请重试。"
        if card_trace_id:
            friendly_en += f"\n\nDebug ID: `{card_trace_id}`"
            friendly_zh += f"\n\n调试 ID: `{card_trace_id}`"
            friendly_en += "\n\nIf this keeps happening, report the Debug ID to the developer."
            friendly_zh += "\n\n如果反复出错，请把调试 ID 反馈给开发者。"

        tech_detail = error_message.strip() if error_message else ""
        if tech_detail:
            body_content = f"{friendly_zh}\n\n---\n**技术详情**\n```\n{tech_detail}\n```"
            body_content_en = f"{friendly_en}\n\n---\n**Technical Details**\n```\n{tech_detail}\n```"
            body_i18n = _i18n(body_content_en, body_content)
        else:
            body_content = friendly_zh
            body_i18n = _i18n(friendly_en, friendly_zh)

    markdown_el: dict[str, Any] = {
        "tag": "markdown",
        "content": body_content,
        "text_size": "notation",
    }
    if body_i18n is not None:
        markdown_el["i18n_content"] = body_i18n

    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": en_label,
            "i18n_content": _i18n(en_label, zh_label),
            "text_size": "notation",
        },
        elements=[markdown_el],
        vertical_spacing="8px",
    )
    panel["border"]["color"] = border_color
    return panel

def _build_background_review_panel(messages: list[str], *, expanded: bool = True, element_id: str | None = None) -> dict[str, Any]:
    """构建后台审查进度面板."""
    en_title, zh_title = _T["bg_review_panel"]
    children: list[dict] = []
    for msg in messages:
        children.append({
            "tag": "markdown",
            "content": msg,
        })
    if not messages:
        children.append({"tag": "markdown", "content": " "})
    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": en_title,
            "i18n_content": _i18n(en_title, zh_title),
            "text_size": "notation",
        },
        elements=children,
    )
    if element_id:
        panel["element_id"] = element_id
    return panel

# ================================================================
# ▍Footer 渲染 — 卡片底部状态栏（状态/耗时/token/模型等）
# footer_fields 是二维列表，内层列表的字段用 " · " 连接。
# ================================================================
def _build_footer_elements(
    footer_data: dict | None,
    is_error: bool = False,
    is_aborted: bool = False,
    fields: list[list[str]] | None = None,
    show_label: bool = False,
) -> list[dict]:
    if fields is None:
        fields = [["status", "elapsed", "context", "model"]]

    data = footer_data or {}
    en_lines: list[str] = []
    zh_lines: list[str] = []
    for row in fields:
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for field in row:
            en, zh = _render_footer_field(field, data, is_error, is_aborted, show_label)
            if en:
                en_parts.append(en)
                if zh:
                    zh_parts.append(zh)
        if en_parts:
            en_lines.append(" · ".join(en_parts))
            zh_lines.append(" · ".join(zh_parts))

    if not en_lines:
        return []

    en_content = "\n".join(en_lines)
    zh_content = "\n".join(zh_lines)
    if is_error:
        en_content = f'<text_tag color="red">{en_content}</text_tag>'
        zh_content = f'<text_tag color="red">{zh_content}</text_tag>'

    return [
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": en_content,
            "i18n_content": _i18n(en_content, zh_content),
            "text_size": "notation",
        },
    ]

# ================================================================
# ▍顶层元素构建器 — header / quote_block / colored_divider
# ================================================================
def build_card_header(
    *,
    title: str = "", 
    subtitle: str = "",
    icon_token: str = "",
    template: str = "green",
) -> dict:
    """Card-level header (top banner above body elements).

    Returns a ``header`` dict for the top-level card JSON.
    Feishu renders this as a colored banner with icon + title + subtitle.
    """
    if not title:
        title = _defaults.CARD_HEADER_TITLE
    title_el: dict[str, Any] = {
        "tag": "lark_md",
        "content": f"**{title}**",
        "i18n_content": _i18n(f"**{title}**", f"**{title}**"),
    }
    header: dict[str, Any] = {
        "title": title_el,
        "template": template,
    }
    if icon_token:
        header["icon"] = {
            "tag": "standard_icon",
            "token": icon_token,
        }
    if subtitle:
        header["subtitle"] = {
            "tag": "plain_text",
            "content": subtitle,
            "i18n_content": _i18n(subtitle, subtitle),
        }
    return header


def build_quote_block(content: str, *, i18n: bool = False) -> dict:
    """CardKit 2.0 quote_block — a highlighted quotation/aside.

    Renders as a left-bordered, tinted background block.
    Useful for key conclusions, warnings, or highlighted notes.
    """
    el: dict[str, Any] = {
        "tag": "quote",
        "text": {
            "tag": "plain_text",
            "content": content,
        },
    }
    if i18n:
        el["text"]["i18n_content"] = _i18n(content, content)
    return el


def build_colored_divider(*, color: str = "grey") -> dict:
    """CardKit 2.0 colored hr divider.

    Supported colors: blue, green, orange, red, purple, indigo,
    turquoise, yellow, grey, violet, wathet, carmine.
    Default is a standard grey divider.
    """
    if color == "grey":
        return {"tag": "hr"}
    return {
        "tag": "hr",
        "color": color,
    }


# ================================================================
# ▍build_seal_actions — ★核心★ 封卡 batch_update actions 构建
# 插入 error panel + footer → 删除 loading_hint + loading_icon。
# existing_elements 过滤已删除的元素，避免重复删除报错。
# ================================================================
def build_seal_actions(*, partial: bool = False, footer_data: dict | None = None, is_error: bool = False, is_aborted: bool = False, error_message: str = "", footer_fields: list[list[str]] | None = None, footer_show_label: bool = False, existing_elements: set[str] | None = None, card_trace_id: str = "", footer_before_panel: bool = False) -> list[dict]:
    """构建保留式封卡 batch_update actions. Inserts error panel + footer via insert_before
    loading_icon, then deletes loading_hint + loading_icon. existing_elements filters deletes."""
    actions: list[dict] = []


    def _elem_exists(eid: str) -> bool:
        return existing_elements is None or eid in existing_elements

    # Error/interrupt panel.
    if error_message:
        actions.append({
            "action": "add_elements",
            "params": {
                "type": "insert_before",
                "target_element_id": _LOADING_ELEMENT_ID,
                "elements": [_build_error_panel(
                    error_message, is_aborted=is_aborted, expanded=True,
                    card_trace_id=card_trace_id,
                )],
            },
        })

    # Background review panel.
    bg_review_messages = footer_data.get("bg_review_messages") if footer_data else None
    if bg_review_messages:
        actions.append({
            "action": "add_elements",
            "params": {
                "type": "insert_before",
                "target_element_id": _LOADING_ELEMENT_ID,
                "elements": [_build_background_review_panel(
                    bg_review_messages,
                    expanded=True,
                )],
            },
        })

    # Partial indicator or footer.
    if partial:
        en_text, zh_text = _T["partial_continues"]
        partial_elements = [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"▸ {en_text} ↩",
                "i18n_content": _i18n(f"▸ {en_text} ↩", f"▸ {zh_text} ↩"),
            },
        ]
        actions.append({
            "action": "add_elements",
            "params": {
                "type": "insert_before",
                "target_element_id": UNIFIED_PANEL_ELEMENT_ID if footer_before_panel else _LOADING_ELEMENT_ID,
                "elements": partial_elements,
            },
        })
    else:
        footer_elements = _build_footer_elements(
            footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
        if footer_elements:
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": UNIFIED_PANEL_ELEMENT_ID if footer_before_panel else _LOADING_ELEMENT_ID,
                    "elements": footer_elements,
                },
            })

    # Delete loading hint (may remain if sealed before answer arrived).
    if _elem_exists(_LOADING_HINT_ELEMENT_ID):
        actions.append({
            "action": "delete_elements",
            "params": {
                "element_ids": [_LOADING_HINT_ELEMENT_ID],
            },
        })

    # Delete loading icon.
    if _elem_exists(_LOADING_ELEMENT_ID):
        actions.append({
            "action": "delete_elements",
            "params": {
                "element_ids": [_LOADING_ELEMENT_ID],
            },
        })

    return actions

def _render_footer_field(
    name: str,
    data: dict,
    is_error: bool,
    is_aborted: bool,
    show_label: bool,
) -> tuple[str | None, str | None]:
    """_render_footer_field()：契约
    入参：name — 字段名（status/elapsed/model/tokens/context/api_calls/...）；data — footer 数据
    返回：tuple[str|None, str|None] — (英文, 中文)，None 表示该字段不显示
    副作用：无
    谁调用：_build_footer_elements() 逐字段调用
    改动影响：改返回格式会影响所有 footer 行的渲染
    """
    if name == "status":
        if is_error:
            en, zh = _T["status_error"]
            return f'<text_tag color="red">{en}</text_tag>', f'<text_tag color="red">{zh}</text_tag>'
        if is_aborted:
            en, zh = _T["status_stopped"]
            return f'<text_tag color="orange">{en}</text_tag>', f'<text_tag color="orange">{zh}</text_tag>'
        en, zh = _T["status_completed"]
        return f'<text_tag color="green">{en}</text_tag>', f'<text_tag color="green">{zh}</text_tag>'

    if name == "elapsed":
        duration = data.get("duration", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            val = _format_elapsed(duration * 1000)
            if show_label:
                en_v, zh_v = _T["elapsed"][0].format(val), _T["elapsed"][1].format(val)
                return f'<text_tag color="green">{en_v}</text_tag>', f'<text_tag color="green">{zh_v}</text_tag>'
            return f'<text_tag color="green">{val}</text_tag>', f'<text_tag color="green">{val}</text_tag>'
        return None, None

    if name == "model":
        v = data.get("model") or None
        if v:
            return f'<text_tag color="blue">{v}</text_tag>', f'<text_tag color="blue">{v}</text_tag>'
        return None, None

    if name == "tokens":
        input_t = data.get("input_tokens", 0) or 0
        output_t = data.get("output_tokens", 0) or 0
        reasoning_t = data.get("reasoning_tokens", 0) or 0
        if input_t or output_t:
            v = f"↑ {_compact(input_t)} ↓ {_compact(output_t)}"
            if reasoning_t:
                v += f" 💭 {_compact(reasoning_t)}"
            return f'<text_tag color="indigo">{v}</text_tag>', f'<text_tag color="indigo">{v}</text_tag>'
        return None, None

    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if max_c:
            pct = int(used / max_c * 100)
            val = f"{_compact(used)}/{_compact(max_c)} ({pct}%)"
            if show_label:
                en_v, zh_v = _T["context"][0].format(val), _T["context"][1].format(val)
                return f'<text_tag color="orange">{en_v}</text_tag>', f'<text_tag color="orange">{zh_v}</text_tag>'
            return f'<text_tag color="orange">{val}</text_tag>', f'<text_tag color="orange">{val}</text_tag>'
        return None, None

    if name == "api_calls":
        v = data.get("api_calls", 0) or 0
        if v:
            en_val, zh_val = _T["api_calls"]
            if show_label:
                return f'<text_tag color="purple">{en_val} {v}</text_tag>', f'<text_tag color="purple">{zh_val} {v}</text_tag>'
            return f'<text_tag color="purple">{v}</text_tag>', f'<text_tag color="purple">{v}</text_tag>'
        return None, None

    if name == "history_offset":
        v = data.get("history_offset", 0) or 0
        if v:
            en_val, zh_val = _T["history_offset"]
            if show_label:
                return f'<text_tag color="grey">{en_val} {v}</text_tag>', f'<text_tag color="grey">{zh_val} {v}</text_tag>'
            return f'<text_tag color="grey">{v}</text_tag>', f'<text_tag color="grey">{v}</text_tag>'
        return None, None

    if name == "compression_exhausted":
        v = data.get("compression_exhausted", False)
        if v:
            en_val, zh_val = _T["compression_exhausted"]
            return en_val, zh_val
        return None, None

    if name == "cache":
        cache_read = data.get("cache_read_tokens", 0) or 0
        input_total = data.get("input_tokens", 0) or 0
        if cache_read and input_total:
            hit_pct = int(cache_read / input_total * 100)
            v = f"{_compact(cache_read)}/{_compact(input_total)} ({hit_pct}%)"
            if show_label:
                en_v, zh_v = _T["cache"][0].format(v), _T["cache"][1].format(v)
                return f'<text_tag color="wathet">{en_v}</text_tag>', f'<text_tag color="wathet">{zh_v}</text_tag>'
            return f'<text_tag color="wathet">{v}</text_tag>', f'<text_tag color="wathet">{v}</text_tag>'
        return None, None

    if name == "cost":
        cost_usd = data.get("estimated_cost_usd", 0) or 0
        cost_status = data.get("cost_status", "unknown")
        if cost_status == "included":
            return _T["cost_included"]
        if cost_status in ("actual", "estimated") and cost_usd:
            if cost_usd < 0.01:
                val = f"${cost_usd:.4f}"
            elif cost_usd < 1:
                val = f"${cost_usd:.3f}"
            else:
                val = f"${cost_usd:.2f}"
            key = "cost_actual" if cost_status == "actual" else "cost_estimated"
            en_val, zh_val = _T[key]
            if show_label:
                return f"Cost {en_val.format(val.lstrip('$'))}", f"费用 {zh_val.format(val.lstrip('$'))}"
            return en_val.format(val.lstrip('$')), zh_val.format(val.lstrip('$'))
        return None, None

    return None, None

def _compact(n: int) -> str:
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"{int(m)}M" if m >= 100 else f"{m:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

def _format_elapsed(ms: float) -> str:
    seconds = ms / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"


# ── 静态卡片 footer ──────────────────────────────────────────────────
def _build_static_footer(label: str, *, show_time: bool = True, extra_info: str = "") -> list[dict]:
    """静态卡片 footer — 简洁标记 + 时间戳 + 额外信息。

    入参：label（str）— 标签文本（如"定时任务""系统消息"）；show_time（bool）— 是否显示时间；extra_info（str）— 额外信息（如任务名称）
    返回：list[dict] — footer 元素列表（hr + markdown）
    副作用：无
    谁调用：build_cron_card / build_gateway_card / build_clarify_card 等
    改动影响：改格式会影响所有静态卡片的底部显示
    """
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    content = f"📌 {label}"
    if extra_info:
        content += f" · {extra_info}"
    if show_time:
        content += f" · {now}"
    return [
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": content,
            "text_size": "notation",
        },
    ]
