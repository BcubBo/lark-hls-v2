# ================================================================
# lark-hls-v2 · feishu/user_cache.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（四问）
# ① 干什么：SQLite用户缓存，存储open_id→name+role+permissions映射
# ② 技术栈：Python sqlite3
# ③ 依赖：无外部依赖
# ④ 给谁看：修改用户权限、新增角色、排查身份识别问题的人
# ▍修改铁律
# 1. API缓存不覆盖手动设置的记录（source=manual优先）
# 2. 新增角色必须同步更新_default_permissions
# ================================================================

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class FeishuUserCache:
    """SQLite-based user cache for Feishu external users.
    
    Stores open_id → name mapping with role-based permissions.
    Supports automatic learning from contact API and manual registration.
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with user cache table."""
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS feishu_users (
                        open_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        feishu_role TEXT DEFAULT 'member',
                        role TEXT NOT NULL DEFAULT 'member',
                        permissions TEXT DEFAULT '{}',
                        source TEXT NOT NULL,
                        chat_id TEXT,
                        linked_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_role ON feishu_users(role)
                """)
                conn.commit()
                logger.info("[FeishuUserCache] Initialized database at %s", self.db_path)
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to initialize database: %s", e)
            raise
    
    def get_user(self, open_id: str) -> Optional[dict]:
        """Get user info by open_id."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM feishu_users WHERE open_id = ?",
                    (open_id,)
                ).fetchone()
                if row:
                    return {
                        "open_id": row["open_id"],
                        "name": row["name"],
                        "feishu_role": row["feishu_role"] if "feishu_role" in row.keys() else "member",
                        "system_role": row["role"],
                        "permissions": json.loads(row["permissions"]),
                        "source": row["source"],
                        "chat_id": row["chat_id"]
                    }
                return None
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to get user %s: %s", open_id, e)
            return None
    
    def get_name(self, open_id: str) -> Optional[str]:
        """Get user name by open_id (convenience method)."""
        user = self.get_user(open_id)
        return user["name"] if user else None
    
    def set_user(self, open_id: str, name: str, role: str = "member", 
                 permissions: dict = None, source: str = "api", chat_id: str = ""):
        """Set or update user info. API source will NOT overwrite manual records."""
        if permissions is None:
            permissions = self._default_permissions(role)
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                # API缓存不覆盖手动设置的记录
                if source == "api":
                    existing = conn.execute(
                        "SELECT source FROM feishu_users WHERE open_id = ?", (open_id,)
                    ).fetchone()
                    if existing and existing[0] == "manual":
                        return  # 跳过，不覆盖手动设置的记录
                
                conn.execute("""
                    INSERT OR REPLACE INTO feishu_users 
                    (open_id, name, role, permissions, source, chat_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (open_id, name, role, json.dumps(permissions), source, chat_id))
                conn.commit()
                logger.info("[FeishuUserCache] Set user %s (%s) as %s from %s", 
                           name, open_id, role, source)
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to set user %s: %s", open_id, e)
    
    def check_permission(self, open_id: str, permission: str) -> bool:
        """Check if user has specific permission."""
        user = self.get_user(open_id)
        if not user:
            return False  # Unknown users have no permissions
        return user["permissions"].get(permission, False)
    
    def link_ids(self, id1: str, id2: str):
        """Link two IDs (open_id and user_id) for the same user."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE feishu_users SET linked_id = ? WHERE open_id = ? AND linked_id IS NULL",
                    (id2, id1)
                )
                conn.execute(
                    "UPDATE feishu_users SET linked_id = ? WHERE open_id = ? AND linked_id IS NULL",
                    (id1, id2)
                )
                conn.commit()
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to link IDs %s and %s: %s", id1, id2, e)
    
    def _default_permissions(self, role: str) -> dict:
        """Get default permissions for role."""
        if role == "admin":
            return {
                "config_read": True, "config_write": True,
                "memory_read": True, "memory_write": True,
                "system_core": True, "command_execute": True,
                "sensitive_info": True, "member_manage": True,
            }
        elif role == "moderator":
            return {
                "config_read": True, "config_write": False,
                "memory_read": True, "memory_write": False,
                "system_core": False, "command_execute": False,
                "sensitive_info": False, "member_manage": True,
            }
        else:  # member
            return {
                "config_read": False, "config_write": False,
                "memory_read": False, "memory_write": False,
                "system_core": False, "command_execute": False,
                "sensitive_info": False, "member_manage": False,
            }
    
    def list_users(self) -> list:
        """List all users."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM feishu_users ORDER BY role, name"
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to list users: %s", e)
            return []
