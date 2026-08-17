"""On registration: backs up config.yaml (timestamped), injects clean defaults."""

# ================================================================
# lark-hls-v2 · plugin/__init__.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：插件注册入口。Hermes 发现插件后调用 register(ctx)：
#    备份 config.yaml → 注入默认配置 → 应用 monkey-patches → 预热飞书客户端 →
#    注册 /aowen 命令钩子。unregister(ctx) 负责清理。
# ② 技术栈：yaml + asyncio + pathlib，纯 Python。
# ③ 依赖：config/defaults.py（默认值常量）、interceptors/apply_patches()、
#    controller.py（get_controller）、aowen/（/aowen 钩子）。
# ④ 给谁看：Hermes 插件系统（调用 register/unregister）、运维人员。
# ▍结构
# _DEFAULT_STREAMING_CONFIG — 默认流式配置 dict（从 defaults.py 组装）
# _backup_config() — 首次安装时备份 config.yaml（带时间戳）
# _ensure_streaming_config() — 注入 lark_hls_v2 段到 config.yaml
# _cleanup_config() — 卸载时清除 lark_hls_v2 段
# register(ctx) — 主入口：配置注入 → apply_patches → 预热 → 注册钩子
# unregister(ctx) — 清理入口
# ▍修改铁律
# 1. register() 是插件加载三要素之一（__init__.py 导出 register），
#    改了函数签名或不导出会导致插件无法加载（静默失败）。
# 2. _ensure_streaming_config() 会用 yaml.dump 写 config.yaml，
#    可能丢失注释和格式（已知限制）。
# 3. _backup_config() 只在首次安装时备份（检查是否已有备份文件），
#    重复安装不会覆盖旧备份。
# ================================================================

from __future__ import annotations
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .. import __version__
from ..config.defaults import (
    ENABLED,
    LINEAR,
    PANEL_EXPANDED,
    STREAMING_PANEL_EXPANDED,
    PRINT_STRATEGY,
    PRINT_STEP,
    FLUSH_INTERVAL_MS,
    CARD_TTL_SEC,
    MAX_TOOL_STEPS,
    MAX_REASONING_ROUNDS,
    FOOTER_FIELDS,
    FOOTER_SHOW_LABEL,
    GATEWAY_CARDS,
)

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

_logger = logging.getLogger("lark_hls_v2")

_PLUGIN_NAME = "lark-hls-v2"

# ▍默认流式配置 — 从 defaults.py 常量组装，注入到 config.yaml 的 lark_hls_v2 段
_DEFAULT_STREAMING_CONFIG: dict[str, Any] = {
    "panel_expanded": PANEL_EXPANDED,
    "streaming_panel_expanded": STREAMING_PANEL_EXPANDED,
    "print_strategy": PRINT_STRATEGY,
    "print_step": PRINT_STEP,
    "flush_interval_ms": FLUSH_INTERVAL_MS,
    "card_ttl_sec": CARD_TTL_SEC,
    "max_tool_steps": MAX_TOOL_STEPS,
    "max_reasoning_rounds": MAX_REASONING_ROUNDS,
    "footer": {
        "fields": FOOTER_FIELDS,
        "show_label": FOOTER_SHOW_LABEL,
    },
}

_prewarm_tasks: set = set()


def _get_hermes_config_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"


def _backup_config() -> None:
    """首次安装时备份 config.yaml（带时间戳后缀，只备份一次）."""
    config_path = _get_hermes_config_path()
    if not config_path.exists():
        return
    backup_pattern = f"config.yaml.*.{_PLUGIN_NAME}"
    parent = config_path.parent
    existing_backups = list(parent.glob(backup_pattern))
    if existing_backups:
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"config.yaml.{timestamp}.{_PLUGIN_NAME}"
    backup_path = parent / backup_name
    try:
        shutil.copy2(config_path, backup_path)
        _logger.info("Backed up config.yaml to %s", backup_path)
    except Exception:
        _logger.exception("Failed to back up config.yaml to %s", backup_path)


def _prepare_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """深拷贝配置 dict（递归）."""
    result: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            result[k] = _prepare_config(v)
        else:
            result[k] = v
    return result


def _ensure_streaming_config() -> None:
    """注入 lark_hls_v2 默认配置段到 config.yaml（如果不存在）."""
    config_path = _get_hermes_config_path()
    if not config_path.exists():
        return
    try:
        text = config_path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        changed = False

        if "lark_hls_v2" not in raw:
            _backup_config()
            raw["lark_hls_v2"] = dict(_DEFAULT_STREAMING_CONFIG)
            changed = True

        plugins = raw.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabled")
            if isinstance(enabled, list) and _PLUGIN_NAME not in enabled:
                _backup_config()
                enabled.append(_PLUGIN_NAME)
                changed = True

        if changed:
            prepped = _prepare_config(raw)
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(prepped, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        _logger.exception("Failed to ensure lark_hls_v2 config in config.yaml")


def _cleanup_config() -> None:
    """卸载时清除 config.yaml 中的 lark_hls_v2 段和 plugins.enabled 条目."""
    config_path = _get_hermes_config_path()
    if not config_path.exists():
        return
    try:
        text = config_path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        changed = False
        if "lark_hls_v2" in raw:
            del raw["lark_hls_v2"]
            changed = True
        plugins = raw.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabled")
            if isinstance(enabled, list) and _PLUGIN_NAME in enabled:
                enabled.remove(_PLUGIN_NAME)
                changed = True
        if changed:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        _logger.exception("Failed to clean up lark_hls_v2 config")



def register(ctx: "PluginContext") -> None:
    """register()：契约
    入参：ctx（PluginContext）— Hermes 插件上下文
    返回：无
    副作用：修改 config.yaml、应用 monkey-patches、预热飞书客户端、注册 /aowen 钩子
    谁调用：Hermes 插件系统（启动时）
    改动影响：这是插件加载三要素之一，不导出或签名变了会导致插件不加载
    """
    _ensure_streaming_config()

    _logger.info("lark-hls-v2 v%s: applying runtime patches...", __version__)
    try:
        from ..interceptors import apply_patches
        apply_patches()
        _logger.info("lark-hls-v2 v%s: patches applied", __version__)
    except Exception:
        _logger.exception("lark-hls-v2 v%s: failed to apply patches", __version__)

    # Pre-warm FeishuClient
    try:
        from ..controller import get_controller
        import asyncio
        ctrl = get_controller()
        if ctrl.enabled:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                _prewarm_task = loop.create_task(ctrl._ensure_init())
                _prewarm_tasks.add(_prewarm_task)
                _prewarm_task.add_done_callback(_prewarm_tasks.discard)
    except Exception:
        _logger.debug("FeishuClient pre-warm skipped", exc_info=True)

    # Register /aowen commands
    try:
        from ..aowen import handle_pre_gateway_dispatch
        ctx.register_hook("pre_gateway_dispatch", handle_pre_gateway_dispatch)
    except Exception:
        _logger.debug("/aowen hook registration skipped", exc_info=True)


def unregister(ctx: "PluginContext") -> None:
    """unregister()：契约
    入参：ctx（PluginContext）— Hermes 插件上下文
    返回：无
    副作用：清除 config.yaml 中的 lark_hls_v2 配置、清空 session
    谁调用：Hermes 插件系统（卸载时）
    改动影响：不影响运行中的 gateway（需要重启才生效）
    """
    _cleanup_config()
    try:
        from ..controller import get_controller
        ctrl = get_controller()
        ctrl._sess_clear()
    except Exception:
        pass
    _logger.info("lark-hls-v2: unregistered")
