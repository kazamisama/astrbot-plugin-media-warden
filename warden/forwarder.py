"""转发链渲染器 —— 把 OneBot `node` 节点数组渲染成单张 PNG.

设计:
  - 自绘气泡:白底圆角 + 发送者 + 时间 + 文本 + 节点内图片缩放贴图
  - 节点内图片异步下载 -> BytesIO -> PIL -> 缩放到最大 360 宽
  - 节点超 N 自动降级:不渲染 image,只把 nodes 落 JSON sidecar
  - 字体:跨平台找系统中文字体 (微软雅黑 / Noto Sans CJK / PingFang)
  - 没有 PIL 时 import 失败 -> ImportError 由调用方处理

公开 API:
  - Forwarder(width, max_nodes, bg_color, text_color, font_size)
  - forwarder.render(nodes, downloader) -> bytes  (PNG)
  - forwarder.can_handle(nodes) -> bool  (判断是否要降级)
"""
from __future__ import annotations
import io
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional


# ----------------- 字体探测 -----------------

_FONT_CANDIDATES = [
    # Windows
    r"C:\\Windows\\Fonts\\msyh.ttc",
    r"C:\\Windows\\Fonts\\msyh.ttf",
    r"C:\\Windows\\Fonts\\simhei.ttf",
    r"C:\\Windows\\Fonts\\simsun.ttc",
    # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _find_font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


# ----------------- emoji 探测 + 字体探测 + 混合 layout -----------------

_EMOJI_RANGES = (
    (0x2600, 0x27BF),       # Misc Symbols + Dingbats
    (0x1F000, 0x1FFFF),     # 整个 SMP emoji 区段(含 1F300-1FAFF + 旗 1F1E6-1F1FF)
)

_EMOJI_SINGLES = frozenset({
    0xFE0F, 0x200D, 0x20E3,             # VS-16, ZWJ, Keycap
    0x2B05, 0x2B06, 0x2B07,             # Arrows
    0x2B1B, 0x2B1C, 0x2B50, 0x2B55,     # Squares / Star / Circle
})


def _is_emoji_char(ch: str) -> bool:
    """判断单字符是否在 emoji 段(含 ZWJ / variation selector / keycap)。"""
    if not ch:
        return False
    cp = ord(ch)
    if cp in _EMOJI_SINGLES:
        return True
    for lo, hi in _EMOJI_RANGES:
        if lo <= cp <= hi:
            return True
    return False


_EMOJI_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]


def _find_emoji_font(size: int):
    """探测系统 emoji 字体(通常 CBDT / Apple color)。
    Pillow 12 仅能渲染出 B&W glyph(不解析 CBDT 彩表),但足够消除豆腐。
    返回 None 时调用方应回退纯文本 layout(不切段)。"""
    from PIL import ImageFont
    for path in _EMOJI_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return None


def _char_w(ch: str, font) -> int:
    bbox = font.getbbox(ch)
    return (bbox[2] - bbox[0]) if bbox else 0


def _wrap_mixed(text: str, font_text, font_emoji, max_w: int):
    """text → list[list[(seg, is_emoji)]]。emoji 段保持原子(ZWJ 链 + VS 一并吞),不跨行拆。
    \\n 视为强制换行(等价于 wrap_text 行为)。
    """
    if not text:
        return []
    lines = [[]]
    cur_w = 0
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append([])
            cur_w = 0
            continue
        if cur_w > 0:
            # 上一段已有内容 —— \\n 必须开新行,等价 wrap_text 的 lines.append(cur)
            lines.append([])
            cur_w = 0
        i = 0
        while i < len(paragraph):
            ch = paragraph[i]
            is_emoji = _is_emoji_char(ch)
            cluster = [ch]
            j = i + 1
            if is_emoji:
                # 扩展 ZWJ 链 + 后续 emoji / VS-16
                while j < len(paragraph):
                    cj = paragraph[j]
                    if cj == "\u200d" and j + 1 < len(paragraph) and _is_emoji_char(paragraph[j + 1]):
                        cluster.append(cj)
                        cluster.append(paragraph[j + 1])
                        j += 2
                    elif cj == "\ufe0f" or _is_emoji_char(cj):
                        cluster.append(cj)
                        j += 1
                    else:
                        break
            else:
                # 合并连续非 emoji 字符(确保 wrap_text 行为等价:一次性 draw 整段字符串)
                while j < len(paragraph) and not _is_emoji_char(paragraph[j]):
                    cluster.append(paragraph[j])
                    j += 1
            cluster_str = "".join(cluster)
            font = font_emoji if is_emoji else font_text
            cw = sum(_char_w(c, font) for c in cluster)
            if not is_emoji and cw > max_w:
                # 非 emoji cluster 自身超 max_w:char-level 切行
                # (emoji cluster 必须保持原子,即便超宽也只能塞当前行)
                cur_seg = ""
                cur_seg_w = 0
                for c in cluster:
                    cw_c = _char_w(c, font)
                    if cur_seg_w + cw_c > max_w and cur_seg:
                        lines[-1].append((cur_seg, False))
                        lines.append([])
                        cur_seg = c
                        cur_seg_w = cw_c
                    else:
                        cur_seg += c
                        cur_seg_w += cw_c
                if cur_seg:
                    lines[-1].append((cur_seg, False))
                cur_w = cur_seg_w
            else:
                if cur_w + cw > max_w and lines[-1]:
                    lines.append([])
                    cur_w = 0
                lines[-1].append((cluster_str, is_emoji))
                cur_w += cw
            i = j
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _line_h_mixed(line, font_text, font_emoji, base_size: int) -> int:
    """混合行高 = 段内最大 bbox 高度。"""
    if not line:
        return 0
    h = 0
    for seg, is_emoji in line:
        font = font_emoji if is_emoji else font_text
        bbox = font.getbbox(seg)
        seg_h = (bbox[3] - bbox[1]) if bbox else base_size + 4
        if seg_h > h:
            h = seg_h
    return h


# ----------------- 头像圆形 mask -----------------

def _circle_mask(size: int):
    """生成 size×size 'L' 模式圆形 mask(白圆黑底)。用于 img.paste(avatar, pos, mask=mask) 切圆。"""
    from PIL import Image, ImageDraw
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    return mask


# ----------------- 节点归一化 -----------------

@dataclass
class ForwardNode:
    sender_name: str
    sender_id: str
    text: str
    time: int          # unix seconds
    image_urls: List[str]   # 节点内图片 URL 列表
    nesting_depth: int = 0
    avatar_path: Optional[str] = None   # v1.7.0: 本地头像文件路径,None = 不画头像
    # v1.9.5: 与 image_urls 一一对应的原始 segment data(file/url/file_size),
    # 用来在 url 失效时按 `file`(MD5) 找 QQ 本地缓存。默认空 = 只有 url 可用。
    image_refs: List[dict] = field(default_factory=list)


def _coerce_nodes(raw_nodes) -> List[ForwardNode]:
    """raw_nodes: OneBot 风格的 [ {type, data}, ... ] 列表 / 或 dict-of-content.

    我们尽量宽容:每个节点可以是 dict (with .data.sender_id / .data.sender_name
    / .data.content / .data.time / .data.message) 或直接是 seg 列表.
    """
    out: List[ForwardNode] = []
    if not isinstance(raw_nodes, list):
        return out
    for nd in raw_nodes:
        if not isinstance(nd, dict):
            continue
        data = nd.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        # napcat get_forward_msg 返回的节点: sender 是嵌套 dict, 内容在顶层 message,
        # time 在顶层; OneBot node 段: 信息在 data.* 里。两者都兼容。
        sender = nd.get("sender") if isinstance(nd.get("sender"), dict) else {}
        sender_id = str(
            data.get("user_id") or data.get("sender_id") or data.get("uin")
            or sender.get("user_id") or sender.get("uin") or "?"
        )
        sender_name = str(
            data.get("nickname") or data.get("name")
            or sender.get("nickname") or sender.get("card")
            or sender_id
        )
        ts = data.get("time")
        if ts is None:
            ts = nd.get("time")
        try:
            ts = int(ts) if ts is not None else 0
        except (TypeError, ValueError):
            ts = 0
        try:
            nesting_depth = int(
                nd.get("__warden_nested_depth")
                or data.get("__warden_nested_depth")
                or 0
            )
        except (TypeError, ValueError):
            nesting_depth = 0
        nesting_depth = max(0, min(nesting_depth, 2))
        # content: 优先 data.content (OneBot), 退到 data.message / nd.message / nd.content
        content = (
            data.get("content")
            or nd.get("content")
            or data.get("message")
            or nd.get("message")
        )
        text_parts: List[str] = []
        image_urls: List[str] = []
        image_refs: List[dict] = []
        if isinstance(content, list):
            for seg in content:
                if not isinstance(seg, dict):
                    continue
                t = (seg.get("type") or "").lower()
                d = seg.get("data") or {}
                if t == "text":
                    text_parts.append(d.get("text", "") or "")
                elif t == "image":
                    url = d.get("url") or d.get("file")
                    if url:
                        image_urls.append(url)
                        # 保留整个 data:url 失效时 `file`(MD5) 是本地缓存的钥匙
                        image_refs.append(dict(d) if isinstance(d, dict) else {})
                elif t == "json":
                    text_parts.append("[json]")
        elif isinstance(content, str):
            text_parts.append(content)
        out.append(ForwardNode(
            sender_name=sender_name,
            sender_id=sender_id,
            text="\n".join([p for p in text_parts if p]),
            time=ts,
            image_urls=image_urls,
            image_refs=image_refs,
            nesting_depth=nesting_depth,
        ))
    return out


# ----------------- 渲染器 -----------------

@dataclass
class _RenderCfg:
    width: int = 720
    bg: tuple = (245, 245, 247)
    bubble_bg: tuple = (255, 255, 255)
    bubble_border: tuple = (220, 220, 224)
    header_color: tuple = (120, 120, 130)
    text_color: tuple = (30, 30, 35)
    time_color: tuple = (150, 150, 160)
    font_size: int = 18
    header_size: int = 16
    padding: int = 16
    gap: int = 12
    bubble_radius: int = 10
    max_image_width: int = 720
    max_image_height: int = 720
    title: str = "[合并转发]"
    # v1.7.0: 头像(与 MediaWardenConfig 默认对齐,Forwarder 可独立使用)
    enable_user_avatar: bool = True
    avatar_size: int = 36


def _node_depth(node: ForwardNode) -> int:
    try:
        return max(0, min(int(getattr(node, "nesting_depth", 0)), 2))
    except (TypeError, ValueError):
        return 0


def _bubble_layout(cfg: _RenderCfg, node: ForwardNode):
    depth = _node_depth(node)
    indent = depth * 6
    bubble_x = cfg.padding + indent
    bubble_w = max(160, cfg.width - cfg.padding * 2 - indent)
    return depth, bubble_x, bubble_w


def _bubble_style(cfg: _RenderCfg, node: ForwardNode):
    if _node_depth(node) <= 0:
        return cfg.bubble_bg, cfg.bubble_border
    return (250, 252, 255), (174, 195, 224)


def _nested_line_color(node: ForwardNode):
    depth = _node_depth(node)
    if depth == 1:
        return (104, 151, 217)
    if depth == 2:
        return (151, 122, 214)
    return (174, 195, 224)


class Forwarder:
    def __init__(self, width: int = 720, max_nodes: int = 30):
        self.cfg = _RenderCfg(width=width)
        self.max_nodes = max_nodes
        self._font = None
        self._font_header = None
        self._font_time = None
        self._fonts_loaded = False

    def _ensure_fonts(self):
        if self._fonts_loaded:
            return
        self._font = _find_font(self.cfg.font_size)
        self._font_header = _find_font(self.cfg.header_size)
        self._font_time = _find_font(self.cfg.header_size - 2)
        self._font_emoji = _find_emoji_font(self.cfg.font_size)
        self._fonts_loaded = True

    def _scale_image(self, im: "Image.Image", text_w: int):
        """按 text_w 和 cfg.max_image_width 双轴上界缩放,返回 (nw, nh).

        max(w, 1) 防 0 除;最小 1x1 防 PIL 拒绝 0 尺寸。
        """
        w, ih = im.size
        scale = min(text_w / max(w, 1), self.cfg.max_image_width / max(w, 1))
        return max(1, int(w * scale)), max(1, int(ih * scale))

    def can_handle(self, nodes: List[ForwardNode]) -> bool:
        return 1 <= len(nodes) <= self.max_nodes

    async def render(self, nodes: List[ForwardNode],
                     image_downloader: Optional[Callable[[str], Awaitable[bytes]]] = None
                     ) -> bytes:
        """渲染为 PNG bytes."""
        from PIL import Image, ImageDraw
        self._ensure_fonts()
        cfg = self.cfg

        # 一次性测量:每节点所需高度
        node_heights: List[int] = []
        line_heights_cache: dict = {}

        def line_h(font, text):
            key = (id(font), text)
            if key in line_heights_cache:
                return line_heights_cache[key]
            bbox = font.getbbox(text)
            h = bbox[3] - bbox[1] if bbox else self.cfg.font_size + 4
            line_heights_cache[key] = h
            return h

        def wrap_text(text: str, font, max_w: int) -> List[str]:
            if not text:
                return []
            lines: List[str] = []
            for paragraph in text.split("\n"):
                if not paragraph:
                    lines.append("")
                    continue
                # 按 char 切 + 像素累加
                cur = ""
                for ch in paragraph:
                    cand = cur + ch
                    bbox = font.getbbox(cand)
                    w = bbox[2] - bbox[0] if bbox else 0
                    if w <= max_w or not cur:
                        cur = cand
                    else:
                        lines.append(cur)
                        cur = ch
                if cur:
                    lines.append(cur)
            return lines

        # 预下载图片 -> PIL 或 None
        node_images: List[List["Image.Image"]] = []
        if image_downloader is not None:
            for nd in nodes:
                imgs = []
                for url in nd.image_urls:
                    try:
                        data = await image_downloader(url)
                        from PIL import Image as _I
                        im = _I.open(io.BytesIO(data)).convert("RGBA")
                        im.load()
                        imgs.append(im)
                    except Exception:
                        continue
                node_images.append(imgs)
        else:
            node_images = [[] for _ in nodes]

        # 逐节点计算高度(混合 layout:emoji 段走 emoji 字体)
        for nd, imgs in zip(nodes, node_images):
            _, _, bubble_w = _bubble_layout(cfg, nd)
            has_avatar = nd.avatar_path is not None and self.cfg.enable_user_avatar
            avatar_offset = (self.cfg.avatar_size + 6) if has_avatar else 0
            text_w = bubble_w - cfg.padding * 2 - avatar_offset
            if self._font_emoji is not None:
                lines = _wrap_mixed(nd.text, self._font, self._font_emoji, text_w)
            else:
                lines = wrap_text(nd.text, self._font, text_w)
            h = cfg.padding  # 顶 padding
            _hdr_h = line_h(self._font_header, nd.sender_name or "anon")
            if nd.avatar_path is not None and self.cfg.enable_user_avatar:
                _hdr_h = max(_hdr_h, self.cfg.avatar_size)
            h += _hdr_h
            h += 4
            for ln in lines:
                if self._font_emoji is not None:
                    h += _line_h_mixed(ln, self._font, self._font_emoji, self.cfg.font_size) + 2
                else:
                    h += line_h(self._font, ln) + 2
            if nd.time:
                h += 6 + line_h(self._font_time, _fmt_time(nd.time))
            if imgs:
                h += 8
            for im in imgs:
                _, nh = self._scale_image(im, text_w)
                h += nh + 6
            h += cfg.padding  # 底 padding
            node_heights.append(h)

        total_h = cfg.padding * 2  # 上下 padding
        total_h += line_h(self._font_header, cfg.title) + 8
        for nh in node_heights:
            total_h += nh + cfg.gap
        if node_heights:
            total_h -= cfg.gap  # 最后一个 gap 不用
        total_h = max(total_h, 200)

        img = Image.new("RGB", (cfg.width, total_h), cfg.bg)
        draw = ImageDraw.Draw(img)

        y = cfg.padding
        # 标题
        draw.text((cfg.padding, y), cfg.title, fill=cfg.header_color,
                  font=self._font_header)
        y += line_h(self._font_header, cfg.title) + 8

        for nd, imgs, nh in zip(nodes, node_images, node_heights):
            depth, bubble_x, bubble_w = _bubble_layout(cfg, nd)
            bubble_bg, bubble_border = _bubble_style(cfg, nd)
            # 气泡背景
            draw.rounded_rectangle(
                (bubble_x, y, bubble_x + bubble_w, y + nh),
                radius=cfg.bubble_radius,
                fill=bubble_bg,
                outline=bubble_border,
            )
            if depth:
                draw.line(
                    (bubble_x + 7, y + 10, bubble_x + 7, y + nh - 10),
                    fill=_nested_line_color(nd),
                    width=3,
                )
            tx = bubble_x + cfg.padding
            ty = y + cfg.padding
            _avatar_im = None
            if nd.avatar_path is not None and self.cfg.enable_user_avatar:
                try:
                    _avatar_im = Image.open(nd.avatar_path).convert("RGBA")
                    _avatar_im = _avatar_im.resize(
                        (self.cfg.avatar_size, self.cfg.avatar_size),
                        getattr(getattr(Image, "Resampling", Image), "LANCZOS"),
                    )
                except Exception:
                    _avatar_im = None
            if _avatar_im is not None:
                _mask = _circle_mask(self.cfg.avatar_size)
                img.paste(_avatar_im, (tx, ty), mask=_mask)
                tx += self.cfg.avatar_size + 6
            draw.text((tx, ty), nd.sender_name or "anon",
                      fill=cfg.header_color, font=self._font_header)
            _hdr_h = line_h(self._font_header, nd.sender_name or "anon")
            if _avatar_im is not None:
                _hdr_h = max(_hdr_h, self.cfg.avatar_size)
            ty += _hdr_h + 4
            # 文本(混合 layout:emoji 段走 emoji 字体)
            has_avatar = nd.avatar_path is not None and self.cfg.enable_user_avatar
            avatar_offset = (self.cfg.avatar_size + 6) if has_avatar else 0
            text_w = bubble_w - cfg.padding * 2 - avatar_offset
            if self._font_emoji is not None:
                _mixed = _wrap_mixed(nd.text, self._font, self._font_emoji, text_w)
                for ln_runs in _mixed:
                    cur_x = tx
                    for seg, is_emoji in ln_runs:
                        _f = self._font_emoji if is_emoji else self._font
                        draw.text((cur_x, ty), seg, fill=cfg.text_color, font=_f)
                        cur_x += sum(_char_w(c, _f) for c in seg)
                    ty += _line_h_mixed(ln_runs, self._font, self._font_emoji, self.cfg.font_size) + 2
            else:
                for ln in wrap_text(nd.text, self._font, text_w):
                    draw.text((tx, ty), ln, fill=cfg.text_color, font=self._font)
                    ty += line_h(self._font, ln) + 2
            if nd.time:
                ty += 6
                draw.text((tx, ty), _fmt_time(nd.time), fill=cfg.time_color,
                          font=self._font_time)
                ty += line_h(self._font_time, _fmt_time(nd.time))
            # 图片
            if imgs:
                ty += 8
            for im in imgs:
                nw, nh_im = self._scale_image(im, text_w)
                im2 = im.resize((nw, nh_im), getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
                img.paste(im2, (tx, ty), mask=im2.getchannel("A"))
                ty += nh_im + 6
            y += nh + cfg.gap

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def render_with_images(self, nodes: List[ForwardNode],
                                 *, node_imgs: Optional[dict] = None) -> bytes:
        """与 render() 同功能,但用调用方预下载的图片 dict{node_index: [bytes, ...]}.

        主要让 main.py 用并发下载,避免 forwarder 内部串行拉图.
        node_imgs 为 None 时走原来的"无图片"路径(等同于把所有图当加载失败).
        """
        if node_imgs is None:
            node_imgs = {i: [] for i in range(len(nodes))}
        from PIL import Image as _I
        # 把预下载的 bytes 转 PIL
        converted: List[List[_I.Image]] = []
        for nd, imgs in zip(nodes, [node_imgs.get(i, []) for i in range(len(nodes))]):
            pil_imgs = []
            for b in imgs:
                try:
                    im = _I.open(io.BytesIO(b)).convert("RGBA")
                    im.load()
                    pil_imgs.append(im)
                except Exception:
                    continue
            converted.append(pil_imgs)
        # 走原来的"无 fetcher"渲染路径
        return await self._render_with_pil(nodes, converted)

    async def _render_with_pil(self, nodes: List[ForwardNode],
                                node_pil: List[List["Image.Image"]]) -> bytes:
        """内部:已知 PIL 图片列表,直接渲染."""
        from PIL import Image, ImageDraw
        self._ensure_fonts()
        cfg = self.cfg

        def line_h(font, text):
            bbox = font.getbbox(text)
            return (bbox[3] - bbox[1]) if bbox else self.cfg.font_size + 4

        def wrap_text(text: str, font, max_w: int) -> List[str]:
            if not text:
                return []
            lines: List[str] = []
            for paragraph in text.split("\n"):
                if not paragraph:
                    lines.append("")
                    continue
                cur = ""
                for ch in paragraph:
                    cand = cur + ch
                    bbox = font.getbbox(cand)
                    w = bbox[2] - bbox[0] if bbox else 0
                    if w <= max_w or not cur:
                        cur = cand
                    else:
                        lines.append(cur)
                        cur = ch
                if cur:
                    lines.append(cur)
            return lines

        node_heights: List[int] = []
        for nd, imgs in zip(nodes, node_pil):
            _, _, bubble_w = _bubble_layout(cfg, nd)
            has_avatar = nd.avatar_path is not None and self.cfg.enable_user_avatar
            avatar_offset = (self.cfg.avatar_size + 6) if has_avatar else 0
            text_w = bubble_w - cfg.padding * 2 - avatar_offset
            if self._font_emoji is not None:
                lines = _wrap_mixed(nd.text, self._font, self._font_emoji, text_w)
            else:
                lines = wrap_text(nd.text, self._font, text_w)
            h = cfg.padding
            _hdr_h = line_h(self._font_header, nd.sender_name or "anon")
            if nd.avatar_path is not None and self.cfg.enable_user_avatar:
                _hdr_h = max(_hdr_h, self.cfg.avatar_size)
            h += _hdr_h
            h += 4
            for ln in lines:
                if self._font_emoji is not None:
                    h += _line_h_mixed(ln, self._font, self._font_emoji, self.cfg.font_size) + 2
                else:
                    h += line_h(self._font, ln) + 2
            if nd.time:
                h += 6 + line_h(self._font_time, _fmt_time(nd.time))
            if imgs:
                h += 8
            for im in imgs:
                w, ih = im.size
                scale = min(text_w / max(w, 1), cfg.max_image_width / max(w, 1))
                nw = max(1, int(w * scale))
                nh_im = max(1, int(ih * scale))
                h += nh_im + 6
            h += cfg.padding
            node_heights.append(h)

        total_h = cfg.padding * 2
        total_h += line_h(self._font_header, cfg.title) + 8
        for nh in node_heights:
            total_h += nh + cfg.gap
        if node_heights:
            total_h -= cfg.gap
        total_h = max(total_h, 200)

        img = Image.new("RGB", (cfg.width, total_h), cfg.bg)
        draw = ImageDraw.Draw(img)

        y = cfg.padding
        draw.text((cfg.padding, y), cfg.title, fill=cfg.header_color,
                  font=self._font_header)
        y += line_h(self._font_header, cfg.title) + 8

        for nd, imgs, nh in zip(nodes, node_pil, node_heights):
            depth, bubble_x, bubble_w = _bubble_layout(cfg, nd)
            bubble_bg, bubble_border = _bubble_style(cfg, nd)
            draw.rounded_rectangle(
                (bubble_x, y, bubble_x + bubble_w, y + nh),
                radius=cfg.bubble_radius,
                fill=bubble_bg,
                outline=bubble_border,
            )
            if depth:
                draw.line(
                    (bubble_x + 7, y + 10, bubble_x + 7, y + nh - 10),
                    fill=_nested_line_color(nd),
                    width=3,
                )
            tx = bubble_x + cfg.padding
            ty = y + cfg.padding
            _avatar_im = None
            if nd.avatar_path is not None and self.cfg.enable_user_avatar:
                try:
                    _avatar_im = Image.open(nd.avatar_path).convert("RGBA")
                    _avatar_im = _avatar_im.resize(
                        (self.cfg.avatar_size, self.cfg.avatar_size),
                        getattr(getattr(Image, "Resampling", Image), "LANCZOS"),
                    )
                except Exception:
                    _avatar_im = None
            if _avatar_im is not None:
                _mask = _circle_mask(self.cfg.avatar_size)
                img.paste(_avatar_im, (tx, ty), mask=_mask)
                tx += self.cfg.avatar_size + 6
            draw.text((tx, ty), nd.sender_name or "anon",
                      fill=cfg.header_color, font=self._font_header)
            _hdr_h = line_h(self._font_header, nd.sender_name or "anon")
            if _avatar_im is not None:
                _hdr_h = max(_hdr_h, self.cfg.avatar_size)
            ty += _hdr_h + 4
            has_avatar = nd.avatar_path is not None and self.cfg.enable_user_avatar
            avatar_offset = (self.cfg.avatar_size + 6) if has_avatar else 0
            text_w = bubble_w - cfg.padding * 2 - avatar_offset
            if self._font_emoji is not None:
                _mixed = _wrap_mixed(nd.text, self._font, self._font_emoji, text_w)
                for ln_runs in _mixed:
                    cur_x = tx
                    for seg, is_emoji in ln_runs:
                        _f = self._font_emoji if is_emoji else self._font
                        draw.text((cur_x, ty), seg, fill=cfg.text_color, font=_f)
                        cur_x += sum(_char_w(c, _f) for c in seg)
                    ty += _line_h_mixed(ln_runs, self._font, self._font_emoji, self.cfg.font_size) + 2
            else:
                for ln in wrap_text(nd.text, self._font, text_w):
                    draw.text((tx, ty), ln, fill=cfg.text_color, font=self._font)
                    ty += line_h(self._font, ln) + 2
            if nd.time:
                ty += 6
                draw.text((tx, ty), _fmt_time(nd.time), fill=cfg.time_color,
                          font=self._font_time)
                ty += line_h(self._font_time, _fmt_time(nd.time))
            if imgs:
                ty += 8
            for im in imgs:
                nw, nh_im = self._scale_image(im, text_w)
                im2 = im.resize((nw, nh_im), getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
                img.paste(im2, (tx, ty), mask=im2.getchannel("A"))
                ty += nh_im + 6
            y += nh + cfg.gap

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _fmt_time(ts: int) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def from_component(component) -> List[ForwardNode]:
    """从 Component(kind='forward') 抽出 ForwardNode[].Phase 2 main.py 用."""
    raw = (component.meta or {}).get("nodes")
    return _coerce_nodes(raw)

