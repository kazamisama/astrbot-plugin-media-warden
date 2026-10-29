"""v1.18.0 一次性归一化:把历史遗留形态收敛到当前存储模型。

原则(2026-10-29 用户定):不为旧数据长期保留补丁。升级时跑一次归一化,
之后代码里不再有「按扩展名猜类型」「读 legacy 副本」「启动时自动迁移」这些分支。

归一化的内容:
  1. 条目补 kind(v1.12/v1.13 的老 record.json 没有这个字段);
  2. 删掉迁移期留下的 legacy_forward.json 与 legacy_* 字段
     (record.json 里已有归一化后的节点,重发/重渲染不需要原始副本);
  3. 索引里老的 GIF 行 kind 从 image 改成 gif(以前靠扩展名特判);
  4. 删掉迁移失败留下的空壳(57 B、nodes:null 的扁平 forward JSON);
  5. v1.16 之前每次转发额外落的扁平渲染图:打包备份后删除(1.22 GB 级)。

闸门是 <storage_root>/.schema_version(与索引的 PRAGMA user_version 同步),
所有步骤幂等 —— 中途崩溃下次启动重跑不会二次破坏。
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from typing import Any, Optional

from . import record as record_store

SCHEMA_VERSION = 2
MARKER_NAME = ".schema_version"
# 迁移期留下的原始副本文件名(只有这里还认它:归一化时要删掉)
LEGACY_FILENAME = "legacy_forward.json"
# 迁移失败留下的空壳:{"forward": true, "node_count": 0, "nodes": null} 这种
_JUNK_MAX_BYTES = 256


def marker_path(storage_root: str) -> str:
    return os.path.join(storage_root, MARKER_NAME)


def read_version(storage_root: str) -> int:
    try:
        with open(marker_path(storage_root), "r", encoding="utf-8") as fh:
            return int((fh.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


def write_version(storage_root: str, version: int) -> None:
    os.makedirs(storage_root, exist_ok=True)
    with open(marker_path(storage_root), "w", encoding="utf-8") as fh:
        fh.write(str(int(version)) + "\n")


def _iter_record_dirs(storage_root: str):
    base = record_store.records_root(storage_root)
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if os.path.isdir(path):
            yield name, path


def _dump(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def normalize_entries(storage_root: str) -> dict:
    """补 kind、删 legacy 副本与字段。返回 {entries, legacy_removed}。"""
    touched = 0
    legacy_removed = 0
    for _name, path in _iter_record_dirs(storage_root):
        rec_path = os.path.join(path, record_store.RECORD_FILENAME)
        if os.path.exists(rec_path):
            try:
                with open(rec_path, "r", encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                rec = None
            if isinstance(rec, dict):
                changed = False
                for key in ("legacy_file", "legacy_migrated", "legacy_source"):
                    if key in rec:
                        rec.pop(key, None)
                        changed = True
                if not rec.get("kind"):
                    rec["kind"] = "forward"
                    changed = True
                if changed:
                    _dump(rec_path, rec)
                    touched += 1
        legacy = os.path.join(path, LEGACY_FILENAME)
        if os.path.exists(legacy):
            try:
                os.remove(legacy)
                legacy_removed += 1
            except OSError:
                pass
    return {"entries": touched, "legacy_removed": legacy_removed}


def _drop_junk_forwards(index) -> dict:
    """删掉迁移失败的空壳行(小体积扁平 forward JSON,且 nodes 为空)。"""
    rows = index.list_assets(kinds=["forward"], limit=None).get("items") or []
    dropped = 0
    for row in rows:
        path = str(row.get("path") or "")
        if not path.lower().endswith(".json"):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > _JUNK_MAX_BYTES:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("nodes"):
            continue
        asset_id = row.get("asset_id") or row.get("id")
        try:
            index.delete_row(int(asset_id))
        except (TypeError, ValueError):
            continue
        except Exception:
            continue
        dropped += 1
        if not index.count_path(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return {"junk_rows": dropped}


def purge_legacy_renders(index, backup_zip: Optional[str] = None,
                         *, limit: Optional[int] = None) -> dict:
    """打包备份后删掉 v1.16 之前额外落的扁平渲染图(kind=forward_render)。"""
    rows = index.list_assets(kinds=["forward_render"],
                             limit=limit).get("items") or []
    if not rows:
        return {"rows": 0, "files": 0, "bytes": 0, "backup": "",
                "backup_files": 0}
    by_path: dict[str, list[dict]] = {}
    for row in rows:
        by_path.setdefault(str(row.get("path") or ""), []).append(row)
    manifest = [dict(row) for row in rows]
    packed = 0
    packed_bytes = 0
    if backup_zip:
        parent = os.path.dirname(os.path.abspath(backup_zip))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 不同群的落盘文件名可以重名(模板相同):arcname 带序号去重,否则
        # zipfile 会写两条同名 entry(解压时只剩最后一条,备份等于残缺)。
        used: dict[str, int] = {}
        with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(
                {"exported_at": int(time.time()), "rows": manifest},
                ensure_ascii=False, indent=1))
            for path in by_path:
                if not path or not os.path.exists(path):
                    continue
                base = os.path.basename(path)
                n = used.get(base, 0)
                used[base] = n + 1
                arcname = os.path.join("files", base) if n == 0 else os.path.join(
                    "files", "%04d_%s" % (n + 1, base))
                try:
                    size = os.path.getsize(path)
                    zf.write(path, arcname=arcname)
                    packed += 1
                    packed_bytes += size
                except OSError:
                    continue
    removed_rows = 0
    removed_files = 0
    removed_bytes = 0
    for path, items in by_path.items():
        for row in items:
            asset_id = row.get("asset_id") or row.get("id")
            try:
                removed_rows += int(index.delete_row(int(asset_id)) or 0)
            except (TypeError, ValueError):
                continue
            except Exception:
                continue
        if not path or index.count_path(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        removed_files += 1
        removed_bytes += size
    return {"rows": removed_rows, "files": removed_files, "bytes": removed_bytes,
            "packed": packed, "packed_bytes": packed_bytes,
            "backup": backup_zip or "", "backup_files": packed}


def run_once(storage_root: str, index=None, *, backup_zip: Optional[str] = None,
             purge_renders: bool = True) -> dict:
    """跑一次归一化;已跑过(版本 >= SCHEMA_VERSION)直接跳过。"""
    if read_version(storage_root) >= SCHEMA_VERSION:
        return {"skipped": True, "version": SCHEMA_VERSION}
    report: dict[str, Any] = {"skipped": False, "version": SCHEMA_VERSION}
    report.update(normalize_entries(storage_root))
    if index is not None:
        try:
            report["gif_rows"] = index.backfill_gif_kind()
        except Exception as exc:  # 归一化失败不该拦住插件启动
            report["gif_error"] = repr(exc)
        try:
            report.update(_drop_junk_forwards(index))
        except Exception as exc:
            report["junk_error"] = repr(exc)
        if purge_renders:
            try:
                report.update(purge_legacy_renders(index, backup_zip))
            except Exception as exc:
                report["render_error"] = repr(exc)
        try:
            index.set_user_version(SCHEMA_VERSION)
        except Exception:
            pass
    write_version(storage_root, SCHEMA_VERSION)
    return report
