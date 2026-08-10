"""On registration: backs up config.yaml (timestamped), injects clean defaults."""

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
    result: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            result[k] = _prepare_config(v)
        else:
            result[k] = v
    return result


def _ensure_streaming_config() -> None:
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
    _cleanup_config()
    try:
        from ..controller import get_controller
        ctrl = get_controller()
        ctrl._sess_clear()
    except Exception:
        pass
    _logger.info("lark-hls-v2: unregistered")
