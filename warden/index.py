"""SQLite 索引 —— 记录每条保存的资产 + 每次失败.

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

  failures(                -- v1.9: 失败也入库(原来只进回执,DB 里查不到)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER, platform TEXT, group_id TEXT, sender_id TEXT,
    msg_id TEXT, idx INTEGER, kind TEXT,
    err TEXT,              -- 可读原因(含 http 400 / save: OSError ...)
    subject TEXT,          -- 稳定去重键: fileid:<id> / url:<host+path> / sha:<digest>
    url TEXT, attempts INTEGER
  )
  索引: ix_fail_ts (ts DESC), ix_fail_subject (subject, kind)
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
CREATE TABLE IF NOT EXISTS failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  platform TEXT, group_id TEXT, sender_id TEXT,
  msg_id TEXT, idx INTEGER, kind TEXT,
  err TEXT, subject TEXT, url TEXT, attempts INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_fail_ts
  ON failures(ts DESC);
CREATE INDEX IF NOT EXISTS ix_fail_subject
  ON failures(subject, kind);
"""

_FAILURE_COLS = ("id", "ts", "platform", "group_id", "sender_id",
                 "msg_id", "idx", "kind", "err", "subject", "url", "attempts")


def failure_subject(url: Optional[str], *, file_id: Optional[str] = None,
                    fallback: Optional[str] = None) -> str:
    """把一次失败归一成稳定去重键.

    优先 fileid:<file_id>（QQ 的 rkey 会换,fileid 不变 -> 同一张图能对上）,
    其次 url:<host><path>（丢掉 query,吸收 rkey/sign 这类时效参数）,
    再次 fallback（调用方给的内容指纹 / 键）。
    都没有时返回 "unknown"。
    """
    fid = (str(file_id) if file_id else "").strip()
    if fid:
        return f"fileid:{fid}"
    u = (url or "").strip()
    if u:
        if "fileid=" in u:
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(u).query)
            vals = q.get("fileid") or q.get("file_id")
            if vals and vals[0]:
                return f"fileid:{vals[0]}"
        from urllib.parse import urlparse
        p = urlparse(u)
        if p.netloc:
            return f"url:{p.netloc}{p.path}"
    if fallback:
        return str(fallback)
    return "unknown"


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

    def record_failure(self, *, platform: str, group_id: str, sender_id: str,
                       msg_id: str, idx: int, kind: str, err: str,
                       subject: Optional[str] = None,
                       url: Optional[str] = None) -> tuple[int, int, float]:
        """失败入库. 返回 (id, 同一 subject 的累计次数, 该 subject 上次失败时间).

        attempts 记的是同一个 subject 累计失败了几次 —— 让"这张图反复存不下来"
        在 DB 里可见,而不只是刷屏回执。subject 为空时按 url 归一。
        """
        c = self._require()
        subj = subject or failure_subject(url)
        prev = c.execute(
            "SELECT COUNT(*), MAX(ts) FROM failures WHERE subject = ? AND kind = ?",
            (subj, kind),
        ).fetchone()
        attempts = int(prev[0] or 0) + 1
        last_ts = float(prev[1]) if prev[1] else 0.0
        cur = c.execute(
            "INSERT INTO failures (ts, platform, group_id, sender_id, msg_id, "
            "idx, kind, err, subject, url, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), platform, group_id, sender_id, msg_id,
             idx, kind, err, subj, url, attempts),
        )
        c.commit()
        return cur.lastrowid, attempts, last_ts

    def recent_failures(self, limit: int = 20) -> list[dict]:
        c = self._require()
        cur = c.execute(
            "SELECT " + ", ".join(_FAILURE_COLS) + " FROM failures "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def failure_stats(self) -> dict:
        c = self._require()
        total = c.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        by_subject = dict(c.execute(
            "SELECT subject, COUNT(*) FROM failures GROUP BY subject "
            "ORDER BY COUNT(*) DESC LIMIT 20"
        ).fetchall())
        top = c.execute(
            "SELECT subject, COUNT(*), MAX(ts) FROM failures "
            "GROUP BY subject ORDER BY COUNT(*) DESC LIMIT 5"
        ).fetchall()
        return {
            "total": int(total),
            "by_subject": by_subject,
            "top": [{"subject": r[0], "count": int(r[1]), "last_ts": int(r[2] or 0)}
                    for r in top],
        }

    def prune_failures_older_than(self, cutoff_ts: int) -> int:
        c = self._require()
        cur = c.execute("DELETE FROM failures WHERE ts < ?", (int(cutoff_ts),))
        c.commit()
        return cur.rowcount

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
        """删除 ts < cutoff 的记录;不删文件,只删索引.返回删除条数.

        v1.9: 同时清掉同期的 failures 记录,避免失败表只涨不落。
        """
        c = self._require()
        cur = c.execute("DELETE FROM assets WHERE ts < ?", (int(cutoff_ts),))
        c.execute("DELETE FROM failures WHERE ts < ?", (int(cutoff_ts),))
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
        fail_total = c.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        by_kind_fail = dict(c.execute(
            "SELECT kind, COUNT(*) FROM failures GROUP BY kind"
        ).fetchall())
        return {
            "total": total,
            "by_kind": by_kind,
            "total_bytes": int(total_bytes),
            "failures": int(fail_total),
            "failures_by_kind": by_kind_fail,
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
