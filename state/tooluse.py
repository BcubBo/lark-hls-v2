# ================================================================
# lark-hls-v2 state/tooluse.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：工具调用追踪与可视化。ToolStep 记录单次工具调用，ToolSession 管理会话，
#    ToolUseTracker 按 session 隔离追踪。与 openclaw-lark 工具展示对齐。
# ② 技术栈：Python 3.11+ / dataclasses / re / json
# ③ 依赖：无外部依赖
# ④ 给谁看：维护 lark-hls-v2 的开发者，理解工具调用的追踪、脱敏和卡片渲染。
# ▍文件从上到下的结构
# ToolStep / ToolSession: 数据类
# redact_inline_secrets(): 脱敏 key=secret / Authorization header / --flag secret
# _sanitize_detail(): 按 sanitizer 类型清洗 detail 文本
# _redact_paths / _basename_only: 路径脱敏
# _TOOL_DESCRIPTORS: 工具名称 -> 图标/标题/脱敏器映射表
# _resolve_tool_descriptor(): 解析工具描述符
# ToolUseTracker: 按 session 隔离的工具调用追踪器
# _build_display_block / _fenced_block: 构建 markdown 代码围栏
# ▍修改铁律
# 1. _SENSITIVE_NAME_RE 的模式【不】删掉任何关键词，改了会导致敏感信息泄露到卡片。
# 2. redact_inline_secrets 的三阶段脱敏链（assign -> auth_header -> flag）【不】调换顺序，
#    改了会导致某些模式漏脱敏。
# 3. _TOOL_DESCRIPTORS 的 aliases 必须用下划线格式（如 web_search），【不】用连字符，
#    改了会导致工具名称匹配失败。
# 4. record_end 通过名字倒序匹配 running 步骤，【不】改成正序，改了会导致同名嵌套工具匹配错误。
# ================================================================

"""工具调用追踪与可视化 -- 与 openclaw-lark 工具展示对齐."""

from __future__ import annotations

__all__ = [
    "ToolStep",
    "ToolSession",
    "ToolUseTracker",
    "redact_inline_secrets",
    "_basename_only",
    "_build_display_block",
    "_fenced_block",
    "_format_duration_label",
    "_humanize_tool_name",
    "_redact_paths",
    "_resolve_tool_descriptor",
    "_sanitize_detail",
    "_TOOL_DESCRIPTORS",
    "_SENSITIVE_NAME_RE",
    "_INLINE_ASSIGNMENT_RE",
    "_AUTH_HEADER_RE",
    "_SECRET_FLAG_RE",
]

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

# ▍数据类

@dataclass
class ToolStep:
    """单次工具调用的状态记录。"""
    name: str
    status: str  # running | success | error
    detail: str = ""
    output: str = ""
    error: str = ""
    result_block: dict[str, Any] | None = None  # {"language": "json"|"text", "content": str}
    error_block: dict[str, Any] | None = None
    started_at: float | None = None
    elapsed_ms: float = 0.0

@dataclass
class ToolSession:
    """单个会话的工具调用列表。"""
    steps: list[ToolStep] = field(default_factory=list)
    started_at: float | None = None

# ▍脱敏正则 -- 改了会导致敏感信息泄露到卡片

_SENSITIVE_NAME_RE = re.compile(
    r"token|secret|password|api[_-]?key|authorization|cookie|credential"
    r"|bearer|session[_-]?id|client[_-]?secret|access[_-]?key",
    re.IGNORECASE,
)

_INLINE_ASSIGNMENT_RE = re.compile(r'(^|[\s"\'`])([A-Za-z_][A-Za-z0-9_]*)(=(?:"[^"]*"|\'[^\']*\'|[^\\s"\'`]+))')
_AUTH_HEADER_RE = re.compile(
    r"(Authorization\s*:\s*(?:Bearer|Basic|Token)\s+)([^\\'\"\s]+)",
    re.IGNORECASE,
)
_SECRET_FLAG_RE = re.compile(
    r'((?:^|[\s"\'`])(--?[A-Za-z0-9][A-Za-z0-9-]*)(=|\s+)("(?:[^"]*)"|\'(?:[^\']*)\'|[^\\s"\'`]+))'
)

# ▍脱敏函数

def redact_inline_secrets(value: str) -> str:
    """脱敏 key=secret、Authorization header、--flag secret 模式。
    三阶段链式脱敏，【不】调换顺序。
    """

    def _redact_assign(m: re.Match) -> str:
        key = str(m.group(2))
        if _SENSITIVE_NAME_RE.search(key):
            return f"{m.group(1)}{key}=[redacted]"
        return str(m.group(0))

    def _redact_flag(m: re.Match) -> str:
        flag = re.sub(r"^-+", "", str(m.group(2)))
        if _SENSITIVE_NAME_RE.search(flag):
            return f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]"
        return str(m.group(0))

    return _SECRET_FLAG_RE.sub(
        _redact_flag,
        _AUTH_HEADER_RE.sub(r"\1[redacted]", _INLINE_ASSIGNMENT_RE.sub(_redact_assign, value)),
    )

def _sanitize_detail(text: str, sanitizer: str | None) -> str:
    """根据 sanitizer 类型清洗 detail 文本。
    command -> 脱敏 + 路径 basename，path -> basename，search/url -> 去引号。
    """
    if not text or not sanitizer:
        return text
    cleaned = re.sub(r"<[^>]+>", "", text).strip()
    if not cleaned:
        return text
    if sanitizer == "command":
        cleaned = redact_inline_secrets(cleaned)
        return _redact_paths(cleaned)
    if sanitizer == "path":
        return _basename_only(re.sub(r"^(?:from|file|path)\s+", "", cleaned, flags=re.IGNORECASE).strip())
    if sanitizer == "search":
        return cleaned.strip("'\"")
    if sanitizer == "url":
        if cleaned.lower().startswith("from "):
            return cleaned.strip("'\"").replace("from ", "", 1)
        return cleaned.strip("'\"")
    return cleaned

def _redact_paths(text: str) -> str:
    """命令中路径只保留 basename。"""
    return re.sub(
        r'(^|[\s=\'"()])([~./][^\s\'"()]+)',
        lambda m: f"{m.group(1)}{os.path.basename(m.group(2))}",
        text,
    )

def _basename_only(text: str) -> str:
    if not text:
        return text
    return os.path.basename(text.replace("\\", "/").rstrip("/"))

# ▍工具描述符映射表 -- aliases 必须用下划线格式

_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {"aliases": ["skill"], "icon": "app-default_outlined", "title": "Load skill", "sanitizer": None},
    {
        "aliases": ["read", "open"],
        "icon": "file-link-text_outlined",
        "title": "Read",
        "sanitizer": "path",
        "no_result": True,
    },
    {
        "aliases": ["write", "edit"],
        "icon": "edit_outlined",
        "title": "Edit",
        "sanitizer": "path",
        "no_result": True,
    },
    {
        "aliases": ["web_search", "web-search", "search"],
        "icon": "search_outlined",
        "title": "Search",
        "sanitizer": "search",
    },
    {
        "aliases": ["web_fetch", "web-fetch", "fetch"],
        "icon": "language_outlined",
        "title": "Fetch web page",
        "sanitizer": "url",
        "no_result": True,
    },
    {"aliases": ["grep"], "icon": "doc-search_outlined", "title": "Search text", "sanitizer": "search"},
    {"aliases": ["glob"], "icon": "folder_outlined", "title": "Search files", "sanitizer": "path"},
    {
        "aliases": ["exec", "bash", "command", "run"],
        "icon": "setting_outlined",
        "title": "Run command",
        "sanitizer": "command",
    },
    {
        "aliases": ["browser", "playwright", "navigate"],
        "icon": "browser-mac_outlined",
        "title": "Browser",
        "no_result": True,
    },
    {"aliases": ["agent", "task", "spawn"], "icon": "robot_outlined", "title": "Run sub-agent"},
    {"aliases": ["check", "determine", "verify"], "icon": "list-check_outlined", "title": "Check"},
    {"aliases": ["summarize", "analyze", "prepare"], "icon": "report_outlined", "title": "Analyze"},
]

def _resolve_tool_descriptor(name: str | None) -> dict[str, Any] | None:
    """按别名匹配工具描述符。"""
    if not name:
        return None
    normalized = name.strip().lower().replace("-", "_")
    for desc in _TOOL_DESCRIPTORS:
        for alias in desc["aliases"]:
            if normalized == alias or normalized.startswith(f"{alias}_"):
                return desc
    return None

def _humanize_tool_name(name: str) -> str:
    cleaned = name.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return "Tool"
    return cleaned[0].upper() + cleaned[1:]

def _format_duration_label(ms: float) -> str:
    return f"{ms:.0f} ms" if ms < 1000 else f"{(ms / 1000):.1f} s"

# ▍显示块构建

def _build_display_block(
    value: Any,
    fallback_lang: str = "json",
    *,
    sanitizer: str | None = None,
) -> dict[str, Any] | None:
    """构建结果/错误的显示块 -- 返回 {language, content, fenced} 含 markdown 代码围栏。"""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.replace("\r\n", "\n").strip()
        if not normalized:
            return None
        if sanitizer == "command":
            normalized = redact_inline_secrets(normalized)
        if normalized.startswith("{") or normalized.startswith("["):
            try:
                parsed = json.loads(normalized)
                pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
                return _fenced_block("json", pretty)
            except json.JSONDecodeError:
                pass
        return _fenced_block("text" if fallback_lang == "json" else fallback_lang, normalized)
    if isinstance(value, (dict, list)):
        try:
            return _fenced_block("json", json.dumps(value, ensure_ascii=False, indent=2))
        except (TypeError, ValueError):
            pass
    normalized = str(value).strip()
    return _fenced_block("text", normalized) if normalized else None

def _fenced_block(language: str, content: str) -> dict[str, Any]:
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", content)), default=0) + 1)
    return {"language": language, "content": content, "fenced": f"{fence}{language}\n{content}\n{fence}"}

# ▍ToolUseTracker -- 按 session 隔离的工具调用追踪器

class ToolUseTracker:
    """按 session 隔离，每个会话独立生命周期.
    max_steps 限制防止工具调用过多导致卡片渲染卡顿。
    """

    def __init__(self, max_steps: int = 128) -> None:
        self._session: ToolSession | None = None
        self._max_steps = max_steps

    @property
    def elapsed_ms(self) -> float:
        if self._session is None or self._session.started_at is None:
            return 0.0
        return (time.time() - self._session.started_at) * 1000

    def record_start(self, name: str, detail: str = "") -> None:
        """记录工具调用开始。"""
        if self._session is None:
            self._session = ToolSession(started_at=time.time())
        if len(self._session.steps) >= self._max_steps:
            return
        self._session.steps.append(
            ToolStep(
                name=name,
                status="running",
                detail=detail,
                started_at=time.time(),
            )
        )

    def record_end(self, name: str, *, error: str = "", output: str = "") -> None:
        """通过名字倒序匹配最近的一个 running 步骤来结束。
        【不】改成正序匹配，改了会导致同名嵌套工具匹配错误。
        """
        if self._session is None:
            return
        desc = _resolve_tool_descriptor(name)
        sanitizer = desc.get("sanitizer") if desc else None
        for step in reversed(self._session.steps):
            if step.name == name and step.status == "running":
                step.status = "error" if error else "success"
                step.error = error
                step.output = output
                step.elapsed_ms = (time.time() - step.started_at) * 1000 if step.started_at is not None else 0.0
                if error:
                    step.error_block = _build_display_block(error, "text", sanitizer=sanitizer)
                elif output:
                    step.result_block = _build_display_block(output, "json", sanitizer=sanitizer)
                return
        self._session.steps.append(
            ToolStep(
                name=name,
                status="error" if error else "success",
                detail=error or output,
                output=output,
                error=error,
                started_at=time.time(),
                error_block=_build_display_block(error, "text", sanitizer=sanitizer) if error else None,
                result_block=_build_display_block(output, "json", sanitizer=sanitizer) if output else None,
            )
        )

    def build_display_steps(self) -> list[dict[str, Any]]:
        """构建用于卡片渲染的步骤列表 -- 与 openclaw 结构对齐。"""
        if self._session is None:
            return []
        steps = []
        for s in self._session.steps:
            desc = _resolve_tool_descriptor(s.name)
            base_title = desc["title"] if desc else _humanize_tool_name(s.name)
            if s.elapsed_ms > 0:
                base_title = f"{base_title} ({_format_duration_label(s.elapsed_ms)})"
            sanitizer = desc.get("sanitizer") if desc else None
            detail = _sanitize_detail(s.detail, sanitizer)
            steps.append(
                {
                    "name": s.name,
                    "title": base_title,
                    "status": s.status,
                    "detail": detail,
                    "output": s.output,
                    "error": s.error,
                    "icon": desc["icon"] if desc else "setting-inter_outlined",
                    "elapsed_ms": s.elapsed_ms,
                    "result_block": None if (desc and desc.get("no_result")) else s.result_block,
                    "error_block": s.error_block,
                }
            )
        return steps
