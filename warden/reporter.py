"""回复组装 —— 把保存结果格式化成 AstrBot 友好输出.

Phase 1 仅做格式骨架,Phase 2 接入真实数据.
"""
from __future__ import annotations
import posixpath
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AssetResult:
    kind: str
    ok: bool
    path: Optional[str] = None
    err: Optional[str] = None
    size: Optional[int] = None
    preview_path: Optional[str] = None
    reused: bool = False


def _lcp_dir(paths: List[str]) -> Optional[str]:
    """所有路径的最长公共目录前缀(posix 视角).

    - 返回 None 表示没有有意义的公共目录(分歧 / 仅根 / 仅盘符)
    - 例子: ['/a/b/x.jpg', '/a/b/y.jpg'] -> '/a/b/'
    """
    if not paths:
        return None
    s = paths[0]
    for p in paths[1:]:
        i = 0
        while i < len(s) and i < len(p) and s[i] == p[i]:
            i += 1
        s = s[:i]
        if not s:
            return None
    if not s or s == "/":
        return None
    if s.endswith(":") or s.endswith(":/") or s.endswith(":\\\\"):
        return None
    # 把 s 截到最近的目录分隔符
    cut = s.rfind("/")
    if cut <= 0:
        return None
    return s[: cut + 1]


@dataclass
class BatchResult:
    items: List[AssetResult] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def ok_count(self) -> int:
        return sum(1 for x in self.items if x.ok)

    @property
    def fail_count(self) -> int:
        return self.total - self.ok_count

    @property
    def storage_root(self) -> Optional[str]:
        ok_paths = [x.path for x in self.items if x.ok and x.path]
        # 统一到 posix 视角做前缀比较
        norm = [p.replace("\\\\", "/") for p in ok_paths]
        return _lcp_dir(norm)


def format_batch(res: BatchResult) -> str:
    if res.total == 0:
        return "[Warden] (no assets)"

    reused_count = sum(1 for x in res.items if x.ok and x.reused)
    if res.fail_count == 0:
        if reused_count == res.ok_count and res.ok_count > 0:
            head = "[Warden] ✅ 已保存（全部为已保存过的重复内容）"
        else:
            head = "[Warden] ✅ 已保存"
    else:
        head = f"[Warden] ⚠️ 部分保存失败 ({res.ok_count}/{res.total})"

    lines = [head]
    if reused_count and reused_count != res.ok_count:
        lines.append(f"  去重: {reused_count} 项为已保存过的重复内容")
    lines.append(f"  用时: {res.duration_s:.2f}s")

    from collections import Counter
    kinds = Counter(x.kind for x in res.items)
    kind_summary = ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items()))
    lines.append(f"  类型: {kind_summary}")

    root = res.storage_root
    if root:
        lines.append(f"  路径: {root}")

    ok_items = [x for x in res.items if x.ok]
    if ok_items:
        lines.append(f"  成功 ({len(ok_items)}):")
        for x in ok_items[:10]:
            sz = f" {x.size}B" if x.size else ""
            tag = " （已保存过·复用原位置）" if x.reused else ""
            lines.append(f"    - {x.kind}: {x.path}{sz}{tag}")
        if len(ok_items) > 10:
            lines.append(f"    ... (+{len(ok_items) - 10} more)")

    fail_items = [x for x in res.items if not x.ok]
    if fail_items:
        lines.append(f"  失败 ({len(fail_items)}):")
        for x in fail_items[:10]:
            lines.append(f"    - {x.kind}: {x.err}")
        if len(fail_items) > 10:
            lines.append(f"    ... (+{len(fail_items) - 10} more)")

    return "\n".join(lines)
