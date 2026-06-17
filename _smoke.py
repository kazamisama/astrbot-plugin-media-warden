"""Smoke v1.1: 骨架 + 配置 + 钩子 + 策略 + 组件识别 + 下载 + 落盘 + dedupe.

独立运行,不依赖 AstrBot runtime.通过在 sys.modules 注入 astrbot.* stub
让 main.py 的 import 通路走通,然后直接调用核心函数验证.
"""
import os
import sys
import tempfile
import types


def _install_stub():
    a = types.ModuleType("astrbot")
    ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star")
    em = types.ModuleType("astrbot.api.event")

    class Star:
        def __init__(self, context):
            self.context = context

    def register(*args, **kwargs):
        def deco(cls):
            return cls

        return deco

    class Context:
        pass

    class AstrMessageEvent:
        pass

    class _MT:
        ALL = "all"
        GROUP_MESSAGE = "group_message"
        PRIVATE_MESSAGE = "private_message"

    class _F:
        EventMessageType = _MT

        def event_message_type(self, *a, **k):
            def deco(fn):
                return fn

            return deco

        def command(self, *a, **k):
            def deco(fn):
                return fn

            return deco

    sm.Star = Star
    sm.register = register
    sm.Context = Context
    em.filter = _F
    em.AstrMessageEvent = AstrMessageEvent
    em.EventMessageType = _MT
    sys.modules["astrbot"] = a
    sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm
    sys.modules["astrbot.api.event"] = em


_install_stub()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # for parent package import

# 装载插件作为包,让 main.py 的 'from .warden import ...' 能工作.
import importlib
_pname = "astrbot_plugin_media_warden_smoke"
if _pname in sys.modules:
    del sys.modules[_pname]
pkg = types.ModuleType(_pname)
pkg.__path__ = [HERE]
sys.modules[_pname] = pkg
main = importlib.import_module(_pname + ".main")
plugin_main = main
from warden import (
    MediaWardenConfig,
    MatchDecision,
    evaluate,
    extract_components,
    recover_raw_media_refs,
    Component,
    AssetResult,
    BatchResult,
    format_batch,
    summarize,
)
from warden.downloader import (
    aiohttp_fetcher, astrbot_component_fetcher,
    download, download_many, DownloadError, Downloaded,
)
from warden.storage import Storage, SaveContext, render_filename, _safe_name, _guess_ext


def banner(t):
    print("\n=== " + t + " ===")


# =========================================================
# Phase 1 测试
# =========================================================


def test_config_from_raw_with_overrides():
    banner("config: from_raw accepts overrides + drops unknowns")
    cfg = MediaWardenConfig.from_raw({
        "match_mode": "blacklist",
        "target_groups": ["aiocqhttp:111", "222"],
        "max_file_size_mb": 50,
        "unknown_field_xxx": "ignored",
    })
    assert cfg.match_mode == "blacklist"
    assert cfg.max_file_size_bytes == 50 * 1024 * 1024
    assert "aiocqhttp:111" in cfg.matched_groups_set()
    assert "222" in cfg.matched_groups_set()
    print("  OK")


def test_config_rejects_invalid_mode():
    banner("config: invalid match_mode raises")
    try:
        MediaWardenConfig(match_mode="nope")
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "match_mode" in str(e)
        print("  OK ->", e)


def test_policy_key_format():
    banner("policy: group key normalizes platform/lower")
    from warden.policy import _key
    assert _key("aiocqhttp", "123") == "aiocqhttp:123"
    assert _key("AIOCQHTTP", "123") == "aiocqhttp:123"
    assert _key(None, None) == ""
    print("  OK")


def _fake_event(*, platform="aiocqhttp", group_id="g1", user_id="u1",
                message=None, message_str="", is_group=True,
                nickname=None, message_id="m42", timestamp=None,
                raw_message=None):
    class _PM:
        def __init__(self, p, g):
            self.name = p
            self.id = p
            self.platform = p
            self.channel_id = g

    class _Sender:
        def __init__(self, u, nick):
            self.user_id = u
            self.nickname = nick

    class _MO:
        def __init__(self, m, gid, sender, mid, ts, raw):
            self.message = m
            self.group_id = gid
            self.sender = sender
            self.message_id = mid
            self.timestamp = ts
            self.raw_message = raw

    class _Result:
        def __init__(self, t): self.text = t

    class _Ev:
        def plain_result(self, t): return _Result(t)
        def get_platform_name(self): return self.platform_meta.name
        def get_group_id(self): return self.message_obj.group_id or ""
        def get_sender_id(self):
            s = getattr(self.message_obj, "sender", None)
            return getattr(s, "user_id", "") if s else ""
        def get_sender_name(self):
            s = getattr(self.message_obj, "sender", None)
            return (getattr(s, "nickname", None) or "") if s else ""

    gid = group_id if is_group else None
    sender_obj = _Sender(user_id, nickname)
    e = _Ev()
    e.platform_meta = _PM(platform, gid)
    e.sender = sender_obj
    e.message_obj = _MO(message, gid, sender_obj, message_id, timestamp, raw_message)
    e.message_str = message_str
    e.message_id = message_id
    e.timestamp = timestamp
    return e


def test_policy_whitelist_match_and_miss():
    banner("policy: whitelist gates by group AND user")
    cfg = MediaWardenConfig(target_groups=["aiocqhttp:g1"], target_users=["u1"])
    assert evaluate(cfg, _fake_event(group_id="g1", user_id="u1")).allow is True
    assert evaluate(cfg, _fake_event(group_id="g2", user_id="u1")).allow is False
    assert evaluate(cfg, _fake_event(group_id="g1", user_id="u9")).allow is False
    print("  OK")


def test_policy_whitelist_empty_lists_means_unrestricted():
    banner("policy: empty whitelist = unrestricted")
    cfg = MediaWardenConfig(target_groups=[], target_users=[], match_mode="whitelist")
    for gid in ["g1", "g2", "g999"]:
        for uid in ["u1", "u42"]:
            assert evaluate(cfg, _fake_event(group_id=gid, user_id=uid)).allow
    print("  OK")


def test_policy_blacklist():
    banner("policy: blacklist blocks on either group or user hit")
    cfg = MediaWardenConfig(
        match_mode="blacklist",
        target_groups=["aiocqhttp:gBAD"], target_users=["uBAD"],
    )
    assert evaluate(cfg, _fake_event(group_id="gOK", user_id="u1")).allow is True
    assert evaluate(cfg, _fake_event(group_id="gBAD", user_id="u1")).allow is False
    assert evaluate(cfg, _fake_event(group_id="gOK", user_id="uBAD")).allow is False
    print("  OK")


def test_policy_private_skipped():
    banner("policy: private/no-group events always skipped (v1)")
    cfg = MediaWardenConfig(target_groups=["aiocqhttp:g1"], target_users=["u1"])
    d = evaluate(cfg, _fake_event(is_group=False, user_id="u1"))
    assert d.allow is False and "non-group" in d.reason
    print("  OK")


def test_components_extract_onebot_segments():
    banner("components: extract from OneBot-style segment list")
    e = _fake_event(message=[
        {"type": "text",  "data": {"text": "看图"}},
        {"type": "image", "data": {"file": "abc.jpg", "url": "https://x/a.jpg"}},
        {"type": "record","data": {"file": "v.amr", "url": "https://x/v.amr"}},
        {"type": "file",  "data": {"file": "f.zip", "name": "f.zip", "file_size": "2048"}},
        {"type": "video", "data": {"file": "v.mp4"}},
        {"type": "json",  "data": {"data": "{\"a\":1}"}},
        {"type": "node",  "data": {"content": [{"type": "text", "data": {"text": "merged"}}]}},
    ])
    cs = extract_components(e)
    kinds = [c.kind for c in cs]
    assert kinds == ["text", "image", "voice", "file", "video", "json", "forward"]
    assert cs[1].url == "https://x/a.jpg"
    assert cs[3].name == "f.zip" and cs[3].size == 2048
    assert cs[6].is_forward and isinstance(cs[6].meta.get("nodes"), list)
    print("  OK ->", kinds)


def test_components_extract_astrbot_objects():
    banner("components: extract from AstrBot Pydantic component objects")

    class _CT:
        def __init__(self, v): self.value = v

    class _Comp:
        def __init__(self, type_name, **kw):
            self.type = _CT(type_name)
            for k, v in kw.items():
                setattr(self, k, v)

    msg = [
        _Comp("Plain", text="hi"),
        _Comp("Image", file="abc.image", url="https://x/a.jpg"),
        _Comp("Image", file="market_emoji_fileid", url=""),   # 商城表情: 无 url, 有 file_id
        _Comp("Face", id=178),                                  # QQ 内置表情: 只有 id
        _Comp("Video", file="v.mp4", url="https://x/v.mp4"),
        _Comp("Record", file="r.amr", url=""),
        _Comp("File", name="f.zip", file="f.zip", url="https://x/f.zip"),
        _Comp("Node", content=[_Comp("Plain", text="m")]),
    ]
    e = _fake_event(message=msg)
    cs = extract_components(e)
    kinds = [c.kind for c in cs]
    assert kinds == ["text", "image", "image", "json", "video", "voice", "file", "forward"], kinds
    # 商城表情: 无 url 但保留 file_id + raw（供 component fetcher 回退）
    assert cs[2].kind == "image" and cs[2].url is None and cs[2].file_id == "market_emoji_fileid"
    assert cs[2].raw is msg[2]
    # 表情存为 json 元数据
    assert cs[3].kind == "json" and cs[3].meta["data"]["face_id"] == 178
    assert cs[1].url == "https://x/a.jpg" and cs[1].is_media
    print("  OK ->", kinds)


def test_components_text_only_fallback():
    banner("components: text-only event returns single text component")
    e = _fake_event(message=[{"type": "text", "data": {"text": "hello"}}])
    cs = extract_components(e)
    assert len(cs) == 1 and cs[0].kind == "text" and cs[0].name == "hello"
    print("  OK")


def test_components_no_message_obj():
    banner("components: missing message_obj falls back to message_str")
    class _E:
        pass

    e = _E()
    e.message_obj = None
    e.message_str = "just text"
    cs = extract_components(e)
    assert len(cs) == 1 and cs[0].kind == "text"
    print("  OK")


def test_reporter_format_batch_ok():
    banner("reporter: format_batch success-only")
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="/data/x/a.jpg", size=1024),
        AssetResult(kind="video", ok=True, path="/data/x/b.mp4", size=2048),
    ], duration_s=0.42)
    txt = format_batch(br)
    assert "✅" in txt and "/data/x/" in txt
    assert "image×1" in txt and "video×1" in txt
    print("  OK")
    print(txt)


def test_reporter_format_batch_partial():
    banner("reporter: format_batch partial failure")
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="/data/a.jpg", size=10),
        AssetResult(kind="file", ok=False, err="timeout"),
    ], duration_s=1.23)
    txt = format_batch(br)
    assert "⚠️" in txt and "1/2" in txt and "timeout" in txt
    print("  OK")
    print(txt)


def test_reporter_marks_reused():
    banner("reporter: reused (dedupe hit) items are flagged in receipt")
    # 部分重复
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="/d/a.jpg", size=10, reused=False),
        AssetResult(kind="image", ok=True, path="/d/b.jpg", size=10, reused=True),
    ], duration_s=0.1)
    txt = format_batch(br)
    assert "已保存过" in txt, txt
    assert "去重: 1 项" in txt, txt
    # 全部重复
    br2 = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="/d/a.jpg", size=10, reused=True),
    ], duration_s=0.1)
    txt2 = format_batch(br2)
    assert "全部为已保存过的重复内容" in txt2, txt2
    print("  OK")


def test_reporter_storage_root_no_common_prefix():
    banner("reporter: storage_root returns None when paths diverge")
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="D:/a.jpg"),
        AssetResult(kind="image", ok=True, path="E:/b.jpg"),
    ])
    assert br.storage_root is None
    print("  OK")


def test_plugin_register_metadata():
    banner("plugin: @register preserves name/author/version metadata")
    from warden import VERSION
    cls = plugin_main.MediaWardenStar
    assert cls.__name__ == "MediaWardenStar"
    assert isinstance(VERSION, str) and VERSION.startswith("1.")
    print("  OK")


def test_plugin_construct_with_default_config():
    banner("plugin: construct with empty config falls back to defaults")
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    inst = plugin_main.MediaWardenStar(ctx, config=None)
    assert inst.cfg.match_mode == "whitelist"
    assert inst.cfg.storage_root == "data/warden"
    print("  OK")


def test_plugin_construct_with_override():
    banner("plugin: construct with overridden config")
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    inst = plugin_main.MediaWardenStar(ctx, config={
        "match_mode": "blacklist", "target_users": ["u1"], "max_file_size_mb": 33,
    })
    assert inst.cfg.match_mode == "blacklist"
    assert "u1" in inst.cfg.matched_users_set()
    assert inst.cfg.max_file_size_bytes == 33 * 1024 * 1024
    print("  OK")


def test_plugin_construct_with_invalid_config_falls_back():
    banner("plugin: invalid config string falls back to defaults gracefully")
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    inst = plugin_main.MediaWardenStar(ctx, config={"match_mode": "nope"})
    assert inst.cfg.match_mode == "whitelist"
    print("  OK")


# =========================================================
# Phase 2 测试
# =========================================================


def test_storage_safe_name():
    banner("storage: safe_name strips separators + control chars")
    assert _safe_name("hello/world:foo") == "hello_world_foo"
    assert _safe_name("a" * 100) == "a" * 40
    assert _safe_name("../etc/passwd") == ".._etc_passwd"  # 路径级 ../ 在 render_filename 阶段剥
    assert _safe_name("\x00\x01abc\x1f") == "abc"
    assert _safe_name("中文 文件") == "中文_文件"
    assert _safe_name("") == "asset"
    print("  OK")


def test_storage_guess_ext():
    banner("storage: guess_ext from name/mime/url/file_id")
    c1 = Component(kind="image", name="photo.JPG")
    assert _guess_ext(c1) == ".jpg"
    c2 = Component(kind="image", url="https://x/y.PNG")
    assert _guess_ext(c2) == ".png"
    c3 = Component(kind="image", file_id="a.amr")
    assert _guess_ext(c3) == ".amr"
    c4 = Component(kind="image")
    assert _guess_ext(c4, mime="image/webp") == ".webp"
    c5 = Component(kind="image")
    assert _guess_ext(c5) == ".bin"
    print("  OK")


def test_storage_guess_ext_sniffs_bytes():
    banner("storage: content magic-bytes win over file_id/mime (gif stays gif)")
    # 复现 bug：QQ 动图 file_id=.gif 但 mime 猜成 jpeg；现在以字节为准
    gif = b"GIF89a" + b"\x00" * 16
    c = Component(kind="image", file_id="8BD....gif", url="https://x/download")
    assert _guess_ext(c, mime="image/jpeg", data=gif) == ".gif", "should sniff gif"
    # 真是 jpeg 字节时就是 jpg
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    assert _guess_ext(c, mime=None, data=jpg) == ".jpg"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    assert _guess_ext(Component(kind="image"), data=png) == ".png"
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
    assert _guess_ext(Component(kind="image"), data=webp) == ".webp"
    # file_id 后缀 现在优先于 mime（无字节时）
    c2 = Component(kind="image", file_id="x.gif")
    assert _guess_ext(c2, mime="image/jpeg") == ".gif"
    print("  OK")


def test_storage_render_filename_pattern():
    banner("storage: render_filename applies pattern + escapes path traversal")
    c = Component(kind="image", name="a/b/c.jpg")
    ctx = SaveContext(
        platform="aiocqhttp", group_id="g1", sender_id="u1",
        sender_name="alice", msg_id="m42", idx=0, ts=1718600000,
    )
    rel = render_filename(
        "{platform}/{group_id}/{date}/{sender_id}_{msg_id}_{idx}_{safe_name}.{ext}",
        c, ctx,
    )
    assert rel.startswith("aiocqhttp/g1/"), rel
    assert "u1_m42_0_" in rel
    assert rel.endswith(".jpg")
    print("  OK ->", rel)


def test_storage_render_filename_blocks_traversal():
    banner("storage: render_filename neutralizes '..' segments")
    c = Component(kind="image", name="x.png")
    ctx = SaveContext(
        platform="aiocqhttp", group_id="../escape", sender_id="u1",
        sender_name="a", msg_id="m", idx=0, ts=1718600000,
    )
    rel = render_filename("{platform}/{group_id}/{sender_id}.{ext}", c, ctx)
    assert ".." not in rel.split("/")
    print("  OK ->", rel)


def test_storage_save_roundtrip():
    banner("storage: save writes file + respects pattern")
    with tempfile.TemporaryDirectory() as td:
        st = Storage(root=td, pattern="{platform}/{group_id}/{sender_id}.{ext}")
        c = Component(kind="image", name="pic.jpg")
        ctx = SaveContext(platform="qq", group_id="g1", sender_id="u1",
                          sender_name="alice", msg_id="m1", idx=0, ts=1718600000)
        data = b"\xff\xd8\xff\xe0fake jpeg body"
        r = st.save(data, c, ctx)
        assert os.path.exists(r.path), r.path
        assert open(r.path, "rb").read() == data
        assert r.reused is False
        # dedupe 复用:同 content 再来一次
        r2 = st.save(data, c, ctx)
        assert r2.reused is True
        # 命中去重时返回首存真实路径（不是 _blake 指纹路径）
        assert os.path.normpath(r2.path) == os.path.normpath(r.path), (r.path, r2.path)
        assert "_blake" not in r2.path
        print("  OK ->", r.path, "reused:", r2.reused)


def test_storage_dedupe_returns_original_location():
    banner("storage: dedupe hit returns first-stored location via .ptr pointer")
    with tempfile.TemporaryDirectory() as td:
        st = Storage(root=td, pattern="{group_id}/{sender_id}/{date}_{kind}_{short_id}{ext}")
        c = Component(kind="image", name="x.png")
        ctx = SaveContext(platform="qq", group_id="g1", sender_id="u1",
                          sender_name="a", msg_id="m1", idx=0, ts=1718600000)
        data = b"PNGDATA-unique-content-123"
        r1 = st.save(data, c, ctx)
        assert r1.reused is False
        # 指针文件存在且指向首存相对路径
        ptr = st._blake_path(r1.blake16)
        assert os.path.exists(ptr) and ptr.endswith(".ptr")
        rel = open(ptr, encoding="utf-8").read().strip()
        assert os.path.normpath(os.path.join(td, rel)) == os.path.normpath(r1.path)
        # 同内容再发 -> 返回原位置，不重写
        r2 = st.save(data, c, ctx)
        assert r2.reused is True
        assert os.path.normpath(r2.path) == os.path.normpath(r1.path)
        print("  OK ->", os.path.basename(r1.path), "reused-at-same-path")


def test_storage_digest_full_vs_partial():
    banner("storage: small files full-hash (no front-1MB collision), big files partial")
    from warden.storage import _blake_digest, _FULL_HASH_LIMIT
    # 小文件全量哈希：前缀相同但末尾不同 -> 不同指纹
    a = b"A" * 1000 + b"X"
    b = b"A" * 1000 + b"Y"
    assert _blake_digest(a) != _blake_digest(b)
    # 大文件：前1MB相同 + size相同 -> 同指纹； size不同 -> 不同指纹
    big1 = b"Z" * (_FULL_HASH_LIMIT + 10)
    big2 = b"Z" * (_FULL_HASH_LIMIT + 10)
    big3 = b"Z" * (_FULL_HASH_LIMIT + 11)
    assert _blake_digest(big1) == _blake_digest(big2)
    assert _blake_digest(big1) != _blake_digest(big3)
    print("  OK")


def test_storage_save_oversize_rejected():
    banner("storage: save rejects data > max_bytes")
    with tempfile.TemporaryDirectory() as td:
        st = Storage(root=td, max_bytes=10)
        c = Component(kind="file", name="big.zip")
        ctx = SaveContext(platform="qq", group_id="g", sender_id="u",
                          sender_name="a", msg_id="m", idx=0, ts=1718600000)
        try:
            st.save(b"x" * 11, c, ctx)
            raise AssertionError("should reject")
        except ValueError as e:
            assert "exceeds" in str(e)
            print("  OK ->", e)


def test_downloader_no_url():
    banner("downloader: download raises DownloadError on no-url component")
    import asyncio
    c = Component(kind="image")  # no url, no file_id
    try:
        asyncio.run(download(c))
        raise AssertionError("should fail")
    except DownloadError as e:
        assert "no url" in str(e)
        print("  OK ->", e)


def test_component_fetcher_local_url_reads_file():
    banner("downloader: astrbot_component_fetcher reads local-path url (napcat) instead of aiohttp")
    import asyncio, tempfile, os
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "media_image_abc.jpg")
        body = b"\xff\xd8\xff\xe0local-jpeg-bytes"
        open(fp, "wb").write(body)
        # 模拟 napcat：Image.url 直接是本地路径
        c = Component(kind="image", url=fp)
        dl = asyncio.run(astrbot_component_fetcher(c, max_bytes=1024))
        assert dl.data == body and dl.size == len(body)
        assert dl.mime == "image/jpeg"
        print("  OK -> read", dl.size, "bytes from local url")


def test_component_fetcher_fileurl_and_encoded():
    banner("downloader: fetcher handles file:// and URL-encoded local paths")
    import asyncio, tempfile, os
    from urllib.parse import quote
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "pic.png")
        body = b"PNGBODY"
        open(fp, "wb").write(body)
        # file:/// 形式
        file_url = "file:///" + fp.replace(os.sep, "/")
        c1 = Component(kind="image", url=file_url)
        dl1 = asyncio.run(astrbot_component_fetcher(c1, max_bytes=1024))
        assert dl1.data == body, "file:// url failed"
        # URL 编码的本地路径（如 c%3A%5C...）
        c2 = Component(kind="image", url=quote(fp))
        dl2 = asyncio.run(astrbot_component_fetcher(c2, max_bytes=1024))
        assert dl2.data == body, "encoded local path failed"
        print("  OK -> file:// and encoded local path both read")


def test_downloader_aiohttp_success_via_mock():
    """用 monkey-patch 替换 aiohttp,避免真实网络."""
    banner("downloader: aiohttp_fetcher success via mock")
    import asyncio

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _CM:
        def __init__(self): self._entered = False
        async def __aenter__(self):
            class _Resp:
                status = 200
                headers = {"Content-Type": "image/png"}
                content = _CM2()
            return _Resp()
        async def __aexit__(self, *a): return False
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n")
            yield _Chunk(b"\x1a\nrest of body")

    class _CM2:
        def __init__(self): self._done = False
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n")
            yield _Chunk(b"\x1a\nrest of body")



    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    c = Component(kind="image", url="https://x/a.png")
    dl = asyncio.run(download(c, fetcher=aiohttp_fetcher))
    assert dl.data.startswith(b"\x89PNG"), dl.data[:4]
    assert dl.mime == "image/png"
    assert dl.size > 0
    print("  OK ->", len(dl.data), "bytes,", dl.mime)


def test_downloader_aiohttp_oversize_aborts():
    banner("downloader: aiohttp_fetcher aborts on stream > max_bytes")
    import asyncio

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Resp:
        status = 200
        headers = {"Content-Type": "application/octet-stream"}

        class content:
            @staticmethod
            def iter_chunked(n):
                async def gen():
                    for _ in range(20):
                        yield _Chunk(b"x" * (100 * 1024))
                return gen()

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _RespCM()
        async def close(self): pass

    class _RespCM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _CM2:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False



    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    c = Component(kind="file", url="https://x/big.bin")
    try:
        asyncio.run(download(c, fetcher=aiohttp_fetcher, max_bytes=10))
        raise AssertionError("should fail")
    except DownloadError as e:
        assert "exceeded" in str(e) or "max_bytes" in str(e)
        print("  OK ->", e)


def test_plugin_e2e_group_message_image_saved():
    """端到端: 群消息 + image + 直接传 fetcher 到钩子 -> 落盘 + reporter."""
    import asyncio

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Content:
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n\x1a\nfake-png-body")

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    with tempfile.TemporaryDirectory() as td:
        ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
        })
        asyncio.run(inst.initialize())

        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[
                {"type": "text",  "data": {"text": "看图"}},
                {"type": "image", "data": {"file": "abc.jpg", "url": "https://x/a.png"}},
            ],
            message_id="m42", timestamp=1718600000,
        )

        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)

        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        assert "✅" in text, text
        assert "image×1" in text
        # 文件实际落盘
        import glob
        files = []
        for root, _, fs in os.walk(td):
            for f in fs:
                if not f.startswith("_blake"):
                    files.append(os.path.join(root, f))
        assert any(".png" in p for p in files), files
        # 显式关 index 让 Windows 临时目录可清理
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK ->", text.replace(chr(10), " | "))


def test_plugin_e2e_forward_saved_as_json_sidecar():
    """端到端: 转发节点 -> JSON sidecar (mode=json 强制)."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_mode": "json",
            "enable_index_db": True,
        })
        asyncio.run(inst.initialize())

        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[
                {"type": "node", "data": {"content": [
                    {"type": "node", "data": {
                        "user_id": "u2", "nickname": "bob",
                        "time": 1718600000,
                        "content": [{"type": "text", "data": {"text": "merged"}}],
                    }},
                ]}},
            ],
            message_id="m99", timestamp=1718600000,
        )

        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)

        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        assert "forward×1" in text, text
        # 验证: 文件确实是 JSON
        import json as _json
        found = []
        for root, _, fs in os.walk(td):
            for f in fs:
                p = os.path.join(root, f)
                if p.endswith(".json"):
                    with open(p, "r", encoding="utf-8") as fh:
                        d = _json.load(fh)
                        if d.get("forward"):
                            found.append((p, d))
        assert found, "no forward sidecar"
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        import json as _json
        found = []
        for root, _, fs in os.walk(td):
            for f in fs:
                p = os.path.join(root, f)
                if p.endswith(".json"):
                    with open(p, "r", encoding="utf-8") as fh:
                        d = _json.load(fh)
                        if d.get("forward"):
                            found.append((p, d))
        assert found, "no forward sidecar"
        print("  OK ->", text.replace(chr(10), " | "))


def test_plugin_skip_text_only():
    """纯文本消息应被跳过,不进入落盘."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td, "enable_index_db": False,
        })
        asyncio.run(inst.initialize())
        ev = _fake_event(group_id="g1", user_id="u1",
                         message=[{"type": "text", "data": {"text": "hi"}}],
                         message_id="m1")
        async def _drive():
            try:
                gen = inst.on_group_message(ev)
                await anext(gen)
                return False
            except StopAsyncIteration:
                return True

        skipped = asyncio.run(_drive())
        assert skipped, "should not yield for text-only"
        files = []
        for root, _, fs in os.walk(td):
            for f in fs:
                files.append(os.path.join(root, f))
        assert not files
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK (no yield, no file)")


# =========================================================
# 入口
# =========================================================


PHASE1 = [
    test_config_from_raw_with_overrides,
    test_config_rejects_invalid_mode,
    test_policy_key_format,
    test_policy_whitelist_match_and_miss,
    test_policy_whitelist_empty_lists_means_unrestricted,
    test_policy_blacklist,
    test_policy_private_skipped,
    test_components_extract_onebot_segments,
    test_components_extract_astrbot_objects,
    test_components_text_only_fallback,
    test_components_no_message_obj,
    test_reporter_format_batch_ok,
    test_reporter_format_batch_partial,
    test_reporter_marks_reused,
    test_reporter_storage_root_no_common_prefix,
    test_plugin_register_metadata,
    test_plugin_construct_with_default_config,
    test_plugin_construct_with_override,
    test_plugin_construct_with_invalid_config_falls_back,
]
PHASE2 = [
    test_storage_safe_name,
    test_storage_guess_ext,
    test_storage_guess_ext_sniffs_bytes,
    test_storage_render_filename_pattern,
    test_storage_render_filename_blocks_traversal,
    test_storage_save_roundtrip,
    test_storage_dedupe_returns_original_location,
    test_storage_digest_full_vs_partial,
    test_storage_save_oversize_rejected,
    test_downloader_no_url,
    test_downloader_aiohttp_success_via_mock,
    test_downloader_aiohttp_oversize_aborts,
    test_plugin_e2e_group_message_image_saved,
    test_plugin_e2e_forward_saved_as_json_sidecar,
    test_plugin_skip_text_only,
]


# =========================================================
# Phase 3 测试 (forwarder + index + commands)
# =========================================================


def test_forwarder_coerce_nodes():
    banner("forwarder: _coerce_nodes tolerates mixed OneBot shapes")
    from warden.forwarder import _coerce_nodes
    raw = [
        {"type": "node", "data": {"user_id": "u1", "nickname": "alice",
                                   "time": 1718600000, "content": [
                                       {"type": "text", "data": {"text": "hi"}},
                                       {"type": "image", "data": {"url": "https://x/a.png"}},
                                   ]}},
        {"type": "node", "data": {"user_id": 12345, "nickname": "bob",
                                   "content": "plain string"}},
    ]
    nodes = _coerce_nodes(raw)
    assert len(nodes) == 2
    assert nodes[0].sender_name == "alice" and nodes[0].sender_id == "u1"
    assert nodes[0].text == "hi"
    assert nodes[0].image_urls == ["https://x/a.png"]
    assert nodes[1].text == "plain string"
    print("  OK")


def test_forwarder_render_text_only():
    banner("forwarder: render text-only nodes -> PNG bytes")
    import asyncio
    from warden.forwarder import Forwarder, ForwardNode
    fwd = Forwarder(width=400, max_nodes=30)
    nodes = [
        ForwardNode(sender_name="alice", sender_id="u1",
                    text="你好世界", time=1718600000, image_urls=[]),
        ForwardNode(sender_name="bob", sender_id="u2",
                    text="second line\nnext line", time=1718600100, image_urls=[]),
    ]
    png = asyncio.run(fwd.render(nodes))
    assert png[:8] == b"\x89PNG\r\n\x1a\n", png[:8]
    # 简单尺寸检查
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(png))
    assert im.size[0] == 400
    assert im.size[1] > 50
    print("  OK ->", im.size, f"{len(png)}B")


def test_forwarder_render_with_image():
    banner("forwarder: render with embedded image download")
    import asyncio
    import io
    from PIL import Image as _I
    from warden.forwarder import Forwarder, ForwardNode

    # 造一张 50x50 红图
    red = _I.new("RGB", (50, 50), (255, 0, 0))
    buf = io.BytesIO()
    red.save(buf, format="PNG")
    red_bytes = buf.getvalue()

    async def fake_dl(url):
        return red_bytes

    fwd = Forwarder(width=400, max_nodes=30)
    nodes = [
        ForwardNode(sender_name="alice", sender_id="u1",
                    text="看图", time=1718600000,
                    image_urls=["https://x/a.png"]),
    ]
    png = asyncio.run(fwd.render(nodes, image_downloader=fake_dl))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    im = _I.open(io.BytesIO(png))
    # 高度应至少包含: title(20) + 1 节点(padding*2 + header + text + image ~50)
    assert im.size[1] > 100, im.size
    print("  OK ->", im.size, f"{len(png)}B")


def test_forwarder_can_handle_node_limit():
    banner("forwarder: can_handle respects max_nodes")
    from warden.forwarder import Forwarder, ForwardNode
    fwd = Forwarder(width=400, max_nodes=3)
    too_many = [ForwardNode("a", "1", "x", 0, []) for _ in range(5)]
    assert fwd.can_handle(too_many) is False
    ok = [ForwardNode("a", "1", "x", 0, [])]
    assert fwd.can_handle(ok) is True
    assert fwd.can_handle([]) is False
    print("  OK")


def test_index_record_and_recent():
    banner("index: record + recent roundtrip")
    import tempfile
    from warden.index import AssetIndex
    with tempfile.TemporaryDirectory() as td:
        idx = AssetIndex(db_path=os.path.join(td, "test.db"))
        idx.open()
        for i in range(5):
            idx.record(platform="qq", group_id="g1", sender_id="u1",
                       msg_id=f"m{i}", idx=0, kind="image",
                       path=f"/data/x{i}.jpg", size=100 * i,
                       sha16=f"h{i}" * 16)
        rows = idx.recent(3)
        assert len(rows) == 3
        # ts DESC 排序
        assert rows[0]["ts"] >= rows[1]["ts"]
        idx.close()
        # reopen
        idx2 = AssetIndex(db_path=os.path.join(td, "test.db"))
        idx2.open()
        assert len(idx2.recent(10)) == 5
        idx2.close()
        print("  OK")


def test_index_find_by_sha():
    banner("index: find_by_sha locates dedupe reuse")
    import tempfile
    from warden.index import AssetIndex
    with tempfile.TemporaryDirectory() as td:
        idx = AssetIndex(db_path=os.path.join(td, "test.db"))
        idx.open()
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m1", idx=0, kind="image", path="/a", size=10,
                   sha16="abcd1234abcd1234")
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m2", idx=0, kind="image", path="/a", size=10,
                   sha16="abcd1234abcd1234")
        rows = idx.find_by_sha("abcd1234abcd1234")
        assert len(rows) == 2
        idx.close()
        print("  OK")


def test_index_stats():
    banner("index: stats aggregates kind + bytes")
    import tempfile
    from warden.index import AssetIndex
    with tempfile.TemporaryDirectory() as td:
        idx = AssetIndex(db_path=os.path.join(td, "test.db"))
        idx.open()
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m1", idx=0, kind="image", path="/a", size=100)
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m2", idx=0, kind="image", path="/b", size=200)
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m3", idx=0, kind="video", path="/c", size=1000)
        s = idx.stats()
        assert s["total"] == 3
        assert s["by_kind"]["image"] == 2
        assert s["by_kind"]["video"] == 1
        assert s["total_bytes"] == 1300
        idx.close()
        print("  OK ->", s)


def test_index_export_json():
    banner("index: export_json writes valid file with forward_meta")
    import tempfile, json as _json
    from warden.index import AssetIndex
    with tempfile.TemporaryDirectory() as td:
        idx = AssetIndex(db_path=os.path.join(td, "test.db"))
        idx.open()
        idx.record(platform="qq", group_id="g", sender_id="u",
                   msg_id="m1", idx=0, kind="forward", path="/x.json",
                   size=200, forward_meta={"node_count": 5})
        out = os.path.join(td, "out.json")
        n = idx.export_json(out)
        assert n == 1
        with open(out, "r", encoding="utf-8") as f:
            d = _json.load(f)
        assert d["version"] == 1
        assert d["items"][0]["forward_meta"] == {"node_count": 5}
        idx.close()
        print("  OK")


def test_plugin_e2e_forward_both_modes():
    """端到端: forward_render_mode=both -> PNG + JSON 都落盘."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_mode": "both",
            "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        assert inst._forwarder is not None
        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "node", "data": {"content": [
                {"type": "node", "data": {
                    "user_id": "u2", "nickname": "bob",
                    "time": 1718600000,
                    "content": [{"type": "text", "data": {"text": "merged hello"}}],
                }},
            ]}}],
            message_id="m200", timestamp=1718600000,
        )
        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)
        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        assert "forward" in text.lower() or "✅" in text, text
        # 应该有 PNG + JSON 都在
        pngs, jsons = 0, 0
        for root, _, fs in os.walk(td):
            for f in fs:
                if f.endswith(".png"):
                    pngs += 1
                if f.endswith(".json") and "forward" in f:
                    jsons += 1
        assert pngs >= 1, "no PNG rendered"
        assert jsons >= 1, "no JSON sidecar"
        # 索引里有数据
        s = inst._index.stats()
        assert s["total"] >= 2, s
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> PNG=%d JSON=%d, index=%d" % (pngs, jsons, s["total"]))


def test_plugin_e2e_forward_too_large_falls_back_to_json():
    """转发节点超过上限 -> 只生成 JSON 不渲染."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_mode": "image",  # 强制 image 但节点超限
            "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        # 造 50 个节点 (>30 上限)
        nodes_content = []
        for i in range(50):
            nodes_content.append({"type": "node", "data": {
                "user_id": f"u{i}", "nickname": f"u{i}",
                "time": 1718600000 + i,
                "content": [{"type": "text", "data": {"text": f"msg {i}"}}],
            }})
        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "node", "data": {"content": nodes_content}}],
            message_id="m300", timestamp=1718600000,
        )
        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)
        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        # 只应 JSON,没有 PNG
        pngs, jsons = 0, 0
        for root, _, fs in os.walk(td):
            for f in fs:
                if f.endswith(".png"):
                    pngs += 1
                if f.endswith(".json") and "forward" in f:
                    jsons += 1
        assert pngs == 0, "should not render when over limit"
        assert jsons >= 1, "should fall back to JSON"
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> JSON only (over limit), jsons=%d" % jsons)


def _close_index_safely(inst):
    if inst is not None and getattr(inst, "_index", None) is not None:
        try:
            inst._index.close()
        except Exception:
            pass
        inst._index = None


def test_plugin_warden_stats_command():
    """直接调 cmd_warden_stats,不通过 AstrBot runtime."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td, "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        # 直接写一条索引
        inst._index.record(platform="qq", group_id="g", sender_id="u",
                           msg_id="m", idx=0, kind="image",
                           path="/x", size=10)
        async def _drive():
            gen = inst.cmd_warden_stats(_ResultE())
            return await anext(gen)
        out = asyncio.run(_drive())
        text = out.text
        assert "warden 统计" in text
        assert "assets: 1" in text
        _close_index_safely(inst)
        print("  OK ->", text.replace(chr(10), " | "))


def test_plugin_warden_list_command():
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td, "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        for i in range(3):
            inst._index.record(platform="qq", group_id="g", sender_id="u",
                               msg_id=f"m{i}", idx=0, kind="image",
                               path=f"/x{i}", size=10)
        async def _drive():
            gen = inst.cmd_warden_list(_ResultE(), "2")
            return await anext(gen)
        out = asyncio.run(_drive())
        text = out.text
        assert "最近 2" in text
        _close_index_safely(inst)
        print("  OK")


def test_plugin_warden_export_command():
    import asyncio, json as _json
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td, "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        inst._index.record(platform="qq", group_id="g", sender_id="u",
                           msg_id="m", idx=0, kind="image", path="/x", size=10)
        out_path = os.path.join(td, "exp.json")
        async def _drive():
            gen = inst.cmd_warden_export(_ResultE(), out_path)
            return await anext(gen)
        out = asyncio.run(_drive())
        text = out.text
        assert "已导出 1" in text, text
        with open(out_path, "r", encoding="utf-8") as f:
            d = _json.load(f)
        assert d["version"] == 1 and len(d["items"]) == 1
        _close_index_safely(inst)
        print("  OK")


def _ResultE():
    """构造一个仅 plain_result 的轻量 event 供命令测试."""
    class _R:
        def __init__(self, t): self.text = t
    class _E:
        def plain_result(self, t): return _R(t)
    return _E()


def test_plugin_e2e_image_triggers_image_result():
    """端到端: 有 image_result 适配器时,image 落盘后会调 image_result 二次回传."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Content:
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n\x1a\nfake-png-body")

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "reply_preview": True,
        })
        asyncio.run(inst.initialize())

        # 事件有 image_result
        image_calls = []

        class _R:
            def __init__(self, t, kind="text"): self.text = t; self.kind = kind

        class _Ev:
            def plain_result(self, t): return _R(t)
            def image_result(self, path):
                image_calls.append(path)
                return _R(path, kind="image")

        ev = _Ev()
        ev.platform_meta = _fake_event().platform_meta
        ev.sender = _fake_event().sender
        ev.message_obj = _fake_event(
            message=[{"type": "image", "data": {
                "file": "x.png", "url": "https://x/x.png"
            }}]
        ).message_obj
        ev.message_str = ""
        ev.message_id = "m700"
        ev.timestamp = 1718600000

        async def _drive():
            out = []
            gen = inst.on_group_message(ev)
            async for r in gen:
                out.append(r)
            return out

        out = asyncio.run(_drive())
        # 应有 2 条: 1) plain_result 回执 2) image_result 预览
        assert len(out) == 2, f"expected 2 yields, got {len(out)}: {out}"
        assert out[0].kind == "text" and "✅" in out[0].text
        assert out[1].kind == "image"
        # image_result 被调用且参数是落盘的 png 路径
        assert len(image_calls) == 1
        assert image_calls[0].endswith(".png")
        assert os.path.exists(image_calls[0])
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> image_result called with", os.path.basename(image_calls[0]))


def test_plugin_e2e_image_fallback_when_no_image_result():
    """无 image_result 适配器时,降级为在 plain_result 后面补 '预览: <path>'."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Content:
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n\x1a\nfake-png-body")

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "reply_preview": True,
        })
        asyncio.run(inst.initialize())

        class _R:
            def __init__(self, t): self.text = t

        # 故意不实现 image_result
        class _Ev:
            def plain_result(self, t): return _R(t)

        ev = _Ev()
        ev.platform_meta = _fake_event().platform_meta
        ev.sender = _fake_event().sender
        ev.message_obj = _fake_event(
            message=[{"type": "image", "data": {
                "file": "x.png", "url": "https://x/x.png"
            }}]
        ).message_obj
        ev.message_str = ""
        ev.message_id = "m701"
        ev.timestamp = 1718600000

        async def _drive():
            out = []
            gen = inst.on_group_message(ev)
            async for r in gen:
                out.append(r)
            return out

        out = asyncio.run(_drive())
        # 2 条 plain_result: 1) 回执 2) '预览: ...' 降级
        assert len(out) == 2
        assert "✅" in out[0].text
        assert out[1].text.startswith("预览: ")
        assert out[1].text.endswith(".png")
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> fallback preview:", out[1].text)


def test_plugin_preview_disabled():
    """reply_preview=False 时不产生预览回传."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Content:
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n\x1a\nfake-png-body")

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "reply_preview": False,
        })
        asyncio.run(inst.initialize())

        class _R:
            def __init__(self, t, kind="text"): self.text = t; self.kind = kind
        class _Ev:
            def plain_result(self, t): return _R(t)
            def image_result(self, p): return _R(p, kind="image")

        ev = _Ev()
        ev.platform_meta = _fake_event().platform_meta
        ev.sender = _fake_event().sender
        ev.message_obj = _fake_event(
            message=[{"type": "image", "data": {
                "file": "x.png", "url": "https://x/x.png"
            }}]
        ).message_obj
        ev.message_str = ""
        ev.message_id = "m702"
        ev.timestamp = 1718600000

        async def _drive():
            out = []
            gen = inst.on_group_message(ev)
            async for r in gen:
                out.append(r)
            return out

        out = asyncio.run(_drive())
        assert len(out) == 1, f"only 1 yield expected, got {len(out)}"
        assert "✅" in out[0].text
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK (reply_preview=False -> no extra yield)")


def test_plugin_forward_render_takes_precedence_over_image():
    """同时有 forward_render 和 image 时,预览优先选 forward_render 的 PNG."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()

    class _Chunk(bytes):
        def __new__(cls, data): return bytes.__new__(cls, data)

    class _Content:
        async def iter_chunked(self, n):
            yield _Chunk(b"\x89PNG\r\n\x1a\nfake-png-body")

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False

    class _Sess:
        def __init__(self, *a, **k): pass
        def get(self, url): return _CM()
        async def close(self): pass

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = _Sess
    fake_aiohttp.ClientTimeout = lambda **k: None
    sys.modules["aiohttp"] = fake_aiohttp

    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "reply_preview": True,
            "forward_render_mode": "both",  # 同时有 PNG 和 JSON
        })
        asyncio.run(inst.initialize())

        class _R:
            def __init__(self, t, kind="text"): self.text = t; self.kind = kind
        class _Ev:
            def plain_result(self, t): return _R(t)
            def image_result(self, p): return _R(p, kind="image")

        ev = _Ev()
        ev.platform_meta = _fake_event().platform_meta
        ev.sender = _fake_event().sender
        ev.message_obj = _fake_event(
            message=[{"type": "node", "data": {"content": [
                {"type": "node", "data": {
                    "user_id": "u2", "nickname": "bob",
                    "time": 1718600000,
                    "content": [{"type": "text", "data": {"text": "hi"}}],
                }},
            ]}}]
        ).message_obj
        ev.message_str = ""
        ev.message_id = "m800"
        ev.timestamp = 1718600000

        async def _drive():
            out = []
            gen = inst.on_group_message(ev)
            async for r in gen:
                out.append(r)
            return out

        out = asyncio.run(_drive())
        # 第一条回执 + 第二条 image_result 预览(应是 forward_render 的 PNG)
        assert len(out) == 2
        assert out[1].kind == "image"
        # 预览路径应以 .png 结尾,且是 forward_*.png 而非 forward_*.json
        assert out[1].text.endswith(".png")
        assert "forward" in os.path.basename(out[1].text)
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK ->", os.path.basename(out[1].text))


PHASE3 = [
    test_forwarder_coerce_nodes,
    test_forwarder_render_text_only,
    test_forwarder_render_with_image,
    test_forwarder_can_handle_node_limit,
    test_index_record_and_recent,
    test_index_find_by_sha,
    test_index_stats,
    test_index_export_json,
    test_plugin_e2e_forward_both_modes,
    test_plugin_e2e_forward_too_large_falls_back_to_json,
    test_plugin_warden_stats_command,
    test_plugin_warden_list_command,
    test_plugin_warden_export_command,
    test_plugin_e2e_image_triggers_image_result,
    test_plugin_e2e_image_fallback_when_no_image_result,
    test_plugin_preview_disabled,
    test_plugin_forward_render_takes_precedence_over_image,
]


# =========================================================
# Phase 4 测试 (v1.3 新功能:retry / concurrent / lookup / prune / path-safety)
# =========================================================


def _make_component(url="https://example.com/a.png", kind="image", file_id="f1"):
    return Component(kind=kind, url=url, file_id=file_id, name="a.png")


def test_downloader_retry_transient():
    banner("downloader: retries on transient error then succeeds")
    import asyncio
    comp = _make_component()
    calls = {"n": 0}

    async def flaky(c, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise DownloadError("http 503 service unavailable")
        return Downloaded(data=b"PNG-bytes", mime="image/png", size=9)

    out = asyncio.run(download(comp, fetcher=flaky, retries=3,
                                backoff_base=0.001, backoff_cap=0.01))
    assert out.data == b"PNG-bytes"
    assert calls["n"] == 3
    print(f"  OK -> retried {calls['n'] - 1} times then succeeded")


def test_downloader_no_retry_on_non_transient():
    banner("downloader: does not retry on 4xx (non-transient)")
    import asyncio
    comp = _make_component()
    calls = {"n": 0}

    async def four_oh_four(c, **kw):
        calls["n"] += 1
        raise DownloadError("http 404 not found")

    try:
        asyncio.run(download(comp, fetcher=four_oh_four, retries=3,
                              backoff_base=0.001, backoff_cap=0.01))
        raise AssertionError("expected DownloadError")
    except DownloadError as e:
        assert "404" in str(e)
        assert calls["n"] == 1, f"should not retry, got {calls['n']} calls"
    print(f"  OK -> no retry on 4xx (calls={calls['n']})")


def test_downloader_give_up_after_max():
    banner("downloader: gives up after max retries")
    import asyncio
    comp = _make_component()
    calls = {"n": 0}

    async def always_5xx(c, **kw):
        calls["n"] += 1
        raise DownloadError("http 502 bad gateway")

    try:
        asyncio.run(download(comp, fetcher=always_5xx, retries=2,
                              backoff_base=0.001, backoff_cap=0.01))
        raise AssertionError("expected DownloadError")
    except DownloadError as e:
        assert "502" in str(e)
        assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
    print(f"  OK -> gave up after {calls['n']} attempts")


def test_downloader_many_concurrent():
    banner("downloader: download_many fetches concurrently, preserves order")
    import asyncio, time
    comps = [_make_component(url=f"https://x/{i}.png", file_id=str(i))
             for i in range(5)]

    async def slow(c, **kw):
        await asyncio.sleep(0.05)
        idx = int(c.file_id)
        return Downloaded(data=f"img-{idx}".encode(), mime="image/png",
                          size=5 + len(c.file_id))

    t0 = time.time()
    out = asyncio.run(download_many(comps, fetcher=slow, retries=0))
    elapsed = time.time() - t0

    assert len(out) == 5
    assert [d.data for d in out] == [f"img-{i}".encode() for i in range(5)]
    assert elapsed < 0.20, f"concurrent too slow: {elapsed:.3f}s"
    print(f"  OK -> 5 components in {elapsed:.3f}s (serial would ~0.25s)")


def test_storage_path_safety_rejects_outside_root():
    banner("storage: _assert_within_root rejects paths outside root")
    root = tempfile.mkdtemp(prefix="warden_safe_")
    s = Storage(root=root, dedupe=False, pattern="{safe_name}.{ext}", max_bytes=1000)
    bad = os.path.join(root, "..", "..", "etc", "passwd")
    try:
        s._assert_within_root(bad)
        raise AssertionError("expected ValueError for outside-root path")
    except ValueError as e:
        assert "escapes storage root" in str(e)
    s._assert_within_root(root)
    s._assert_within_root(os.path.join(root, "sub", "file.png"))
    print("  OK -> outside-root rejected, inside-root accepted")


def test_storage_works_for_legit_paths():
    banner("storage: _assert_within_root accepts normal save paths")
    root = tempfile.mkdtemp(prefix="warden_legit_")
    s = Storage(root=root, dedupe=False, pattern="{safe_name}.{ext}", max_bytes=1000)
    comp = _make_component()
    ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                      sender_name="alice",
                      msg_id="m1", idx=0, ts=1718600000)
    sr = s.save(b"hello", comp, ctx, mime="text/plain")
    rp = os.path.realpath(sr.path)
    assert rp.startswith(os.path.realpath(root))
    assert os.path.exists(sr.path)
    print(f"  OK -> saved at {sr.path}")


def _drive_command(plugin, event, fn):
    out = []
    gen = fn(event)
    if hasattr(gen, "__aiter__"):
        import asyncio
        async def _collect():
            res = []
            async for r in gen:
                res.append(r)
            return res
        return asyncio.run(_collect())
    for r in gen:
        out.append(r)
    return out


def _build_plugin_for_command(tmpdir, *, enable_index=True, log_to_stdout=False):
    cfg = {
        "storage_root": tmpdir,
        "enable_index_db": enable_index,
        "log_to_stdout": log_to_stdout,
    }
    pl = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](), cfg)
    return pl


def _seed_index(pl, *, msg_id="m42", n=3, ts=None):
    import time as _t
    pl._index.open()
    use_ts = int(ts) if ts is not None else int(_t.time())
    conn = pl._index._conn
    for i in range(n):
        conn.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx, kind, path, size, sha16) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (use_ts, "aiocqhttp", "g1", "u1", msg_id, i, "image",
             f"/fake/path_{i}.png", 100 + i, f"sha{i:x}"),
        )
    conn.commit()


def test_plugin_warden_lookup_command():
    banner("plugin: /warden lookup <msg_id>")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_lookup_")
    pl = _build_plugin_for_command(tmp, enable_index=True)
    asyncio.run(pl.initialize())
    _seed_index(pl, msg_id="m42", n=2)
    _seed_index(pl, msg_id="m99", n=1)

    class _R:
        def __init__(self, t): self.text = t
    class _E:
        def plain_result(self, t): return _R(t)
    ev = _E()

    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_lookup(e, "m42"))
    assert len(out) == 1
    text = out[0].text
    assert "lookup msg_id=m42" in text
    assert "(2 条)" in text
    assert "m99" not in text
    print(f"  OK -> {text.splitlines()[0]}")

    pl._index.close()


def test_plugin_warden_prune_command():
    banner("plugin: /warden prune <days>")
    import asyncio, time as _t
    tmp = tempfile.mkdtemp(prefix="warden_prune_")
    pl = _build_plugin_for_command(tmp, enable_index=True)
    asyncio.run(pl.initialize())
    pl._index.open()
    old_ts = int(_t.time()) - 100 * 86400
    new_ts = int(_t.time()) - 1 * 86400
    conn = pl._index._conn
    for i in range(2):
        conn.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx, kind, path, size, sha16) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (old_ts, "aiocqhttp", "g1", "u1", f"old{i}", 0, "image",
             f"/x/old_{i}.png", 10, f"o{i:x}"),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx, kind, path, size, sha16) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_ts, "aiocqhttp", "g1", "u1", f"new{i}", 0, "image",
             f"/x/new_{i}.png", 10, f"n{i:x}"),
        )
    conn.commit()

    class _R:
        def __init__(self, t): self.text = t
    class _E:
        def plain_result(self, t): return _R(t)
    ev = _E()

    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_prune(e, "50"))
    assert len(out) == 1
    assert "已删除 2 条" in out[0].text
    assert pl._index.count() == 2

    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_prune(e, "0"))
    assert "days 必须 > 0" in out[0].text

    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_prune(e, "abc"))
    assert "usage:" in out[0].text

    pl._index.close()
    print("  OK -> 2 old pruned, 2 new kept, errors handled")


def test_index_find_by_msg_filters():
    banner("index: find_by_msg returns only matching msg_id")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_findby_")
    pl = _build_plugin_for_command(tmp, enable_index=True)
    asyncio.run(pl.initialize())
    pl._index.open()
    _seed_index(pl, msg_id="m42", n=2)
    _seed_index(pl, msg_id="m99", n=1)

    rows = pl._index.find_by_msg("m42")
    assert len(rows) == 2
    assert all(r["msg_id"] == "m42" for r in rows)
    assert rows[0]["idx"] < rows[1]["idx"]

    rows2 = pl._index.find_by_msg("m99")
    assert len(rows2) == 1
    assert rows2[0]["msg_id"] == "m99"

    rows3 = pl._index.find_by_msg("nope")
    assert rows3 == []

    pl._index.close()
    print("  OK -> m42=2, m99=1, nope=0")


def test_index_count_and_prune_older_than():
    banner("index: count + prune_older_than (no file delete)")
    import asyncio, time as _t
    tmp = tempfile.mkdtemp(prefix="warden_count_")
    pl = _build_plugin_for_command(tmp, enable_index=True)
    asyncio.run(pl.initialize())
    pl._index.open()

    old_ts = int(_t.time()) - 30 * 86400
    new_ts = int(_t.time()) - 5 * 86400
    conn = pl._index._conn
    for i in range(3):
        conn.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx, kind, path, size, sha16) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (old_ts, "aiocqhttp", "g1", "u1", f"o{i}", 0, "image",
             f"/x/o_{i}.png", 10, f"o{i:x}"),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx, kind, path, size, sha16) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_ts, "aiocqhttp", "g1", "u1", f"n{i}", 0, "image",
             f"/x/n_{i}.png", 10, f"n{i:x}"),
        )
    conn.commit()

    assert pl._index.count() == 5
    cutoff = int(_t.time()) - 7 * 86400
    removed = pl._index.prune_older_than(cutoff)
    assert removed == 3
    assert pl._index.count() == 2

    removed2 = pl._index.prune_older_than(cutoff)
    assert removed2 == 0

    pl._index.close()
    print(f"  OK -> removed 3, left 2")


def test_plugin_e2e_concurrent_download_two_images():
    banner("plugin: end-to-end concurrent download of 2 image components")
    import asyncio, time as _t
    tmp = tempfile.mkdtemp(prefix="warden_concurrent_")
    pl = _build_plugin_for_command(
        tmp, enable_index=True, log_to_stdout=False,
    )
    pl.cfg.match_mode = "whitelist"
    pl.cfg.target_groups = ["g1"]
    pl.cfg.target_users = ["u1"]
    pl.cfg.max_concurrent = 4
    pl.cfg.download_retries = 0
    asyncio.run(pl.initialize())

    async def stub_fetcher(c, **kw):
        await asyncio.sleep(0.05)
        return Downloaded(
            data=b"\x89PNG\r\n\x1a\n" + b"x" * 50,
            mime="image/png", size=58,
        )

    from warden import downloader as dl_mod
    orig = dl_mod.aiohttp_fetcher
    dl_mod.aiohttp_fetcher = stub_fetcher
    try:
        comps = [
            _make_component(url="https://x/1.png", file_id="1"),
            _make_component(url="https://x/2.png", file_id="2"),
        ]
        t0 = _t.time()
        dls = asyncio.run(dl_mod.download_many(
            comps, fetcher=stub_fetcher, retries=0,
        ))
        elapsed = _t.time() - t0

        assert len(dls) == 2
        assert all(isinstance(d, Downloaded) for d in dls)
        assert elapsed < 0.09, f"expected < 0.09s, got {elapsed:.3f}s"
    finally:
        dl_mod.aiohttp_fetcher = orig

    pl._index.close()
    print(f"  OK -> 2 images in {elapsed:.3f}s (serial ~0.10s)")


def test_forwarder_render_with_images():
    banner("forwarder: render_with_images uses pre-downloaded bytes")
    import asyncio
    from warden.forwarder import Forwarder, ForwardNode
    fwd = Forwarder(width=400, max_nodes=10)

    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
        b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    nodes = [
        ForwardNode(sender_id="u1", sender_name="alice", text="look",
                    image_urls=["https://x/a.png"], time=1718600000),
        ForwardNode(sender_id="u2", sender_name="bob", text="seen",
                    image_urls=[], time=1718600001),
    ]
    node_imgs = {0: [tiny_png], 1: []}

    png = asyncio.run(fwd.render_with_images(nodes, node_imgs=node_imgs))
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000
    print(f"  OK -> {len(png)}B PNG (2 nodes, 1 pre-loaded image)")


PHASE4 = [
    test_downloader_retry_transient,
    test_downloader_no_retry_on_non_transient,
    test_downloader_give_up_after_max,
    test_downloader_many_concurrent,
    test_component_fetcher_local_url_reads_file,
    test_component_fetcher_fileurl_and_encoded,
    test_storage_path_safety_rejects_outside_root,
    test_storage_works_for_legit_paths,
    test_plugin_warden_lookup_command,
    test_plugin_warden_prune_command,
    test_index_find_by_msg_filters,
    test_index_count_and_prune_older_than,
    test_plugin_e2e_concurrent_download_two_images,
    test_forwarder_render_with_images,
]


def test_recover_raw_url_overrides_local_jpeg():
    banner("recover: raw_message http url overrides PreProcessStage-jpeg'd component")
    from warden.components import Component
    # ?? AstrBot 4.26 PreProcessStage ??????: url/file ???? jpeg
    comp = Component(kind="image", url=r"C:\\temp\\media_image_x.jpg",
                     file_id=r"C:\\temp\\media_image_x.jpg")
    media = [comp]
    # raw_message ?? napcat ???: ?? gif ? http ??
    raw = {"message": [
        {"type": "text", "data": {"text": "hi"}},
        {"type": "image", "data": {
            "file": "ABCD.gif",
            "url": "https://multimedia.nt.qq.com.cn/download?fileid=xyz",
            "file_size": "1226503",
        }},
    ]}
    ev = _fake_event(message=[{"type": "image", "data": {}}], raw_message=raw)

    refs = recover_raw_media_refs(ev)
    assert len(refs) == 1, refs
    assert refs[0]["kind"] == "image"
    assert refs[0]["url"].startswith("https://"), refs[0]
    assert refs[0]["name"] == "ABCD.gif"
    assert refs[0]["size"] == 1226503

    # ????????
    _ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    star = plugin_main.MediaWardenStar(_ctx, config=None)
    star._recover_original_urls(ev, media)
    assert comp.url == "https://multimedia.nt.qq.com.cn/download?fileid=xyz", comp.url
    assert comp.name == "ABCD.gif"
    assert comp.size == 1226503
    print("  OK -> component.url restored to original http gif link")


def test_recover_raw_no_http_keeps_local():
    banner("recover: no http in raw seg -> component untouched (no degrade)")
    from warden.components import Component
    comp = Component(kind="image", url=r"C:\\temp\\media_image_y.jpg")
    media = [comp]
    # ??? url ????????(napcat ???) -> ????
    raw = {"message": [
        {"type": "image", "data": {"file": "z.jpg",
                                   "url": r"C:\\temp\\media_image_y.jpg"}},
    ]}
    ev = _fake_event(message=[{"type": "image", "data": {}}], raw_message=raw)
    refs = recover_raw_media_refs(ev)
    assert refs[0]["url"] is None, "local path should not be treated as http url"
    _ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    star = plugin_main.MediaWardenStar(_ctx, config=None)
    star._recover_original_urls(ev, media)
    assert comp.url == r"C:\\temp\\media_image_y.jpg", "must stay unchanged"
    # ? raw_message ?????
    ev2 = _fake_event(message=[{"type": "image", "data": {}}], raw_message=None)
    star._recover_original_urls(ev2, [Component(kind="image", url="x")])
    print("  OK -> local-only raw and no-raw both leave component unchanged")


PHASE4 += [
    test_recover_raw_url_overrides_local_jpeg,
    test_recover_raw_no_http_keeps_local,
]


if __name__ == "__main__":
    for t in PHASE1 + PHASE2 + PHASE3 + PHASE4:
        t()
    print("\nALL OK (phase1=%d phase2=%d phase3=%d phase4=%d)"
          % (len(PHASE1), len(PHASE2), len(PHASE3), len(PHASE4)))
