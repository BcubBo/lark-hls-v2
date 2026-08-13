# =================================================================
# lark-hls-v2 · card/md.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（固定四问，问完才算完）
# ① 干什么：Markdown 文本预处理——标题降级、表格降级、图片 key 剥离、长文本分块、星号转义
# ② 技术栈：Python re（预编译正则）
# ③ 依赖：无外部依赖，纯文本处理
# ④ 给谁看：elements.py（渲染前调用）、special.py（cron/gateway 卡片构建）
# ▍文件从上到下的结构
# 常量区：降级阈值、分块上限、预编译正则
# _find_tables_outside_code_blocks()：定位代码块外的表格
# _downgrade_tables()：超限表格降级为代码块
# _strip_invalid_image_keys()：移除非 img_ 前缀的图片引用
# escape_markdown_asterisks()：保护合法 markdown 格式，转义飞书会误配对的星号
# optimize_markdown_style()：标题降级 + 代码块保护 + 空行压缩 + 图片剥离
# _split_long_text()：超长文本按段落/换行拆分为多块
# ▍修改铁律（都是血泪教训）
# 1. 正则全部预编译——这些函数在流式渲染时被高频调用，每次 re.compile 会拖慢。
#    新增正则必须放在模块顶部预编译区。
# 2. escape_markdown_asterisks() 用 \\x00 作占位符——还原后必须清掉残留的 \\x00，
#    否则飞书会显示方块字符（□）。改逻辑时先通读整个函数。
# 3. _downgrade_tables() 的 limit 参数区分流式（20）和静态（5），
#    飞书 Card 2.0 单卡硬限是 200 个元素，表格多了会超限。
# ▍外号表
# "降级" → 把表格塞进代码块，飞书不渲染为表格但内容可见
# "占位符" → \\x00PnPx00 格式的临时标记，保护代码/粗体/斜体不被转义
# =================================================================
"""Markdown 文本处理 — 标题降级、表格降级、图片 key 剥离、长文本分块.

这些函数在卡片渲染前对 Markdown 做预处理，适配飞书 Card 2.0 的限制：
- 表格数量有上限（流式 20，静态 5）
- 标题层级 H1-H3 需要降级（飞书渲染不出大标题）
- 星号会被飞书激进配对（2*4000+4*3000 这种数学表达式会被吃掉）
"""

from __future__ import annotations

import logging
import re

_logger = logging.getLogger("lark_hls_v2")

# ▍常量区
_MAX_CARD_TABLES = 20  # 流式卡片：20表降级阈值（流式增量内容，飞书宽松执行）
_MAX_CRON_TABLES = 5   # 静态卡片：5表降级阈值（飞书 Card 2.0 单卡硬限）
_MAX_CHUNK_CHARS = 2400

# ▍预编译正则——高频调用，不能在函数内 re.compile
# v1.3.0 新增保护占位符模式，避免代码块/粗体/斜体被误转义
_RE_FENCED_CODE = re.compile(r'```[\s\S]*?```')
_RE_INLINE_CODE = re.compile(r'`[^`]+`')
_RE_BOLD = re.compile(r'\*{2,3}(?!\s)((?:(?!\*{2,3}).)+?)(?<!\s)\*{2,3}', re.DOTALL)
_RE_VALID_ITALIC = re.compile(r'(?<![a-zA-Z0-9_])\*(?!\s)((?:(?!\*).)+?)(?<!\s)\*', re.DOTALL)
_RE_UNPAIRED_ASTERISK = re.compile(r'(?<!\\)\*(?=[^\s*])')
_RE_TABLE_ROW = re.compile(r"\|.+\|\n\|[-:| ]+\|[\s\S]*?(?=\n\n|\n(?!\|)|$)")
_RE_IMAGE_REF = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_RE_CODE_BLOCK_EXTRACT = re.compile(r"(^|\n)(`{3,})([^\n]*)\n[\s\S]*?\n\2(?=\n|$)")
_RE_H1_TO_H3 = re.compile(r"^#{1,3} ", re.MULTILINE)
_RE_HEADING_DEMOTE = re.compile(r"^#{2,6} (.+)$", re.MULTILINE)
_RE_H1_DEMOTE = re.compile(r"^# (.+)$", re.MULTILINE)
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
_RE_SHORT_MD_CHECK = re.compile(r'^#{1,6} |\n#{1,6} |```|!\[|\n{3,}')
# v1.3.0: placeholder pattern for restoring protected code/bold/italic blocks
_RE_PROTECTED_PLACEHOLDER = re.compile(r'\x00P(\d+)P\x00')

__all__ = [
    "_MAX_CRON_TABLES",
    "_downgrade_tables",
    "_find_tables_outside_code_blocks",
    "_split_long_text",
    "_strip_invalid_image_keys",
    "escape_markdown_asterisks",
    "optimize_markdown_style",
]


def _find_tables_outside_code_blocks(text: str) -> list[tuple[int, int, str]]:
    """查找代码块外的 markdown 表格，返回 [(start, end, raw), ...]。

    为什么：飞书会把代码块里的表格也当真表格渲染，需要先排除。
    改了会怎样：如果 _in_code 判断有误，表格会被重复降级或漏降级。
    """
    code_ranges: list[tuple[int, int]] = []
    for m in _RE_FENCED_CODE.finditer(text):
        code_ranges.append((m.start(), m.end()))

    def _in_code(idx: int) -> bool:
        return any(s <= idx < e for s, e in code_ranges)

    results: list[tuple[int, int, str]] = []
    for m in _RE_TABLE_ROW.finditer(text):
        if not _in_code(m.start()):
            results.append((m.start(), m.end(), m.group(0)))
    return results


def _downgrade_tables(text: str, limit: int = _MAX_CARD_TABLES) -> str:
    """超限表格降级为代码块（保留内容可见但飞书不渲染为表格元素）。

    入参：text（原始 markdown）、limit（允许的最大表格数）
    返回：处理后的 markdown 文本
    副作用：无
    谁调用：elements.py 的 answer 渲染、special.py 的 cron/gateway 卡片
    改动影响：降低 limit 会让更多表格变成代码块，用户看到的不是表格而是纯文本
    """
    # Early return: no tables possible without pipe characters
    if '|' not in text:
        return text
    matches = _find_tables_outside_code_blocks(text)
    if len(matches) <= limit:
        return text
    result = text
    for start, end, raw in reversed(matches[limit:]):
        replacement = f"```\n{raw}\n```"
        result = result[:start] + replacement + result[end:]
    return result


def _strip_invalid_image_keys(text: str) -> str:
    """移除非 img_ 前缀的图片引用。

    飞书只认 img_ 开头的图片 key，其他格式（如 URL）会被当错误处理。
    改了会怎样：如果放宽条件，飞书 API 可能返回 400。
    """
    if "![" not in text:
        return text

    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(2).startswith("img_") else ""

    return _RE_IMAGE_REF.sub(_replace, text)


def escape_markdown_asterisks(text: str) -> str:
    """保护合法 markdown 格式，转义飞书会误配对的星号。

    飞书 Markdown 解析器比 CommonMark 更激进——会把 2*4000+4*3000 这种
    数学表达式里的 * 当作斜体标记。本函数：
    1. 用 \\x00 占位符保护代码块、粗体、斜体
    2. 转义剩余的孤星号
    3. 还原占位符，清理残留 \\x00

    ⚠️ 占位符用 \\x00 是因为这个字符在正常文本中不会出现。
    改了会怎样：如果占位符还原逻辑出错，飞书会显示 □ 方块字符。
    """
    if '\x00' in text:
        text = text.replace('\x00', '')

    if '*' not in text:
        return text

    _protected: list[str] = []

    def _save(m: re.Match) -> str:
        _protected.append(m.group(0))
        return f'\x00P{len(_protected) - 1}P\x00'

    # Step 1: 保护代码区域（ fenced code + inline code ）
    text = _RE_FENCED_CODE.sub(_save, text)
    text = _RE_INLINE_CODE.sub(_save, text)

    # Step 2: 保护粗体 **...** 和 ***...***
    text = _RE_BOLD.sub(_save, text)

    text = _RE_VALID_ITALIC.sub(_save, text)

    # Step 4: 转义剩余 *（飞书可能误配对的）
    text = _RE_UNPAIRED_ASTERISK.sub(r'\\*', text)

    # ▍还原占位符——必须倒序，避免 P10P 匹配到 P1P
    if _protected:
        for i in range(len(_protected) - 1, -1, -1):
            text = text.replace(f'\x00P{i}P\x00', _protected[i])

    # Null bytes render as boxes (□) in Feishu and must never reach the API.
    if '\x00' in text:
        text = text.replace('\x00', '')

    return text


def optimize_markdown_style(text: str) -> str:
    """Markdown 预处理流水线：代码块保护 → 标题降级 → 还原 → 空行压缩 → 图片剥离。

    入参：text（原始 markdown）
    返回：优化后的 markdown
    副作用：无
    谁调用：elements.py 的 answer 渲染、special.py 的 cron/gateway 卡片
    改动影响：短文本（<100 字符且无特殊标记）会跳过以节省 CPU

    流水线：
    原始文本 → 提取代码块（占位符保护） → 标题降级 → 还原代码块 → 压缩空行 → 剥离无效图片 key
    """
    if len(text) < 100 and not _RE_SHORT_MD_CHECK.search(text):
        return text
    try:
        # 1. 提取代码块
        mark = "___CB_"
        code_blocks: list[str] = []

        def _extract(m: re.Match) -> str:
            prefix = m.group(1) or ""
            block = m.group(0)[len(prefix) :]
            idx = len(code_blocks)
            code_blocks.append(block)
            return f"{prefix}{mark}{idx}___"

        r = _RE_CODE_BLOCK_EXTRACT.sub(_extract, text)

        # 2. 标题降级（仅当存在 H1-H3 时）——H1→H4, H2-H6→H5
        if _RE_H1_TO_H3.search(text):
            r = _RE_HEADING_DEMOTE.sub(r'##### \1', r)
            r = _RE_H1_DEMOTE.sub(r'#### \1', r)

        # 3. 还原代码块
        for i, block in enumerate(code_blocks):
            r = r.replace(f"{mark}{i}___", block)

        # 4. 压缩多余空行
        r = _RE_MULTI_NEWLINE.sub("\n\n", r)

        # 5. 剥离无效图片 key
        r = _strip_invalid_image_keys(r)

        return r
    except Exception:
        _logger.debug("optimize_markdown_style failed", exc_info=True)
        return text


def _split_long_text(text: str, limit: int = _MAX_CHUNK_CHARS) -> list[str]:
    """将超长文本按段落/换行拆分为多个不超过 limit 字符的块。

    为什么：飞书 Card 2.0 单个 markdown 元素有长度限制，超长会截断。
    拆分策略：优先在段落分隔（\\n\\n）处切，其次在换行（\\n）处切，最后硬切。
    改了会怎样：降低 limit 会产生更多块，增加 API 调用次数。
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
