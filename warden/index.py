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


# v1.10: 建表 / 建索引分开 —— 旧库补列必须在建索引之前,
# 否则 CREATE INDEX ... ON failures(subject) 会在 subject 还不存在时直接报错。
SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  platform TEXT, group_id TEXT, sender_id TEXT,
  msg_id TEXT, idx INTEGER,
  kind TEXT, path TEXT, size INTEGER,
  sha16 TEXT, forward_meta TEXT
);
CREATE TABLE IF NOT EXISTS failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  platform TEXT, group_id TEXT, sender_id TEXT,
  msg_id TEXT, idx INTEGER, kind TEXT,
  err TEXT, subject TEXT, url TEXT, attempts INTEGER DEFAULT 1
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_assets_msg
  ON assets(platform, group_id, msg_id, idx);
CREATE INDEX IF NOT EXISTS ix_assets_sha
  ON assets(sha16);
CREATE INDEX IF NOT EXISTS ix_assets_ts
  ON assets(ts DESC);
CREATE INDEX IF NOT EXISTS ix_fail_ts
  ON failures(ts DESC);
CREATE INDEX IF NOT EXISTS ix_fail_subject
  ON failures(subject, kind);
"""

_FAILURE_COLS = ("id", "ts", "platform", "group_id", "sender_id",
                 "msg_id", "idx", "kind", "err", "subject", "url", "attempts")

# v1.10: 旧库升级清单 —— CREATE TABLE IF NOT EXISTS 不会给已存在的表补列,
# 所以 open() 里显式对比 PRAGMA table_info 并 ALTER TABLE ADD COLUMN。
# 键是表名,值是 {列名: 列声明}(只用于"补列",不改已有列的类型)。
_COLUMN_SPECS: dict = {
    "assets": {"forward_meta": "TEXT"},
    "failures": {"err": "TEXT", "subject": "TEXT", "url": "TEXT",
                 "attempts": "INTEGER DEFAULT 1"},
}
SCHEMA_VERSION = 1


def _asset_rows(cur) -> list[dict]:
    """游标 -> dict 行,顺带把 forward_meta 的 JSON 文本解回对象.

    三个读资产的方法(recent / find_by_msg / find_by_sha)共用,免得
    只有某一个方法返回的 forward_meta 是字符串。
    """
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        fm = d.get("forward_meta")
        if isinstance(fm, str) and fm:
            try:
                d["forward_meta"] = json.loads(fm)
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


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
        self.migrated: list[str] = []

    def open(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA_TABLES)
        self.migrated = self._ensure_columns()
        self._conn.executescript(SCHEMA_INDEXES)
        # 只升不降:upgrade.run_once 会把 user_version 写成它自己的版本(2),
        # 如果这里每次都无条件写回 SCHEMA_VERSION(1),那个闸门就被抹掉了。
        cur = int(self._conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        if cur < SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def _ensure_columns(self) -> list[str]:
        """把旧版本的库补齐到当前 schema;返回实际新增的「表.列」列表."""
        c = self._require()
        added: list[str] = []
        for table, cols in _COLUMN_SPECS.items():
            have = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols.items():
                if name in have:
                    continue
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
        return added

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

    def recent(self, limit: int = 10) -> list[dict]:
        c = self._require()
        cur = c.execute(
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16, forward_meta FROM assets "
            # 同一秒内落盘的资产按自增 id 兜底排序 —— 只按 ts 时顺序不确定,
            # 与 recent_failures 的 ORDER BY ts DESC, id DESC 保持一致
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return _asset_rows(cur)

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
        return _asset_rows(c.execute(sql, params))

    @staticmethod
    def _asset_filter(kinds=None, group_id=None, q="") -> tuple[str, list]:
        """list_assets / summary 共用的 WHERE 片段（过滤语义只写一处）。"""
        where: list[str] = []
        params: list[Any] = []
        kinds = [k for k in (kinds or []) if k]
        if kinds:
            # v1.14.0 之前没有「动图」这个 kind,GIF 也记成 image —— 于是「动图」标签
            # 一个老文件都筛不出来、「图片」里却混着一堆 gif。这里按扩展名再分一次,
            # 两边互斥(同一行不会被两个标签都数一遍)。
            plain = [k for k in kinds if k not in ("image", "gif")]
            parts: list[str] = []
            if "image" in kinds:
                parts.append("(kind = 'image' AND lower(path) NOT LIKE '%.gif')")
            if "gif" in kinds:
                parts.append("(kind = 'gif' OR (kind = 'image' AND lower(path) LIKE '%.gif'))")
            if plain:
                parts.append("kind IN (" + ",".join("?" * len(plain)) + ")")
                params.extend(plain)
            where.append("(" + " OR ".join(parts) + ")")
        if group_id:
            where.append("group_id = ?")
            params.append(str(group_id))
        needle = (q or "").strip()
        if needle:
            where.append("(path LIKE ? OR msg_id LIKE ? OR sender_id LIKE ?)")
            like = "%" + needle + "%"
            params.extend([like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return clause, params

    def list_assets(self, *, limit: int = 100, offset: int = 0,
                    kinds: Optional[list] = None, group_id: Optional[str] = None,
                    q: str = "") -> dict:
        """分类管理用的资产列表（v1.14.0）.

        与 recent() 的区别:支持按 kind 过滤、按群过滤、按 path 模糊搜索,
        并且返回 total —— 页面分页需要它,不能靠「拿一页再数」猜。
        """
        c = self._require()
        clause, params = self._asset_filter(kinds, group_id, q)
        total = c.execute("SELECT COUNT(*) FROM assets" + clause, params).fetchone()[0]
        cols = ("SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
                "kind, path, size, sha16, forward_meta FROM assets" + clause
                + " ORDER BY ts DESC, id DESC")
        # v1.17.0: limit=None 表示取全部。页面分页以前固定只取 2000 行再在
        # 合并列表上切片 —— 第 2000 条之后永远翻不到（total 5012 却返回 0 条）。
        if limit is None or int(limit) <= 0:
            rows = _asset_rows(c.execute(cols, params))
        else:
            rows = _asset_rows(c.execute(
                cols + " LIMIT ? OFFSET ?",
                params + [max(1, int(limit)), max(0, int(offset))],
            ))
        return {"total": int(total), "items": rows}

    def find_by_sha(self, sha16: str) -> list[dict]:
        c = self._require()
        cur = c.execute(
            "SELECT id, ts, platform, group_id, sender_id, msg_id, idx, "
            "kind, path, size, sha16, forward_meta FROM assets WHERE sha16 = ?",
            (sha16,),
        )
        return _asset_rows(cur)

    def forget_path(self, path: str) -> int:
        """页面删掉一个扁平文件后,把对应的索引行也清掉(返回删除行数)。"""
        if not path:
            return 0
        c = self._require()
        cur = c.execute("DELETE FROM assets WHERE path = ?", (str(path),))
        c.commit()
        return int(cur.rowcount or 0)

    def delete_row(self, asset_id: int) -> int:
        """按行 id 删一条索引(页面清理派生文件用:同一 path 可能被多行共享,
        不能用 forget_path 把别人的行也带走)。"""
        c = self._require()
        cur = c.execute("DELETE FROM assets WHERE id = ?", (int(asset_id),))
        c.commit()
        return int(cur.rowcount or 0)

    def count(self) -> int:
        c = self._require()
        return c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def count_path(self, path: str) -> int:
        """同一个文件还有几行索引引用它（Storage 按内容去重会让多行共享一个文件）。

        页面删扁平文件前必须问这一句：以前直接 os.remove + 删掉该 path 的全部行，
        删一张共享图会连带删掉别人最多 10 条消息的索引行(v1.17.0 修复)。
        """
        if not path:
            return 0
        c = self._require()
        return int(c.execute("SELECT COUNT(*) FROM assets WHERE path = ?",
                             (str(path),)).fetchone()[0] or 0)

    def backfill_gif_kind(self) -> int:
        """v1.18.0: 老索引把 GIF 记成 image,靠页面按扩展名特判。

        归一化时一次性改掉,之后类型只看 kind 字段。
        """
        c = self._require()
        cur = c.execute(
            "UPDATE assets SET kind = 'gif' "
            "WHERE kind = 'image' AND lower(path) LIKE '%.gif'")
        c.commit()
        return int(cur.rowcount or 0)

    def set_user_version(self, version: int) -> None:
        c = self._require()
        c.execute("PRAGMA user_version = %d" % int(version))
        c.commit()

    def summary(self, *, kinds: Optional[list] = None,
                group_id: Optional[str] = None, q: str = "") -> dict:
        """同 list_assets 的过滤条件下:行数与总字节数（页面顶部统计用）。"""
        c = self._require()
        clause, params = self._asset_filter(kinds, group_id, q)
        row = c.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM assets"
                        + clause, params).fetchone()
        return {"total": int(row[0] or 0), "bytes": int(row[1] or 0)}

    def prune_older_than(self, cutoff_ts: int) -> dict:
        """删除 ts < cutoff 的 assets 与 failures;不删文件,只删索引.

        v1.9: 同时清掉同期的 failures 记录,避免失败表只涨不落。
        v1.10: 两张表都在删,所以返回 {"assets": n, "failures": m} —— 回执不再
               只报资产条数,把静默删掉的失败记录也说清楚。
        """
        c = self._require()
        n_assets = c.execute("DELETE FROM assets WHERE ts < ?",
                             (int(cutoff_ts),)).rowcount
        n_fail = c.execute("DELETE FROM failures WHERE ts < ?",
                           (int(cutoff_ts),)).rowcount
        c.commit()
        return {"assets": n_assets, "failures": n_fail}

    def stats(self) -> dict:
        c = self._require()
        total = c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        by_kind = dict(c.execute(
            "SELECT kind, COUNT(*) FROM assets GROUP BY kind"
        ).fetchall())
        total_bytes = c.execute(
            "SELECT COALESCE(SUM(size), 0) FROM assets"
        ).fetchone()[0]
        # 同一个文件可能被多行引用（Storage 按内容去重）：SUM 会把它的字节数
        # 重复计一遍。unique_bytes 按 path 去重，才是磁盘上的真实占用。
        unique_bytes = c.execute(
            "SELECT COALESCE(SUM(size), 0) FROM ("
            "SELECT DISTINCT path, size FROM assets)"
        ).fetchone()[0]
        fail_total = c.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        by_kind_fail = dict(c.execute(
            "SELECT kind, COUNT(*) FROM failures GROUP BY kind"
        ).fetchall())
        return {
            "total": total,
            "by_kind": by_kind,
            "total_bytes": int(total_bytes),
            "unique_bytes": int(unique_bytes),
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
        # 导出格式版本就是这里的整数 1(与插件版本 VERSION 无关).
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": out}, f,
                      ensure_ascii=False, indent=2)
        return len(out)
