# =================================================================
# lark-hls-v2 · card/styles.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（固定四问，问完才算完）
# ① 干什么：卡片视觉样式的集中配置——颜色、字号、圆角、间距
# ② 技术栈：Python dataclass（frozen=True 不可变）
# ③ 依赖：无外部依赖，纯数据定义
# ④ 给谁看：需要统一卡片样式的渲染模块
# ▍文件从上到下的结构
# CardStyles dataclass：所有样式参数的容器（frozen，不可变）
# DEFAULT_STYLES：全局默认样式单例
# ▍修改铁律（都是血泪教训）
# 1. CardStyles 是 frozen 的——改值必须创建新实例，不能直接赋值。
# 2. 新增字段必须给默认值，否则所有现有调用方会报 TypeError。
# 3. 字段名要和 elements.py 里的使用点对齐，改名前先搜。
# ▍外号表
# "DEFAULT_STYLES" → 全局默认样式单例（创建后不改，只读）
# =================================================================
"""CardStyles — centralized style configuration for Feishu Card 2.0.

所有卡片视觉参数集中管理。
渲染模块通过 DEFAULT_STYLES 或 CardStyles(**overrides) 获取样式值。
"""

from __future__ import annotations
from dataclasses import dataclass, field

__all__ = ["CardStyles", "DEFAULT_STYLES"]


@dataclass(frozen=True)
class CardStyles:
    """卡片视觉样式参数容器。

    frozen=True 保证创建后不可变——改值必须新建实例。
    所有字段有默认值，可以通过 CardStyles(**overrides) 覆盖部分参数。

    改动影响：新增字段需要同步检查 elements.py 里是否有使用点。
    """

    # 分隔线颜色（灰色为默认，蓝色/绿色等用于特殊场景）
    divider_color: str = "grey"

    # 可折叠面板标题背景色（实际是 title_el 的 text_color）
    header_bg: str = "grey"

    # 面板边框颜色
    panel_border: str = "grey"

    # 字号：回答区用 normal_v2（正文），脚注用 notation（小字）
    font_size_answer: str = "normal_v2"
    font_size_footer: str = "notation"

    # 面板边框圆角
    panel_corner_radius: str = "5px"

    # 面板内部垂直间距
    panel_vertical_spacing: str = "4px"

    # 面板内边距（上 右 下 左）
    panel_padding: str = "8px 8px 8px 8px"


# ▍全局默认样式单例——创建后只读，不要尝试修改
DEFAULT_STYLES = CardStyles()
