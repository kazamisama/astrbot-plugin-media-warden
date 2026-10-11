"""按 MD5 在本地找图片 —— QQ/NapCat 图片缓存的兜底查找。

## 为什么需要这个

QQ 的图片下载链接（`multimedia.nt.qq.com.cn/download?appid=1407&fileid=…&rkey=…`）
里的 rkey 会失效，失效后返回 HTTP 400；而 NapCat 的 `get_msg` 只是**原样返回消息里
那条 url**（实测 `url_same_as_msg=true`），并不会重签，所以「重取」这条路救不了。

但图片本体其实**早就在本地**：QQ 客户端收到图片时就按 MD5 存了副本，

  * `Tencent Files/<QQ号>/Image/C2C/Image2/<md5>.<ext>`   ← 普通图片，扩展名保留原格式
  * `Tencent Files/<QQ号>/nt_qq/nt_data/Emoji/emoji-recv/<YYYY-MM>/Ori/<md5>.<ext>`  ← 表情

而消息段里的 `data.file`（我们记在 `Component.meta["raw_file"]`）就是 `<md5>.<ext>`。
所以下载失败时可以直接按这个 MD5 把本地副本读出来 —— **不需要 rkey，也不需要网络**。

（AstrBot 自己的 `PreProcessStage` 会把图转成 jpeg 静帧放进 `.astrbot/data/temp`，
但那个目录里往往什么也没有；这里查的是 QQ 的缓存，与 AstrBot 无关。）
"""
from __future__ import annotations

import os
from typing import Optional

# emoji-recv 按月分目录；搜太多会有 IO 代价，按「近几个月」倒序找
_EMOJI_MONTHS = 6
_MAX_SCAN_DIRS = 24


def _tencent_roots() -> list[str]:
    """可能的 QQ 数据根目录（Documents 位置可能被改过）。"""
    out: list[str] = []
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Documents"), home):
        p = os.path.join(base, "Tencent Files")
        if os.path.isdir(p):
            out.append(p)
    return out


def _account_dirs() -> list[str]:
    dirs: list[str] = []
    for root in _tencent_roots():
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    dirs.append(p)
        except OSError:
            continue
    return dirs


def find_cached_image(name: Optional[str]) -> Optional[str]:
    """name 是消息段里的 `data.file`（形如 `<md5>.<ext>`）；找到本地文件就返回绝对路径。

    先找 `Image/C2C/Image2`（扩展名与原始格式一致，动图是 gif），找不到再找
    `nt_qq/nt_data/Emoji/emoji-recv/*/Ori`。都不在就返回 None（调用方照常报失败）。
    """
    if not isinstance(name, str) or not name:
        return None
    base = os.path.basename(name).strip()
    stem, ext = os.path.splitext(base)
    if not stem or len(stem) < 16:  # QQ 的 md5 是 32 位；太短的不猜
        return None
    stem = stem.lower()
    ext = ext.lower()

    for acct in _account_dirs():
        # ① 普通图片缓存: <acct>/Image/C2C/Image2/
        d = os.path.join(acct, "Image", "C2C", "Image2")
        if os.path.isdir(d):
            hit = _match_in(d, stem, ext, recursive=False)
            if hit:
                return hit
        # ② 表情缓存: <acct>/nt_qq/nt_data/Emoji/emoji-recv/<YYYY-MM>/Ori/
        d = os.path.join(acct, "nt_qq", "nt_data", "Emoji", "emoji-recv")
        if os.path.isdir(d):
            hit = _match_in_emoji(d, stem, ext)
            if hit:
                return hit
    return None


def _match_in(dirpath: str, stem: str, ext: str, *, recursive: bool) -> Optional[str]:
    """在 dirpath 里按 md5 找同名文件（大小写不敏感）。"""
    if ext:
        for cand in (stem + ext, stem + ext.upper()):
            p = os.path.join(dirpath, cand)
            if os.path.isfile(p):
                return p
    try:
        names = os.listdir(dirpath)
    except OSError:
        return None
    for n in names:
        s, e = os.path.splitext(n)
        if s.lower() == stem:
            return os.path.join(dirpath, n)
    if recursive:
        # 兜底:一天一层/一个月一层的子目录
        try:
            for sub in os.listdir(dirpath):
                p = os.path.join(dirpath, sub)
                if os.path.isdir(p):
                    hit = _match_in(p, stem, ext, recursive=False)
                    if hit:
                        return hit
        except OSError:
            pass
    return None


def _match_in_emoji(dirpath: str, stem: str, ext: str) -> Optional[str]:
    """emoji-recv/<YYYY-MM>/Ori 结构:按月份倒序找近几个月的 Ori 目录。"""
    try:
        months = sorted(
            (n for n in os.listdir(dirpath) if os.path.isdir(os.path.join(dirpath, n))),
            reverse=True,
        )[:_EMOJI_MONTHS]
    except OSError:
        return None
    scanned = 0
    for month in months:
        for sub in ("Ori", "Thumb"):
            d = os.path.join(dirpath, month, sub)
            if not os.path.isdir(d):
                continue
            scanned += 1
            if scanned > _MAX_SCAN_DIRS:
                return None
            hit = _match_in(d, stem, ext, recursive=False)
            if hit:
                return hit
    return None
