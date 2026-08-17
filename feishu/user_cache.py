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
        """Initialize SQLite database schema. 延迟到首次写入时才创建。"""
        self._schema_ready = False
        self.db_path_str = str(self.db_path)
    
    def _ensure_schema(self):
        """确保 schema 存在（延迟初始化）。"""
        if self._schema_ready:
            return
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
                # 群成员关联表：一个用户可在多个群，每个群有独立角色
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS group_members (
                        open_id TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        feishu_role TEXT DEFAULT 'member',
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (open_id, chat_id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_gm_chat ON group_members(chat_id)
                """)
                conn.commit()
                self._schema_ready = True
                logger.info("[FeishuUserCache] Initialized database at %s", self.db_path)
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to initialize database: %s", e)
            raise
    
    def get_user(self, open_id: str) -> Optional[dict]:
        """Get user info by open_id. 数据库不存在时直接返回 None（不创建）。"""
        if not Path(self.db_path).exists():
            return None
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
        self._ensure_schema()
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
    
    def auto_insert_from_message(self, open_id: str, name: str,
                                  chat_id: str, feishu_role: str = "member") -> None:
        """群消息自动入库：写 feishu_users + group_members 两表。
        
        feishu_users: 用户基础信息（open_id, name, role, permissions, source）
        group_members: 群归属 + 群内角色（open_id, chat_id, feishu_role）
        
        source 优先级：manual > auto > api
        """
        if not open_id or not name:
            return
        self._ensure_schema()
        try:
            with sqlite3.connect(self.db_path) as conn:
                # ── 步骤1：更新 feishu_users ──
                existing = conn.execute(
                    "SELECT source, chat_id FROM feishu_users WHERE open_id = ?",
                    (open_id,)
                ).fetchone()
                
                if existing:
                    if existing[0] != "manual":
                        conn.execute("""
                            UPDATE feishu_users 
                            SET name = ?, source = 'auto', updated_at = CURRENT_TIMESTAMP
                            WHERE open_id = ?
                        """, (name, open_id))
                else:
                    conn.execute("""
                        INSERT INTO feishu_users (open_id, name, feishu_role, role, permissions, source, updated_at)
                        VALUES (?, ?, ?, 'member', '{}', 'auto', CURRENT_TIMESTAMP)
                    """, (open_id, name, feishu_role))
                
                # ── 步骤2：更新 group_members（精确匹配 open_id + chat_id）──
                if chat_id:
                    # 如果 user_id 不是 ou_ 开头，查 linked_id 找到对应的 open_id
                    gm_open_id = open_id
                    if not open_id.startswith("ou_"):
                        linked = conn.execute(
                            "SELECT linked_id FROM feishu_users WHERE open_id = ? AND linked_id IS NOT NULL AND linked_id != ''",
                            (open_id,)
                        ).fetchone()
                        if linked and linked[0]:
                            gm_open_id = linked[0]
                    
                    conn.execute("""
                        INSERT INTO group_members (open_id, chat_id, feishu_role, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(open_id, chat_id) DO UPDATE SET
                            feishu_role = excluded.feishu_role,
                            updated_at = CURRENT_TIMESTAMP
                    """, (gm_open_id, chat_id, feishu_role))
                
                conn.commit()
                logger.info("[FeishuUserCache] Auto-inserted %s (%s) chat=%s role=%s",
                           name, open_id[:12], chat_id[:12] if chat_id else "?", feishu_role)
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to auto-insert %s: %s", open_id, e)

    def upsert_group_member(self, open_id: str, chat_id: str,
                             feishu_role: str = "member") -> None:
        """单独更新 group_members 表（用于全量同步时批量写入）。"""
        if not open_id or not chat_id:
            return
        self._ensure_schema()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO group_members (open_id, chat_id, feishu_role, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(open_id, chat_id) DO UPDATE SET
                        feishu_role = excluded.feishu_role,
                        updated_at = CURRENT_TIMESTAMP
                """, (open_id, chat_id, feishu_role))
                conn.commit()
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to upsert group_member %s/%s: %s",
                        open_id[:12], chat_id[:12], e)

    def list_group_members(self, chat_id: str) -> list:
        """查询某群的全部成员（JOIN group_members + feishu_users）。
        
        返回: [{open_id, name, feishu_role, role, permissions, source, linked_id}, ...]
        按 feishu_role 排序：owner > admin > member
        """
        if not Path(self.db_path).exists():
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT u.open_id, u.name, u.role, u.permissions, u.source, u.linked_id,
                           g.feishu_role, g.joined_at
                    FROM group_members g
                    JOIN feishu_users u ON u.open_id = g.open_id
                    WHERE g.chat_id = ?
                    ORDER BY CASE g.feishu_role
                        WHEN 'owner' THEN 0
                        WHEN 'admin' THEN 1
                        ELSE 2
                    END, u.name
                """, (chat_id,)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to list group members: %s", e)
            return []

    def list_users(self, chat_id: str = None) -> list:
        """List users, optionally filtered by chat_id.
        
        chat_id 精确匹配 group_members 表。
        chat_id=None 时返回全部（向后兼容）。
        """
        if not Path(self.db_path).exists():
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if chat_id is not None and chat_id:
                    rows = conn.execute("""
                        SELECT DISTINCT u.*
                        FROM feishu_users u
                        JOIN group_members g ON g.open_id = u.open_id
                        WHERE g.chat_id = ?
                        ORDER BY u.name
                    """, (chat_id,)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM feishu_users ORDER BY feishu_role DESC, name"
                    ).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error("[FeishuUserCache] Failed to list users: %s", e)
            return []

    def migrate_chat_id_to_group_members(self) -> int:
        """迁移旧数据：将 feishu_users.chat_id 逗号分隔值写入 group_members 表。
        
        返回迁移的记录数。幂等：已存在的 (open_id, chat_id) 会被跳过。
        """
        if not Path(self.db_path).exists():
            return 0
        count = 0
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT open_id, chat_id, feishu_role FROM feishu_users WHERE chat_id IS NOT NULL AND chat_id != ''"
                ).fetchall()
                for open_id, chat_ids, feishu_role in rows:
                    for cid in chat_ids.split(","):
                        cid = cid.strip()
                        if not cid:
                            continue
                        try:
                            conn.execute("""
                                INSERT OR IGNORE INTO group_members (open_id, chat_id, feishu_role)
                                VALUES (?, ?, ?)
                            """, (open_id, cid, feishu_role or "member"))
                            count += 1
                        except Exception:
                            pass
                conn.commit()
                logger.info("[FeishuUserCache] Migrated %d chat_id entries to group_members", count)
        except Exception as e:
            logger.error("[FeishuUserCache] Migration failed: %s", e)
        return count
