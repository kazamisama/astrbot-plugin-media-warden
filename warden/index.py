"""SQLite 索引 —— 记录每条保存的资产.

Schema:
  assets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,           -- 落盘 unix 时间
    platform TEXT, group_id TEXT, sender_id TEXT, msg_id TEXT,
    idx INTEGER, kind TEXT, path TEXT, size INTEGER,
    sha16 TEXT, forward_meta TEXT  -- JSON
  )
  索引: ix_assets_msg (platform, group_id, msg_id, idx)
        ix_assets_sha (sha16)
        ix_assets_ts  (ts DESC)
"""
from __future__ import annotations
import json
import os
import sqlite3
import time
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  platform TEXT, group_id TEXT, sender_id TEXT,
  msg_id TEXT, idx INTEGER,
  kind TEXT, path TEXT, size INTEGER,
  sha16 TEXT, forward_meta TEXT
);
CREATE INDEX IF NOT EXISTS ix_assets_msg
  ON assets(platform, group_id, msg_id, idx);
CREATE INDEX IF NOT EXISTS ix_assets_sha
  ON assets(sha16);
CREATE INDEX IF NOT EXISTS ix_assets_ts
  ON assets(ts DESC);
"""


class AssetIndex:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AssetIndex not opened")
        return self._conn

    def record(self, *, platform: str, group_id: str, sender_id: str,
               msg_id: str, idx: int, kind: str, path: str,
               size: int, sha16: Optional[str] = None,
               forward_meta: Optional[dict] = None) -> int:
        c = self._require()
        cur = c.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, "
            "idx, kind, path, size, sha16, forward_meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), platform, group_id, sender_id, msg_id,
             idx, kind, path, size, sha16,
             json.dumps(forward_meta) if forward_meta else None),
        )
        c.commit()
        return cur.lastrowid

    def recent(self, limit: int = 10) -> list[dict]:
        c = self._require()
        cur = c.execute(
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16 FROM assets "
            "ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def find_by_msg(self, msg_id: str, *,
                    platform: Optional[str] = None,
                    group_id: Optional[str] = None,
                    sender_id: Optional[str] = None) -> list[dict]:
        """反向查:某条消息保存了哪些资产.

        至少要 msg_id,可选加 platform/group_id/sender_id 缩窄范围.
        """
        c = self._require()
        where = ["msg_id = ?"]
        params: list[Any] = [msg_id]
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if sender_id:
            where.append("sender_id = ?")
            params.append(sender_id)
        sql = (
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16, forward_meta FROM assets WHERE "
            + " AND ".join(where) + " ORDER BY idx ASC, id ASC"
        )
        cur = c.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            if d.get("forward_meta"):
                try:
                    d["forward_meta"] = json.loads(d["forward_meta"])
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return out

    def find_by_sha(self, sha16: str) -> list[dict]:
        c = self._require()
        cur = c.execute(
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16 FROM assets WHERE sha16 = ?",
            (sha16,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def count(self) -> int:
        c = self._require()
        return c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def prune_older_than(self, cutoff_ts: int) -> int:
        """删除 ts < cutoff 的记录;不删文件,只删索引.返回删除条数."""
        c = self._require()
        cur = c.execute("DELETE FROM assets WHERE ts < ?", (int(cutoff_ts),))
        c.commit()
        return cur.rowcount

    def stats(self) -> dict:
        c = self._require()
        total = c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        by_kind = dict(c.execute(
            "SELECT kind, COUNT(*) FROM assets GROUP BY kind"
        ).fetchall())
        total_bytes = c.execute(
            "SELECT COALESCE(SUM(size), 0) FROM assets"
        ).fetchone()[0]
        return {
            "total": total,
            "by_kind": by_kind,
            "total_bytes": int(total_bytes),
            "db_path": self.db_path,
        }

    def export_json(self, out_path: str) -> int:
        c = self._require()
        rows = c.execute(
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16, forward_meta FROM assets "
            "ORDER BY id ASC"
        ).fetchall()
        cols = ["id", "ts", "platform", "group_id", "sender_id", "msg_id",
                "idx", "kind", "path", "size", "sha16", "forward_meta"]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            if d.get("forward_meta"):
                try:
                    d["forward_meta"] = json.loads(d["forward_meta"])
                except (TypeError, ValueError):
                    pass
            out.append(d)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": out}, f,
                      ensure_ascii=False, indent=2)
        return len(out)
