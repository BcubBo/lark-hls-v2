"""Footer rendering — standalone module extracted from elements.py.

FooterRenderer encapsulates all footer element construction so that
footer layout is fully independent of other card elements.
"""

from __future__ import annotations

from typing import Any

from .i18n import _i18n, _t, _T

# Import defaults for footer layout
from ..config.defaults import FOOTER_FIELDS, FOOTER_SHOW_LABEL

__all__ = [
    "FooterRenderer",
    "build_footer_elements",
    "render_footer_field",
]

# ── Formatting helpers (moved from elements.py) ──

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


def render_footer_field(
    name: str,
    data: dict,
    is_error: bool,
    is_aborted: bool,
    show_label: bool,
) -> tuple[str | None, str | None]:
    """Render a single footer field, returning (en, zh) or (None, None)."""
    if name == "status":
        if is_error:
            return _T["status_error"]
        if is_aborted:
            return _T["status_stopped"]
        return _T["status_completed"]

    if name == "elapsed":
        duration = data.get("duration", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            val = _format_elapsed(duration * 1000)
            if show_label:
                return _T["elapsed"][0].format(val), _T["elapsed"][1].format(val)
            return val, val
        return None, None

    if name == "model":
        v = data.get("model") or None
        return v, v

    if name == "tokens":
        input_t = data.get("input_tokens", 0) or 0
        output_t = data.get("output_tokens", 0) or 0
        reasoning_t = data.get("reasoning_tokens", 0) or 0
        if input_t or output_t:
            v = f"↑ {_compact(input_t)} ↓ {_compact(output_t)}"
            if reasoning_t:
                v += f" 💭 {_compact(reasoning_t)}"
            return v, v
        return None, None

    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if max_c:
            pct = int(used / max_c * 100)
            val = f"{_compact(used)}/{_compact(max_c)} ({pct}%)"
            if show_label:
                return _T["context"][0].format(val), _T["context"][1].format(val)
            return val, val
        return None, None

    if name == "api_calls":
        v = data.get("api_calls", 0) or 0
        if v:
            en_val, zh_val = _T["api_calls"]
            if show_label:
                return f"{en_val} {v}", f"{zh_val} {v}"
            return str(v), str(v)
        return None, None

    if name == "history_offset":
        v = data.get("history_offset", 0) or 0
        if v:
            en_val, zh_val = _T["history_offset"]
            if show_label:
                return f"{en_val} {v}", f"{zh_val} {v}"
            return str(v), str(v)
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
                return _T["cache"][0].format(v), _T["cache"][1].format(v)
            return v, v
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


def build_footer_elements(
    footer_data: dict | None,
    is_error: bool = False,
    is_aborted: bool = False,
    fields: list[list[str]] | None = None,
    show_label: bool = False,
) -> list[dict]:
    """Build footer HR + markdown elements for the card."""
    if fields is None:
        fields = list(FOOTER_FIELDS)

    data = footer_data or {}
    en_lines: list[str] = []
    zh_lines: list[str] = []
    for row in fields:
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for field in row:
            en, zh = render_footer_field(field, data, is_error, is_aborted, show_label)
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
        en_content = f"<font color='red'>{en_content}</font>"
        zh_content = f"<font color='red'>{zh_content}</font>"

    return [
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": en_content,
            "i18n_content": _i18n(en_content, zh_content),
            "text_size": "notation",
        },
    ]


class FooterRenderer:
    """Configurable footer renderer.

    Usage::

        renderer = FooterRenderer()  # uses config defaults
        elements = renderer.build(footer_data, is_error=False)
    """

    def __init__(
        self,
        fields: list[list[str]] | None = None,
        show_label: bool | None = None,
    ) -> None:
        self.fields = fields if fields is not None else list(FOOTER_FIELDS)
        self.show_label = show_label if show_label is not None else FOOTER_SHOW_LABEL

    def render_field(
        self,
        name: str,
        data: dict,
        is_error: bool = False,
        is_aborted: bool = False,
    ) -> tuple[str | None, str | None]:
        """Render a single footer field."""
        return render_footer_field(name, data, is_error, is_aborted, self.show_label)

    def build(
        self,
        footer_data: dict | None,
        *,
        is_error: bool = False,
        is_aborted: bool = False,
    ) -> list[dict]:
        """Build complete footer elements (HR + markdown)."""
        return build_footer_elements(
            footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            fields=self.fields,
            show_label=self.show_label,
        )
