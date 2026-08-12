"""CardStyles — centralized style configuration for Feishu Card 2.0."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CardStyles", "DEFAULT_STYLES"]


@dataclass(frozen=True)
class CardStyles:
    """Visual style parameters for card rendering.

    All fields have sensible defaults. Override via CardStyles(**overrides).
    """

    # Divider / separator
    divider_color: str = "grey"

    # Collapsible panel header background (text_color of title_el)
    header_bg: str = "grey"

    # Panel border color
    panel_border: str = "grey"

    # Font sizes
    font_size_answer: str = "normal_v2"
    font_size_footer: str = "notation"

    # Panel border corner radius
    panel_corner_radius: str = "5px"

    # Panel vertical spacing
    panel_vertical_spacing: str = "4px"

    # Panel padding
    panel_padding: str = "8px 8px 8px 8px"


DEFAULT_STYLES = CardStyles()
