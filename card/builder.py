# =================================================================
# lark-hls-v2 · card/builder.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：卡片组装器 — 将 elements 模块的原始积木拼装成完整卡片 JSON
# ② 技术栈：飞书 CardKit 2.0 schema，json 序列化
# ③ 依赖：card/elements.py（所有积木函数）、card/i18n.py
# ④ 给谁看：改卡片整体结构、调整元素组装顺序的开发者
# ▍文件从上到下的结构
# ① _FEISHU_ELEMENT_LIMIT / _ELEMENT_LIMIT_MARGIN — 飞书 200 元素上限常量
# ② _enforce_card_element_limit — 超限时裁剪 panel 子元素（从头部裁，加"已折叠"提示）
# ③ build_streaming_card_v2 — ★核心★ 流式卡片构建器，卡片生命周期的起点
# ▍修改铁律
# 1. build_streaming_card_v2 的元素顺序是契约 — panel → answer → loading_hint → loading_icon。
#    改顺序会导致 card_flow.py 的 _do_unified_flush 里 element_id 定位失败。
# 2. _enforce_card_element_limit 从头部裁剪（保留最新的推理/工具步骤），
#    【不】改成从尾部裁 — 用户关注的是最新状态。
# 3. streaming_config 的 print_frequency_ms/print_step/print_strategy 影响打字机效果，
#    别随便改值，改了用户体验会变。
# ▍更新记录
# *更新：2026-08-13 · 添加龙崎注释风格*
# =================================================================

from __future__ import annotations
import json

from typing import TYPE_CHECKING, Any

from .elements import (
    ANSWER_ELEMENT_ID,
    STREAMING_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    _LOADING_ELEMENT_ID,
    _LOADING_HINT_ELEMENT_ID,
    _build_footer_elements,
    _build_unified_panel_placeholder,
    _collapsible_panel,
    _count_tag_objects,
    _loading_element,
    _loading_hint_element,
    _streaming_element,
    build_unified_panel,
    build_card_header,
    build_quote_block,
    build_colored_divider,
)
from .i18n import _LOCALES, _T, _i18n, _t

if TYPE_CHECKING:
    from ..state.linear import ReasoningRound

__all__ = [
    'build_streaming_card_v2',
    '_enforce_card_element_limit',
]

# ⚠️ 飞书 Card 2.0 硬限制：所有含 tag 键的 JSON 对象（含嵌套）总数 ≤ 200。
# 超限会报 300312 schema error，卡片完全渲染失败。
_FEISHU_ELEMENT_LIMIT = 200

# 预留 5 个元素的余量，防止 seal 时 footer/error panel 导致刚好超限
_ELEMENT_LIMIT_MARGIN = 5


# ================================================================
# ▍_enforce_card_element_limit — 200 元素上限防护网
# 从 panel 头部裁剪旧元素，保留最新内容。添加"⚡ 还有 N 项已折叠"提示。
# ================================================================

def _enforce_card_element_limit(
    card: dict[str, Any],
    *,
    panel_element_id: str = UNIFIED_PANEL_ELEMENT_ID,
) -> dict[str, Any]:
    """_enforce_card_element_limit()：契约
    入参：card — 完整卡片 dict；panel_element_id — 要裁剪的 panel ID
    返回：dict — 裁剪后的卡片（原地修改 + 返回）
    副作用：修改 card 内嵌的 panel elements 列表
    谁调用：finalize_card() seal 阶段
    改动影响：改裁剪策略会影响用户看到的历史推理/工具步骤数量
    """
    threshold = _FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_MARGIN
    total = _count_tag_objects(card)
    if total <= threshold:
        import logging as _dbg_log
    return card

    # ── 定位 unified panel ──
    body = card.get("body", {})
    elements = body.get("elements", [])
    panel = None
    for elem in elements:
        if elem.get("element_id") == panel_element_id and elem.get("tag") == "collapsible_panel":
            panel = elem
            break

    if panel is None:
        # 无 panel — answer/footer 不可裁
        return card

    children: list[dict] = panel.get("elements", [])

    # ── 检查是否已有"已折叠"提示 ──
    hint_idx = None
    for i, child in enumerate(children):
        if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
            hint_idx = i
            break
    _HINT_TEMPLATE = {"tag": "markdown", "content": "⚡ 还有 0 项已折叠", "text_size": "notation"}
    _HINT_TAG_COUNT = _count_tag_objects(_HINT_TEMPLATE)  # 通常为 1
    if hint_idx is None:
        total += _HINT_TAG_COUNT  # 为新折叠提示预留空间

    # ── 从头部裁剪直到低于阈值 ──
    # ⚠️ 从头部裁（保留最新），【不】改成从尾部裁
    trimmed_count = 0
    while total > threshold and len(children) > 1:
        # 跳过折叠提示（如果在首位）
        first_content = children[0].get("content", "")
        remove_idx = 1 if isinstance(first_content, str) and first_content.endswith("已折叠") else 0
        removed = children.pop(remove_idx)
        total -= _count_tag_objects([removed])
        trimmed_count += 1

    if trimmed_count > 0:
        # 更新或添加折叠提示
        hint_idx = None
        for i, child in enumerate(children):
            if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
                hint_idx = i
                break
        if hint_idx is not None:
            old_hint = children[hint_idx]["content"]
            # 解析已有的折叠数量，累加新裁剪数
            existing_count = 0
            _idx = old_hint.find("项")
            if _idx > 0:
                _end = _idx
                while _end > 0 and old_hint[_end - 1] == ' ':
                    _end -= 1
                _start = _end
                while _start > 0 and old_hint[_start - 1].isdigit():
                    _start -= 1
                if _start < _end:
                    existing_count = int(old_hint[_start:_end])
            total_trimmed = existing_count + trimmed_count
            children[hint_idx]["content"] = f"⚡ 还有 {total_trimmed} 项已折叠"
        else:
            children.insert(0, {
                "tag": "markdown",
                "content": f"⚡ 还有 {trimmed_count} 项已折叠",
                "text_size": "notation",
            })

    # 回写 panel children
    panel["elements"] = children
    return card


# ================================================================
# ▍build_streaming_card_v2 — ★核心★ 流式卡片构建器
# 卡片生命周期 v1.0.2+：首卡创建 → 流式更新 → seal 封卡。
# 这里只负责首卡的 JSON 结构，后续更新由 card_flow.py 的 flush 驱动。
# ================================================================

def build_streaming_card_v2(
    *,
    tool_steps: list[dict] | None = None,
    elapsed_ms: float = 0,
    show_tool_use: bool = True,
    show_reasoning: bool = False,
    show_streaming_element: bool = True,
    streaming_panel_expanded: bool = True,
    print_strategy: str = "delay",
    print_step: int = 4,
    include_unified_panel: bool = True,
    include_loading_hint: bool = True,
    include_answer_element: bool = True,
    include_card_header: bool = True,
    card_header_title: str = "",
    card_header_subtitle: str = "",
    card_header_icon: str = "",
    card_header_template: str = "",
) -> dict[str, Any]:
    """build_streaming_card_v2()：契约
    入参：各 include_* 开关控制首卡包含哪些元素；print_* 控制打字机效果
    返回：dict — CardKit 2.0 schema 流式卡片 JSON
    副作用：无
    谁调用：card_flow._do_create_linear_card()
    改动影响：
      - 元素顺序是契约，改了 card_flow 的 element_id 定位会失败
      - include_unified_panel=False 用于首卡（panel 在首字即显时才插入）
      - streaming_config 影响用户看到的打字机速度
    """
    elements: list[dict] = []

    # ── 卡片级 header（彩色横幅 + 图标 + 标题）──
    header: dict[str, Any] | None = None
    if include_card_header:
        try:
            from ..config import defaults as _def
            header = build_card_header(
                title=card_header_title or _def.CARD_HEADER_TITLE,
                subtitle=card_header_subtitle,
                icon_token=card_header_icon if card_header_icon is not None else _def.CARD_HEADER_ICON,
                template=card_header_template or _def.CARD_HEADER_TEMPLATE,
            )
        except Exception:
            pass  # 优雅降级 — 没有 header 卡片也能用

    # ── Unified panel 占位（linear 模式 — reasoning+tools 共用一个 panel）──
    if include_unified_panel:
        elements.append(_build_unified_panel_placeholder(expanded=streaming_panel_expanded))

    # ── 流式回答元素 ──
    if show_streaming_element and include_answer_element:
        elements.append(_streaming_element(element_id=ANSWER_ELEMENT_ID))

    # ── Loading hint（"正在加载上下文..."，首字即显时删除）──
    if include_loading_hint:
        elements.append(_loading_hint_element())

    # ── Loading spinner ──
    elements.append(_loading_element())

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "streaming_config": {
                "print_frequency_ms": {"default": 70},
                "print_step": {"default": print_step},
                "print_strategy": print_strategy,
            },
            "locales": _LOCALES,
            "summary": {
                "content": _T["processing"][1],
                "i18n_content": _t("processing"),
            },
        },
        "body": {"elements": elements},
    }
    # ── 注入卡片级 header（彩色横幅）──
    if header is not None:
        card["header"] = header
    return card
