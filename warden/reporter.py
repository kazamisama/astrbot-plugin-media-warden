"""回复组装 —— 把保存结果格式化成 AstrBot 友好输出.

Phase 1 仅做格式骨架,Phase 2 接入真实数据.

v1.9: 失败原因分类(下载失败 / 落盘失败),让回执说清是"源链接失效拿不到料"
还是"拿到料但写不进磁盘"。
v1.10: 三种失败文案(failure_phrase / 原因行 / 回执头)统一走 _split_fetch_save,
不再各处重算一遍 fetch/save 分解。
"""
from __future__ import annotations
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
    # v1.9: 失败分类("fetch"/"save"/"other")与补充说明(补救链结果 / 历史失败次数 / 链接已重取)
    failure_kind: Optional[str] = None
    note: Optional[str] = None


def _as_segments(p: str) -> List[str]:
    """路径切段: 兼容 / 与反斜杠;前导空段表示绝对路径."""
    return p.replace("\\", "/").split("/")


def _lcp_dir(paths: List[str]) -> Optional[str]:
    """所有路径的最长公共目录前缀(posix 视角).

    - 返回 None 表示没有有意义的公共目录(分歧 / 仅根 / 仅盘符)
    - 例子: ['/a/b/x.jpg', '/a/b/y.jpg'] -> '/a/b/'
    - v1.11.0: 按「段」比较而非按字符比较 —— 按字符会在 'C:/a/x.jpg' 与
      'C:/ab/y.jpg' 之间切出 'C:/a'(它是另一个文件名的前缀,不是目录);
      以前还会先把 '\\' 换成 '/' 再比较,所以 Windows 路径从未显示出「路径:」行。
    """
    if not paths:
        return None
    seg_list = [_as_segments(p) for p in paths]
    common = seg_list[0][:-1]  # 去掉文件名,只留目录段
    for segs in seg_list[1:]:
        n = 0
        while n < len(common) and n < len(segs) - 1 and common[n] == segs[n]:
            n += 1
        common = common[:n]
        if not common:
            return None
    if not common or common == [""]:
        return None
    # 只剩盘符(C: / D:)没有意义
    if len(common) == 1 and common[0].endswith(":"):
        return None
    return "/".join(common) + "/"


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
        # 统一到 posix 视角做前缀比较(单个反斜杠: 以前写成了两个,Windows 路径从未被归一)
        norm = [p.replace("\\", "/") for p in ok_paths]
        return _lcp_dir(norm)

    @property
    def fail_items(self) -> List["AssetResult"]:
        return [x for x in self.items if not x.ok]

    def failure_breakdown(self) -> dict:
        """失败的分类计数: fetch / save / other."""
        out: dict = {}
        for x in self.fail_items:
            cat = x.failure_kind or classify_error(x.err)
            out[cat] = out.get(cat, 0) + 1
        return out

    def _split_fetch_save(self) -> tuple[int, int]:
        """(拿不到料, 拿到料但写不进去/其它) —— 三种文案共用的唯一算法.

        "other" 归到第二类:它同样不是源链接的问题,@ 文案里不该说"链接失效"。
        """
        fetch_n = self.failure_breakdown().get("fetch", 0)
        return fetch_n, self.fail_count - fetch_n

    def failure_phrase(self) -> str:
        """给 @ 触发者的那句话:说清是「源链接拿不到」还是「写不进磁盘」。"""
        if self.fail_count == 0:
            return "0 个媒体保存失败"
        fetch_n, save_n = self._split_fetch_save()
        if fetch_n and not save_n:
            return (f"{self.fail_count} 个媒体没能保存 —— 下载失败(源链接已失效)"
                    f"，{self.ok_count}/{self.total} 已保存")
        if save_n and not fetch_n:
            return (f"{self.fail_count} 个媒体没能保存 —— 落盘失败(写入磁盘出错)"
                    f"，{self.ok_count}/{self.total} 已保存")
        return (f"{self.fail_count}/{self.total} 个媒体保存失败"
                f"（下载失败 {fetch_n} + 落盘失败 {save_n}）")


# 失败原因分类 —— 前缀由 main._store_dl / aiohttp_fetcher / 转发渲染写入
_ERROR_CATEGORY_LABELS = {
    "fetch": "下载失败（源链接失效）",
    "save": "落盘失败（写入磁盘/存储）",
    "other": "其它失败",
}


def classify_error(err: Optional[str]) -> str:
    """把失败原因归到 fetch / save / other.

    fetch: 走网络拿料的一步失败 —— GetMediaError("get_media: ...") /
           DownloadError("download: ..." / "http 400 ...") /
           转发节点图 "image download failed (...)" /
           "download: timeout / connection error ..."
    save:  拿到料但落盘失败 —— "save: OSError..." / "json save: ..." / "render: ..."
    """
    msg = (err or "").lower()
    if "image download failed" in msg:
        return "fetch"
    for key in ("download:", "get_media:", "http ", "timeout", "connection error"):
        if key in msg:
            return "fetch"
    for key in ("save:", "json save:", "render:"):
        if key in msg:
            return "save"
    return "other"


def _reason_line(breakdown: dict) -> str:
    """失败回执里的一行明细(按分类计数)。"""
    parts = [
        f"{_ERROR_CATEGORY_LABELS.get(cat, cat)}×{n}"
        for cat, n in sorted(breakdown.items())
    ]
    if len(parts) == 1:
        return f"  原因: {parts[0]}"
    return "  原因: " + "; ".join(parts)


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
        fetch_n, save_n = res._split_fetch_save()
        if fetch_n == res.fail_count:
            detail = "下载失败（源链接失效）"
        elif fetch_n == 0:
            detail = "落盘失败"
        else:
            detail = f"下载失败 {fetch_n} + 落盘失败 {save_n}"
        head = (
            f"[Warden] ⚠️ 部分保存失败 ({res.ok_count}/{res.total})"
            f" — {detail}"
        )

    lines = [head]
    if reused_count and reused_count != res.ok_count:
        lines.append(f"  去重: {reused_count} 项为已保存过的重复内容")
    lines.append(f"  用时: {res.duration_s:.2f}s")
    if res.fail_count:
        lines.append(_reason_line(res.failure_breakdown()))

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
            note = f"  [{x.note}]" if x.note else ""
            lines.append(f"    - {x.kind}: {x.path}{sz}{tag}{note}")
        if len(ok_items) > 10:
            lines.append(f"    ... (+{len(ok_items) - 10} more)")

    fail_items = res.fail_items
    if fail_items:
        lines.append(f"  失败 ({len(fail_items)}):")
        for x in fail_items[:10]:
            note = f"  [{x.note}]" if x.note else ""
            lines.append(f"    - {x.kind}: {x.err}{note}")
        if len(fail_items) > 10:
            lines.append(f"    ... (+{len(fail_items) - 10} more)")

    return "\n".join(lines)
