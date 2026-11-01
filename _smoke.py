"""Smoke v1.1: 骨架 + 配置 + 钩子 + 策略 + 组件识别 + 下载 + 落盘 + dedupe.

独立运行,不依赖 AstrBot runtime.通过在 sys.modules 注入 astrbot.* stub
让 main.py 的 import 通路走通,然后直接调用核心函数验证.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

PERMISSION_CALLS: list = []


def _install_stub():
    a = types.ModuleType("astrbot")
    ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star")
    em = types.ModuleType("astrbot.api.event")
    mc = types.ModuleType("astrbot.api.message_components")

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

    class _PT:
        ADMIN = "admin"
        MEMBER = "member"

    class _F:
        EventMessageType = _MT
        PermissionType = _PT

        def event_message_type(self, *a, **k):
            def deco(fn):
                return fn

            return deco

        def command(self, *a, **k):
            def deco(fn):
                return fn

            return deco

        def permission_type(*a, **k):
            PERMISSION_CALLS.append(a[0] if a else k)

            def deco(fn):
                return fn

            return deco

    sm.Star = Star
    sm.register = register
    sm.Context = Context
    em.filter = _F
    em.AstrMessageEvent = AstrMessageEvent
    em.EventMessageType = _MT

    class At:
        def __init__(self, qq=None, name=""):
            self.qq = qq
            self.name = name

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class Reply:
        def __init__(self, id=None, **kw):
            self.id = id
            for k, v in kw.items():
                setattr(self, k, v)

    class Face:
        def __init__(self, id=None, **kw):
            self.id = id
            for k, v in kw.items():
                setattr(self, k, v)

    class Image:
        def __init__(self, file="", **kw):
            self.file = file
            for k, v in kw.items():
                setattr(self, k, v)

        @classmethod
        def fromBytes(cls, data):
            return cls(file=data)

    class Video:
        def __init__(self, file="", **kw):
            self.file = file
            self.url = kw.get("url", "")
            self.path = kw.get("path", "")

        @classmethod
        def fromFileSystem(cls, path, **kw):
            from pathlib import Path as _P
            return cls(file=_P(path).resolve(strict=False).as_uri(),
                       path=str(path), **kw)

    class Node:
        def __init__(self, content=None, **kw):
            self.content = list(content or [])
            self.name = kw.get("name", "")
            self.uin = kw.get("uin", "0")

    class Nodes:
        def __init__(self, nodes=None, **kw):
            self.nodes = list(nodes or [])

    mc.At = At
    mc.Plain = Plain
    mc.Reply = Reply
    mc.Face = Face
    mc.Image = Image
    mc.Video = Video
    mc.Node = Node
    mc.Nodes = Nodes
    sys.modules["astrbot"] = a
    sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm
    sys.modules["astrbot.api.event"] = em
    sys.modules["astrbot.api.message_components"] = mc


_install_stub()


def _install_aiohttp_stub_if_missing():
    """没有 aiohttp 时给个导入期占位符.

    warden/avatar.py 与 warden/downloader.py 在模块层 import aiohttp;
    开发机上通常没有这个包(它由 AstrBot 运行环境提供),没有桩就 import 不进 main.py.
    这里只做导入期兼容:任何真正的 HTTP 调用都会显式报错.
    有真 aiohttp 的环境不受影响(立即 return).
    """
    try:
        import aiohttp  # noqa: F401
        return
    except ImportError:
        pass

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None, **kw):
            self.total = total

    class ClientSession:
        def __init__(self, *a, **kw):
            raise RuntimeError("aiohttp 未安装:测试请注入 fetcher 替身")

    cc = types.ModuleType("aiohttp")
    cc.ClientError = ClientError
    cc.ClientTimeout = ClientTimeout
    cc.ClientSession = ClientSession
    sys.modules["aiohttp"] = cc


_install_aiohttp_stub_if_missing()

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
# 插件自己的 warden 包(与顶层 'warden' 是两份模块);打桩要打在插件这一份上
plugin_warden = importlib.import_module(_pname + ".warden")
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
    download, DownloadError, Downloaded,
)
from warden.storage import Storage, SaveContext, render_filename, _safe_name, _guess_ext

# 关键: 上面这些名字来自顶层的 warden.*,而插件内部用的是它自己包里的
# <pkg>.warden.* —— 两份 DownloadError 是**不同的类对象**。测试里若是用顶层
# 那个类抛错,插件的 except DownloadError 根本不会匹配(回退链被整段跳过)。
# 这里统一改成插件那一份,保证测试与插件判的是同一个类。
DownloadError = plugin_warden.downloader.DownloadError
Downloaded = plugin_warden.downloader.Downloaded
download = plugin_warden.downloader.download
aiohttp_fetcher = plugin_warden.downloader.aiohttp_fetcher
astrbot_component_fetcher = plugin_warden.downloader.astrbot_component_fetcher
record_store = plugin_warden.record
record_admin = importlib.import_module(_pname + ".warden.record_admin")
record_page = importlib.import_module(_pname + ".warden.record_page")


def banner(t):
    print("\n=== " + t + " ===")


# =========================================================
# Phase 1 测试
# =========================================================


def test_config_lenient_with_overrides():
    banner("config: lenient load accepts known overrides + drops unknowns")
    cfg, errors = MediaWardenConfig.from_raw_lenient({
        "match_mode": "blacklist",
        "target_groups": ["aiocqhttp:111", "222"],
        "max_file_size_mb": 50,
        "unknown_field_xxx": "ignored",
    })
    assert errors == [], errors
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


def test_config_rejects_zero_concurrency():
    banner("config: max_concurrent must be >= 1")
    try:
        MediaWardenConfig(max_concurrent=0)
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "max_concurrent" in str(e)
    cfg = MediaWardenConfig(max_concurrent=1)
    assert cfg.max_concurrent == 1
    assert "aiocqhttp" in cfg.avatar_platforms
    assert "qq_official" in cfg.avatar_platforms
    print("  OK -> max_concurrent=0 rejected, default avatar whitelist present")


def test_config_from_raw_lenient_preserves_valid_fields():
    banner("config: lenient raw keeps valid fields, reports invalid ones")
    cfg, errors = MediaWardenConfig.from_raw_lenient({
        "storage_root": "/custom/root",
        "max_concurrent": 0,
        "match_mode": "whitelist",
    })
    assert cfg.storage_root == "/custom/root"
    assert cfg.max_concurrent == 4  # invalid value skipped -> default
    assert cfg.match_mode == "whitelist"
    assert any("max_concurrent" in e for e in errors)

    cfg2, errors2 = MediaWardenConfig.from_raw_lenient({
        "avatar_platforms": "aiocqhttp",
        "storage_root": "/custom/root2",
    })
    assert cfg2.avatar_platforms == ["aiocqhttp", "qq_official"]
    assert cfg2.storage_root == "/custom/root2"
    assert any("avatar_platforms" in e for e in errors2)
    print(f"  OK -> valid fields kept, errors={errors} | avatar_platforms type skipped: {errors2}")


def test_policy_key_format():
    banner("policy: group key normalizes platform/lower")
    from warden.policy import _key
    assert _key("aiocqhttp", "123") == "aiocqhttp:123"
    assert _key("AIOCQHTTP", "123") == "aiocqhttp:123"
    assert _key(None, None) == ""
    print("  OK")


def _archived_records(root):
    """读 _records/ 下所有条目的 record.json(聊天记录的结构化存档)。"""
    import json as _json
    out = []
    base = os.path.join(root, "_records")
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "record.json")
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            out.append(_json.load(fh))
    return out

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
        def chain_result(self, chain): return _Result(chain)
        def image_result(self, p): return _Result(p)
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


def test_policy_group_whitelist_case_insensitive():
    banner("policy: group whitelist normalizes platform prefix case")
    cfg = MediaWardenConfig(target_groups=["AIOCQHTTP:G1"], target_users=["u1"])
    assert evaluate(cfg, _fake_event(platform="aiocqhttp",
                                     group_id="g1", user_id="u1")).allow is True
    cfg2 = MediaWardenConfig(match_mode="blacklist",
                             target_groups=["AIOCQHTTP:G1"])
    assert evaluate(cfg2, _fake_event(platform="aiocqhttp",
                                      group_id="g1", user_id="u1")).allow is False
    print("  OK")


def test_policy_whitelist_empty_lists_means_nothing_saved():
    banner("policy: empty whitelist = save nothing（v1.14.0 语义反转）")
    cfg = MediaWardenConfig(target_groups=[], target_users=[], match_mode="whitelist")
    d = evaluate(cfg, _fake_event(group_id="g1", user_id="u1"))
    assert d.allow is False and "empty" in d.reason, d
    # 群配上、人不配 = 仍然不保存
    cfg2 = MediaWardenConfig(target_groups=["aiocqhttp:g1"], target_users=[])
    d2 = evaluate(cfg2, _fake_event(group_id="g1", user_id="u1"))
    assert d2.allow is False and "sender" in d2.reason, d2
    # "*" = 该群所有人
    cfg3 = MediaWardenConfig(target_groups=["aiocqhttp:g1"], target_users=["*"])
    assert evaluate(cfg3, _fake_event(group_id="g1", user_id="u1")).allow is True
    assert evaluate(cfg3, _fake_event(group_id="g1", user_id="u42")).allow is True
    assert evaluate(cfg3, _fake_event(group_id="g2", user_id="u1")).allow is False
    # 黑名单空 = 不限（逃生口）
    cfg4 = MediaWardenConfig(match_mode="blacklist", target_groups=[], target_users=[])
    assert evaluate(cfg4, _fake_event(group_id="g9", user_id="u9")).allow is True
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
        {"type": "node",  "data": {
            "resid": "outer-resid",
            "content": [{"type": "text", "data": {"text": "merged"}}],
        }},
    ])
    cs = extract_components(e)
    kinds = [c.kind for c in cs]
    assert kinds == ["text", "image", "voice", "file", "video", "json", "forward"]
    assert cs[1].url == "https://x/a.jpg"
    assert cs[3].name == "f.zip" and cs[3].size == 2048
    assert cs[6].is_forward and isinstance(cs[6].meta.get("nodes"), list)
    assert cs[6].meta.get("forward_id") == "outer-resid"
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
    assert "图片×1" in txt and "视频×1" in txt
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


def test_plugin_construct_with_invalid_config_keeps_valid_fields():
    banner("plugin: invalid config field skipped, valid fields preserved")
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    inst = plugin_main.MediaWardenStar(ctx, config={
        "match_mode": "nope",
        "target_users": ["u1"],
        "storage_root": "/custom/root",
        "max_file_size_mb": 33,
    })
    assert inst.cfg.match_mode == "whitelist"
    assert "u1" in inst.cfg.matched_users_set()
    assert inst.cfg.storage_root == "/custom/root"
    assert inst.cfg.max_file_size_bytes == 33 * 1024 * 1024
    assert inst._config_errors, "expected config warnings"
    assert any("match_mode" in e for e in inst._config_errors)
    print(f"  OK -> valid fields kept, warnings={inst._config_errors}")


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
        assert "图片×1" in text
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

def test_plugin_e2e_forward_image_all_fail_not_saved_and_at():
    """端到端: 转发图内某图全部下载失败 -> 整图判失败不落盘, 且 @ 监听对象."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_image": True,
            "download_retries": 0,
            "enable_index_db": False,
            "reply_preview": False,
        })
        asyncio.run(inst.initialize())
        if inst._forwarder is None:
            print("  SKIP -> PIL unavailable")
            return
        # 让所有图片下载失败(patch main 模块里引用的 download 名)
        _m = plugin_main
        DownloadError = _m.DownloadError
        orig_download = _m.download

        async def _fail_download(comp, *a, **k):
            raise DownloadError("forced timeout")

        _m.download = _fail_download
        try:
            ev = _fake_event(
                group_id="g1", user_id="u1", nickname="alice",
                message=[
                    {"type": "node", "data": {"content": [
                        {"type": "node", "data": {
                            "user_id": "u2", "nickname": "bob",
                            "time": 1718600000,
                            "content": [
                                {"type": "text", "data": {"text": "hi"}},
                                {"type": "image", "data": {"url": "https://x/y.jpg"}},
                            ],
                        }},
                    ]}},
                ],
                message_id="mfail", timestamp=1718600000,
            )

            async def _drive():
                out = []
                async for x in inst.on_group_message(ev):
                    out.append(x)
                return out

            out = asyncio.run(_drive())
        finally:
            _m.download = orig_download
        # 1) 没有 PNG 落盘
        pngs = [os.path.join(r, f) for r, _, fs in os.walk(td)
                for f in fs if f.endswith(".png")]
        assert not pngs, f"unexpected png saved: {pngs}"
        # 2) 文本里报告失败
        head = out[0].text if hasattr(out[0], "text") else str(out[0])
        assert "失败" in head, head
        # 3) 末尾有一条 @ 链(chain_result -> list, 首元素是 At, qq=sender)
        at_msgs = [x for x in out if hasattr(x, "text") and isinstance(x.text, list)]
        assert at_msgs, "no @ chain yielded"
        chain = at_msgs[-1].text
        assert chain and chain[0].__class__.__name__ == "At", chain
        assert str(chain[0].qq) == "u1", chain[0].qq
        print("  OK -> no png saved, sender @ed:", chain[0].qq)



def test_plugin_e2e_forward_saved_as_json_sidecar():
    """端到端: 转发节点 -> JSON sidecar (mode=json 强制)."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_image": False,
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
        assert "聊天记录×1" in text, text
        # v1.16.0: 关掉渲染后不再落扁平 JSON —— 结构化数据就在条目 record.json 里
        recs = _archived_records(td)
        assert len(recs) == 1 and recs[0]["node_count"] == 1, recs
        assert recs[0]["nodes"][0]["segments"][0]["data"]["text"] == "merged", recs[0]
        flat = [f for _r, _d, fs in os.walk(td) for f in fs
                if f.endswith(".json") and "forward" in f]
        assert not flat, flat
        if inst._index is not None:
            inst._index.close()
            inst._index = None
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
                if f == ".schema_version":   # v1.18.0 归一化闸门文件,不算素材
                    continue
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
    test_config_lenient_with_overrides,
    test_config_rejects_invalid_mode,
    test_config_rejects_zero_concurrency,
    test_config_from_raw_lenient_preserves_valid_fields,
    test_policy_key_format,
    test_policy_whitelist_match_and_miss,
    test_policy_group_whitelist_case_insensitive,
    test_policy_whitelist_empty_lists_means_nothing_saved,
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
    test_plugin_construct_with_invalid_config_keeps_valid_fields,
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
    test_plugin_e2e_forward_image_all_fail_not_saved_and_at,
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
    # v1.9.5: 节点图的 segment data 要整份留下来(file=MD5 是本地缓存的钥匙)
    assert nodes[0].image_refs == [{"url": "https://x/a.png"}], nodes[0].image_refs
    assert nodes[1].text == "plain string"
    # file 与 url 并存(真实 napcat 形态)时两者都要在
    raw2 = [{"type": "node", "data": {"user_id": "u2", "content": [
        {"type": "image", "data": {"file": "AABB.jpg", "url": "https://x/b.jpg",
                                   "file_size": "123"}},
    ]}}]
    n2 = _coerce_nodes(raw2)[0]
    assert n2.image_urls == ["https://x/b.jpg"], n2.image_urls
    assert n2.image_refs[0]["file"] == "AABB.jpg", n2.image_refs
    assert n2.image_refs[0]["file_size"] == "123", n2.image_refs
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


def test_forwarder_render_nested_frame():
    banner("forwarder: render nested nodes with framed indentation")
    import asyncio
    import io
    from PIL import Image
    from warden.forwarder import Forwarder, ForwardNode

    fwd = Forwarder(width=400, max_nodes=30)
    nodes = [
        ForwardNode("alice", "u1", "outer", 1718600000, []),
        ForwardNode("bob", "u2", "inner", 1718600001, [], nesting_depth=1),
        ForwardNode("carol", "u3", "inner again", 1718600002, [], nesting_depth=2),
    ]
    png = asyncio.run(fwd.render(nodes))
    img = Image.open(io.BytesIO(png))
    assert img.size[0] == 400 and img.size[1] > 200
    print("  OK ->", img.size)


def test_forwarder_emoji_mixed_layout():
    banner("forwarder: emoji mixed-layout render (v1.6.0)")
    import asyncio
    import io
    from PIL import Image
    from warden.forwarder import (
        Forwarder, ForwardNode,
        _is_emoji_char, _wrap_mixed,
    )

    # 1. _is_emoji_char 单元
    assert _is_emoji_char("a") is False
    assert _is_emoji_char("中") is False
    assert _is_emoji_char(" ") is False
    assert _is_emoji_char("😊") is True
    assert _is_emoji_char("😀") is True
    assert _is_emoji_char("❤") is True
    assert _is_emoji_char("✨") is True
    assert _is_emoji_char("\u200d") is True   # ZWJ
    assert _is_emoji_char("\ufe0f") is True   # VS-16
    assert _is_emoji_char("\u20e3") is True   # keycap
    assert _is_emoji_char("🚩") is True      # SMP flag
    assert _is_emoji_char("") is False       # empty

    # 2. _wrap_mixed 切段
    fwd = Forwarder(width=400, max_nodes=30)
    fwd._ensure_fonts()
    if fwd._font_emoji is None:
        print("  SKIP (no system emoji font on this host)")
        return

    # 中文 + emoji 混合
    lines = _wrap_mixed("你好😊世界👍", fwd._font, fwd._font_emoji, 336)
    flat = [(seg, is_emoji) for line in lines for seg, is_emoji in line]
    assert flat == [("你好", False), ("😊", True), ("世界", False), ("👍", True)], flat

    # ZWJ family emoji 必须保持原子(不拆开)
    lines = _wrap_mixed("👨‍👩‍👧‍👦 test", fwd._font, fwd._font_emoji, 336)
    flat = [(seg, is_emoji) for line in lines for seg, is_emoji in line]
    assert flat[0] == ("👨\u200d👩\u200d👧\u200d👦", True), flat
    assert flat[1] == (" test", False), flat

    # VS-16 (variation selector) 不拆
    lines = _wrap_mixed("❤ test ❤️", fwd._font, fwd._font_emoji, 336)
    flat = [(seg, is_emoji) for line in lines for seg, is_emoji in line]
    assert flat[0] == ("❤", True), flat
    assert flat[2] == ("❤️", True), flat

    # 纯文本(无 emoji)必须切成一个段 + 整段 is_emoji=False
    lines = _wrap_mixed("plain ascii text", fwd._font, fwd._font_emoji, 336)
    flat = [(seg, is_emoji) for line in lines for seg, is_emoji in line]
    assert flat == [("plain ascii text", False)], flat

    # \n 强制换行 —— 不能合并段
    lines = _wrap_mixed("line1\nline2", fwd._font, fwd._font_emoji, 336)
    assert len(lines) == 2, lines
    assert lines[0] == [("line1", False)]
    assert lines[1] == [("line2", False)]

    # 3. 端到端渲染不崩(尺寸校验 + 字节大小合理)
    nodes = [
        ForwardNode("alice", "u1", "中文 + 😊 emoji + 👍 测试", 1718600000, []),
        ForwardNode("bob", "u2", "🚩🇨🇳🇯🇵 flags", 1718600100, []),
    ]
    png = asyncio.run(fwd.render(nodes))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(io.BytesIO(png))
    assert im.size[0] == 400
    assert im.size[1] > 100
    assert len(png) > 5000

    # 4. v1.7.2 regression: 长文本不再被塞到单行
    # 旧 bug: cluster 自身 cw > max_w 且 lines[-1] 空 (falsy) 时,永不切行 → 整段超宽被画布截断
    # 现 fix: 非 emoji cluster 超宽时退化为 char-level 切行
    from warden.forwarder import _char_w as _cw_probe
    long_cn = "中" * 80  # 80 * 18px = 1440px >> max_w=336
    lines = _wrap_mixed(long_cn, fwd._font, fwd._font_emoji, 336)
    assert len(lines) >= 4, f"长文本应切 ≥4 行,实际 {len(lines)} 行"
    for ln in lines:
        total_w = sum(
            sum(_cw_probe(c, fwd._font_emoji if is_emoji else fwd._font) for c in seg)
            for seg, is_emoji in ln
        )
        assert total_w <= 336, f"行宽 {total_w} 超 max_w=336: {[s for s,_ in ln]}"

    # 5. v1.7.2 regression: text_w 扣除头像宽度,图片不越界画布
    # 旧 bug: 有头像时 tx 已被推到 bubble_x + padding + avatar_size + 6,
    #         但 text_w 仍按 bubble_w - padding*2 算,导致图片右边超 cfg.width
    fwd2 = Forwarder(width=720, max_nodes=10)
    fwd2._ensure_fonts()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from PIL import Image as PI
        avatar = PI.new("RGBA", (36, 36), (255, 0, 0, 255))
        avatar_path = os.path.join(td, "avatar.png")
        avatar.save(avatar_path)
        test_img = PI.new("RGB", (720, 100), (0, 255, 0))
        buf = io.BytesIO()
        test_img.save(buf, format="PNG")
        nodes2 = [ForwardNode("alice", "u1", "测试", 1718600000, [], avatar_path=avatar_path)]
        png2 = asyncio.run(fwd2.render_with_images(nodes2, node_imgs={0: [buf.getvalue()]}))
        out = Image.open(io.BytesIO(png2))
        # 扫描画布最右 2 列,确认没有图片像素(绿)或气泡像素(白)溢出
        for y in range(50, min(out.size[1] - 50, 300)):
            for x in (out.size[0] - 1, out.size[0] - 2):
                p = out.getpixel((x, y))
                assert p != (0, 255, 0), f"图片溢出画布右边 @ ({x},{y}): {p}"
                assert p != (255, 255, 255), f"气泡溢出画布右边 @ ({x},{y}): {p}"

    print("  OK -> mixed-layout + ZWJ + VS-16 + long-text-wrap + avatar-textw all pass")


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


def test_plugin_e2e_forward_entry_render_and_record():
    """端到端: 渲染开关默认开 -> 条目里同时有 record.json 与 render.png,无扁平副本。"""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
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
        # 条目里应该同时有 render.png 与 record.json;扁平目录里一份都不该有
        recs = _archived_records(td)
        assert len(recs) == 1 and recs[0]["render"] == "render.png", recs
        rdir = record_store.record_dir(td, recs[0]["record_id"])
        assert os.path.isfile(os.path.join(rdir, "render.png")), rdir
        base_records = os.path.join(td, "_records")
        flat_files = [os.path.join(r, f) for r, _d, fs in os.walk(td) for f in fs
                      if f.endswith((".png", ".json"))
                      and not os.path.abspath(r).startswith(os.path.abspath(base_records))]
        assert not flat_files, flat_files
        # v1.14.1: 渲染图住在条目目录里,不再多落一份扁平 forward_image_*.png ——
        # 所以索引里只有 JSON 那一行(以前是 forward + forward_render 两行)。
        # 聊天记录不再产生扁平索引行(结构化在条目里,渲染图也在条目里)
        s = inst._index.stats()
        assert s["total"] == 0, s
        entry = record_store.list_records(td)
        assert len(entry) == 1 and entry[0]["kind"] == "forward", entry
        assert entry[0].get("has_render") or record_store.has_render(
            td, entry[0]["record_id"]) == "render.png"
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> 条目内 render.png + record.json, 索引 0 行, 无扁平副本")


def test_plugin_e2e_forward_too_large_falls_back_to_entry():
    """转发节点超过上限 -> 不渲染,但条目里照样有结构化记录."""
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            # 渲染开关默认开,但节点超过 forward_render_max_nodes=30
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
        # 不出图,但结构化记录在条目里(50 个节点都在)
        assert not [f for _r, _d, fs in os.walk(td) for f in fs if f.endswith(".png")], \
            "超限时不该出图"
        recs = _archived_records(td)
        assert len(recs) == 1 and recs[0]["node_count"] == 50, recs
        assert not recs[0].get("render"), recs[0]
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> 超限只存结构化(条目), 不出图")


def test_plugin_e2e_forward_expands_nested_two_levels():
    """端到端: 嵌套合并转发最多展开 2 层后再保存 JSON."""
    import asyncio
    import json
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_image": False,
            "enable_index_db": True,
        })
        asyncio.run(inst.initialize())

        responses = {
            "inner1": [
                {"type": "node", "data": {
                    "user_id": "u3", "nickname": "carol",
                    "time": 1718600001,
                    "content": [
                        {"type": "text", "data": {"text": "inner one"}},
                        {"type": "forward", "data": {"id": "inner2"}},
                    ],
                }},
            ],
            "inner2": [
                {"type": "node", "data": {
                    "user_id": "u4", "nickname": "dave",
                    "time": 1718600002,
                    "content": [
                        {"type": "text", "data": {"text": "inner two"}},
                    ],
                }},
            ],
        }
        calls = []

        class _Bot:
            async def call_action(self, action, **kwargs):
                assert action == "get_forward_msg"
                fwd_id = kwargs.get("message_id") or kwargs.get("id")
                calls.append(fwd_id)
                return {"messages": responses[fwd_id]}

        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "node", "data": {"content": [
                {"type": "node", "data": {
                    "user_id": "u2", "nickname": "bob",
                    "time": 1718600000,
                    "content": [
                        {"type": "text", "data": {"text": "outer"}},
                        {"type": "forward", "data": {"id": "inner1"}},
                    ],
                }},
            ]}}],
            message_id="m301", timestamp=1718600000,
        )
        ev.bot = _Bot()

        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)

        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        assert "聊天记录×1" in text, text
        assert calls == ["inner1", "inner2"], calls

        # v1.16.0: 结构化数据住在条目 record.json 里(不再落扁平 sidecar)
        recs = _archived_records(td)
        assert len(recs) == 1 and recs[0]["node_count"] == 3, recs
        dumped = json.dumps(recs[0], ensure_ascii=False)
        assert "outer" in dumped and "inner one" in dumped and "inner two" in dumped
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> nested calls=%s node_count=%s" % (calls, recs[0]["node_count"]))


def test_plugin_e2e_forward_prefers_fetched_sender_names():
    """端到端: 顶层有 forward id 时优先用 get_forward_msg 的完整昵称."""
    import asyncio
    import json
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td,
            "target_groups": ["aiocqhttp:g1"],
            "target_users": ["u1"],
            "forward_render_image": False,
            "enable_index_db": True,
        })
        asyncio.run(inst.initialize())
        calls = []

        class _Bot:
            async def call_action(self, action, **kwargs):
                assert action == "get_forward_msg"
                fwd_id = kwargs.get("message_id") or kwargs.get("id")
                calls.append(fwd_id)
                assert fwd_id == "outer-full"
                return {"messages": [
                    {"type": "node", "data": {
                        "user_id": "u2", "nickname": "真实昵称",
                        "time": 1718600000,
                        "content": [
                            {"type": "text", "data": {"text": "fetched"}},
                        ],
                    }},
                ]}

        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "node", "data": {
                "resid": "outer-full",
                "content": [
                    {"type": "node", "data": {
                        "user_id": "u2", "nickname": "QQ用户",
                        "time": 1718600000,
                        "content": [
                            {"type": "text", "data": {"text": "inline"}},
                        ],
                    }},
                ],
            }}],
            message_id="m302", timestamp=1718600000,
        )
        ev.bot = _Bot()

        async def _drive():
            gen = inst.on_group_message(ev)
            return await anext(gen)

        first = asyncio.run(_drive())
        text = first.text if hasattr(first, "text") else str(first)
        assert "聊天记录×1" in text, text
        assert calls == ["outer-full"], calls

        recs = _archived_records(td)
        assert len(recs) == 1, recs
        names = [n.get("name") for n in recs[0]["nodes"]]
        assert "真实昵称" in names, names
        assert "QQ用户" not in names, names
        dumped = json.dumps(recs[0], ensure_ascii=False)
        assert "fetched" in dumped and "inline" not in dumped, dumped
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK -> fetched sender name wins")


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
        # v1.11.0: list 与 lookup 共用 _format_asset_row —— 带上 sha16(msg_id 也在)
        assert "sha16=" in text, text
        assert "msg=m2#0" in text, text
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

        async def _drive(arg):
            gen = inst.cmd_warden_export(_ResultE(), arg)
            return await anext(gen)

        # 根目录内绝对路径
        out_path = os.path.join(td, "exp.json")
        out = asyncio.run(_drive(out_path))
        assert "已导出 1" in out.text, out.text
        with open(out_path, "r", encoding="utf-8") as f:
            d = _json.load(f)
        assert d["version"] == 1 and len(d["items"]) == 1

        # 空参数 -> 默认 _export.json
        out = asyncio.run(_drive(""))
        assert "_export.json" in out.text, out.text
        assert os.path.exists(os.path.join(td, "_export.json"))

        # 相对路径 -> 根目录内
        rel = os.path.join("sub", "rel.json")
        out = asyncio.run(_drive(rel))
        assert "已导出" in out.text, out.text
        assert os.path.exists(os.path.join(td, rel))

        # 越界绝对路径 / .. 相对路径 -> 拒绝
        with tempfile.TemporaryDirectory(prefix="warden_outside_") as outside:
            evil_abs = os.path.join(outside, "evil.json")
            out = asyncio.run(_drive(evil_abs))
            assert "导出路径非法" in out.text, out.text
            assert not os.path.exists(evil_abs)
        out = asyncio.run(_drive(os.path.join("..", "escape.json")))
        assert "导出路径非法" in out.text, out.text

        _close_index_safely(inst)
        print("  OK -> default/relative/inside ok, outside/.. rejected")


def _ResultE():
    """构造一个仅 plain_result 的轻量 event 供命令测试."""
    class _R:
        def __init__(self, t): self.text = t
    class _E:
        def plain_result(self, t): return _R(t)
    return _E()


def test_commands_require_admin_permission():
    banner("commands: all eight /warden commands registered ADMIN")
    admin = plugin_main.filter.PermissionType.ADMIN
    decorated = [c for c in PERMISSION_CALLS if c == admin]
    assert len(decorated) == 8, PERMISSION_CALLS
    print("  OK -> 8 commands registered with PermissionType.ADMIN")


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
            "forward_render_image": True,  # 同时有 PNG 和 JSON
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
        # 预览路径应以 .png 结尾;v1.14.1 起渲染图就在条目目录里(render.png),
        # 不再落扁平 forward_image_*.png,所以按「条目里的 render.png」断言。
        assert out[1].text.endswith(".png")
        assert os.path.basename(out[1].text) == "render.png", out[1].text
        assert os.path.isfile(out[1].text), out[1].text
        if inst._index is not None:
            inst._index.close()
            inst._index = None
        print("  OK ->", os.path.basename(out[1].text))


PHASE3 = [
    test_forwarder_coerce_nodes,
    test_forwarder_render_text_only,
    test_forwarder_render_with_image,
    test_forwarder_can_handle_node_limit,
    test_forwarder_render_nested_frame,
    test_index_record_and_recent,
    test_index_find_by_sha,
    test_index_stats,
    test_index_export_json,
    test_plugin_e2e_forward_entry_render_and_record,
    test_plugin_e2e_forward_too_large_falls_back_to_entry,
    test_plugin_e2e_forward_expands_nested_two_levels,
    test_plugin_e2e_forward_prefers_fetched_sender_names,
    test_plugin_warden_stats_command,
    test_plugin_warden_list_command,
    test_plugin_warden_export_command,
    test_commands_require_admin_permission,
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


def test_plugin_download_all_concurrent():
    banner("plugin: _download_all fetches concurrently, preserves order")
    import asyncio, time
    tmp = tempfile.mkdtemp(prefix="warden_many_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    pl.cfg.max_concurrent = 4
    pl.cfg.download_retries = 0
    pl.cfg.get_media_fallback = False   # 跳过本地缓存查找,只测并发下载与顺序
    asyncio.run(pl.initialize())

    comps = [_make_component(url=f"https://x/{i}.png", file_id=str(i))
             for i in range(5)]

    async def slow(c, **kw):
        await asyncio.sleep(0.05)
        return Downloaded(data=f"img-{c.file_id}".encode(), mime="image/png",
                          size=5 + len(c.file_id))

    orig = plugin_main.download
    plugin_main.download = slow
    try:
        t0 = time.time()
        out = asyncio.run(pl._download_all(comps, None))
        elapsed = time.time() - t0
    finally:
        plugin_main.download = orig

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


def test_storage_pointer_escape_ignored():
    banner("storage: tampered .ptr pointing outside root is ignored")
    root = tempfile.mkdtemp(prefix="warden_ptr_")
    with tempfile.TemporaryDirectory(prefix="warden_ptr_out_") as outside:
        evil = os.path.join(outside, "evil.jpg")
        with open(evil, "wb") as f:
            f.write(b"evil")
        s = Storage(root=root, dedupe=True,
                    pattern="{group_id}/{msg_id}_{short_id}{ext}", max_bytes=1000)
        comp = Component(kind="image", name="a.jpg", url="http://x/a.jpg")
        ctx1 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                           sender_name="alice", msg_id="m1", idx=0, ts=1718600000)
        first = s.save(b"tamper-bytes", comp, ctx1, mime="image/jpeg")
        assert not first.reused and first.blake16

        ptr = s._blake_path(first.blake16)
        rel_evil = os.path.relpath(evil, start=s.root)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(rel_evil)

        ctx2 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                           sender_name="alice", msg_id="m2", idx=0, ts=1718600001)
        second = s.save(b"tamper-bytes", comp, ctx2, mime="image/jpeg")
        assert not second.reused, "tampered pointer must not be trusted"
        assert os.path.realpath(second.path).startswith(
            os.path.realpath(root) + os.sep
        )
        assert os.path.exists(second.path)
    print("  OK -> escaped pointer ignored, re-saved inside root")


def test_storage_pointer_content_mismatch_ignored():
    banner("storage: .ptr target with changed content is not reused")
    root = tempfile.mkdtemp(prefix="warden_ptr_content_")
    s = Storage(root=root, dedupe=True,
                pattern="{group_id}/{msg_id}_{short_id}{ext}", max_bytes=1000)
    comp = Component(kind="image", name="a.jpg", url="http://x/a.jpg")
    data = b"original-bytes-abc"
    ctx1 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                       sender_name="alice", msg_id="m1", idx=0, ts=1718600000)
    first = s.save(data, comp, ctx1, mime="image/jpeg")
    assert not first.reused and first.blake16

    # 原地替换指针指向的目标文件内容(模拟文件被改动)
    with open(first.path, "wb") as f:
        f.write(b"mutated-bytes-xyz")

    ctx2 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                       sender_name="alice", msg_id="m2", idx=0, ts=1718600001)
    second = s.save(data, comp, ctx2, mime="image/jpeg")
    assert not second.reused, "pointer target content mismatch must not be trusted"
    with open(second.path, "rb") as f:
        assert f.read() == data

    ptr = s._blake_path(second.blake16)
    rel = open(ptr, encoding="utf-8").read().strip()
    assert os.path.normpath(os.path.join(root, rel)) == os.path.normpath(second.path), \
        "pointer must be repaired to the new file"

    ctx3 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                       sender_name="alice", msg_id="m3", idx=0, ts=1718600002)
    third = s.save(data, comp, ctx3, mime="image/jpeg")
    assert third.reused is True, "repaired pointer must be reused next time"
    assert os.path.normpath(third.path) == os.path.normpath(second.path)
    print("  OK -> changed target ignored, pointer repaired, third save reuses second path")


def test_forward_avatar_platform_whitelist():
    banner("forward: avatar fetch only for whitelisted platforms + image render")
    import asyncio
    ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        pl = plugin_main.MediaWardenStar(ctx, config={
            "storage_root": td, "enable_index_db": True,
            "log_to_stdout": False,
            "forward_render_image": True,
            "enable_user_avatar": True,
        })
        asyncio.run(pl.initialize())
        calls: list = []

        async def fake_fetch(cache, pairs, concurrency=4):
            calls.append(list(pairs))
            return {p: None for p in pairs}

        original = plugin_main.fetch_many_avatars
        plugin_main.fetch_many_avatars = fake_fetch
        try:
            nodes = [{
                "type": "node",
                "data": {
                    "user_id": "u1",
                    "nickname": "alice",
                    "content": [{"type": "text", "data": {"text": "hi"}}],
                },
            }]
            comp = Component(kind="forward", meta={"nodes": nodes})

            def _ctx(platform, msg_id):
                return SaveContext(platform=platform, group_id="g1",
                                   sender_id="sender", sender_name="sender",
                                   msg_id=msg_id, idx=0, ts=1718600000)

            asyncio.run(pl._save_forward(comp, _ctx("telegram", "m-tg"), None))
            assert calls == [], calls

            asyncio.run(pl._save_forward(comp, _ctx("aiocqhttp", "m-qq"), None))
            assert len(calls) == 1 and ("aiocqhttp", "u1") in calls[0], calls

            # json-only 模式不需要头像,不应再触发抓取
            pl.cfg.forward_render_image = False
            asyncio.run(pl._save_forward(comp, _ctx("aiocqhttp", "m-qq-json"), None))
            assert len(calls) == 1, calls
        finally:
            plugin_main.fetch_many_avatars = original
            _close_index_safely(pl)
    print("  OK -> telegram skipped, image fetches, json skips avatar fetch")


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


def _build_plugin_for_command(tmpdir, *, enable_index=True, log_to_stdout=False,
                              extra=None):
    cfg = {
        "storage_root": tmpdir,
        "enable_index_db": enable_index,
        "log_to_stdout": log_to_stdout,
        # v1.14.0: whitelist 空列表已变成「不保存」，这些用例测的是下游流水线，
        # 用黑名单空列表（= 不限）表达「全存」，不掺和策略本身。
        "match_mode": "blacklist",
    }
    if extra:
        cfg.update(extra)
    pl = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](), cfg)
    pl._sleep_scale = 0.0  # 不真等「迟到缓存重查」的 sleep
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

    # v1.11.0: 16 位十六进制的 msg_id 不该被当成 sha16 直接判"未找到"
    hexmsg = "0123456789abcdef"
    _seed_index(pl, msg_id=hexmsg, n=1)
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_lookup(e, hexmsg))
    assert f"lookup msg_id={hexmsg}" in out[0].text, out[0].text

    # sha16 命中路径
    pl._index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m-sha", idx=0, kind="image", path="/x/sha.png",
                     size=1, sha16="a" * 16)
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_lookup(e, "a" * 16))
    assert "出现 1 次" in out[0].text, out[0].text
    assert "sha16=" + "a" * 16 in out[0].text, out[0].text
    print("  OK -> sha16 命中 + 16-hex msg_id 回退到按 msg_id 查")

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
    assert removed["assets"] == 3, removed
    assert pl._index.count() == 2

    removed2 = pl._index.prune_older_than(cutoff)
    assert removed2["assets"] == 0, removed2

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

    # 走插件自己的并发入口(main._download_all),不是 downloader 的辅助函数
    pl.cfg.get_media_fallback = False
    orig = plugin_main.download
    plugin_main.download = stub_fetcher
    try:
        comps = [
            _make_component(url="https://x/1.png", file_id="1"),
            _make_component(url="https://x/2.png", file_id="2"),
        ]
        t0 = _t.time()
        dls = asyncio.run(pl._download_all(comps, None))
        elapsed = _t.time() - t0

        assert len(dls) == 2
        assert all(isinstance(d, Downloaded) for d in dls)
        assert elapsed < 0.09, f"expected < 0.09s, got {elapsed:.3f}s"
    finally:
        plugin_main.download = orig

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


def test_forwarder_transparent_image_shows_bubble_bg():
    banner("forwarder: transparent PNG composites onto bubble background")
    import asyncio, io
    from PIL import Image
    from warden.forwarder import Forwarder, ForwardNode

    src = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(15, 25):
        for y in range(15, 25):
            src.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    src.save(buf, format="PNG")

    fwd = Forwarder(width=240, max_nodes=5)
    nodes = [ForwardNode(sender_id="u1", sender_name="anon", text="",
                         image_urls=["https://x/trans.png"], time=0)]
    png = asyncio.run(fwd.render_with_images(nodes, node_imgs={0: [buf.getvalue()]}))
    out = Image.open(io.BytesIO(png)).convert("RGB")
    colors = out.getcolors(maxcolors=out.width * out.height)
    color_map = {c: n for n, c in (colors or [])}
    assert color_map.get((0, 0, 0), 0) == 0, "transparent pixels must not render black"
    assert color_map.get((255, 0, 0), 0) > 0, "opaque image pixels must render"
    assert color_map.get((255, 255, 255), 0) > 0, "bubble background must show through"
    print("  OK -> transparent area shows bubble white, opaque center visible")


def test_downloader_retries_aiohttp_client_error():
    banner("downloader: retries aiohttp.ClientError then succeeds")
    import asyncio
    import sys
    import types

    # aiohttp_fetcher 在函数内懒导入 aiohttp;这里给它一个最小桩(带 ClientError),
    # 用完恢复 sys.modules,避免污染后续用例。
    aiohttp = types.ModuleType("aiohttp")

    class _ClientError(Exception):
        pass

    class _ClientTimeout:
        def __init__(self, total=None, **kw):
            self.total = total

    class _ClientSession:
        def __init__(self, *a, **kw):
            raise RuntimeError("this test injects an explicit session")

    aiohttp.ClientError = _ClientError
    aiohttp.ClientTimeout = _ClientTimeout
    aiohttp.ClientSession = _ClientSession
    _orig_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = aiohttp

    class _Content:
        async def iter_chunked(self, n):
            yield b"PNG-bytes"

    class _Resp:
        status = 200
        headers = {"Content-Type": "image/png"}
        content = _Content()

    class _CM:
        def __init__(self, remaining):
            self.remaining = remaining

        async def __aenter__(self):
            if self.remaining[0] > 0:
                self.remaining[0] -= 1
                raise aiohttp.ClientError("connection reset by peer")
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Sess:
        def __init__(self):
            self.remaining = [1]

        def get(self, url):
            return _CM(self.remaining)

        async def close(self):
            pass

    comp = Component(kind="image", url="https://x/a.png")
    sess = _Sess()
    try:
        out = asyncio.run(
            download(
                comp,
                fetcher=aiohttp_fetcher,
                session=sess,
                retries=2,
                backoff_base=0.001,
                backoff_cap=0.01,
            )
        )
    finally:
        if _orig_aiohttp is not None:
            sys.modules["aiohttp"] = _orig_aiohttp
        else:
            sys.modules.pop("aiohttp", None)
    assert out.data == b"PNG-bytes"
    assert sess.remaining[0] == 0
    print("  OK -> retried transient ClientError then succeeded")


def test_save_forward_falls_back_when_forwarder_missing():
    banner("plugin: forwarder unavailable falls back to JSON sidecar")
    import asyncio
    import tempfile

    context = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(context, config={
            "storage_root": td,
            "forward_render_image": True,
            "enable_index_db": False,
            "enable_user_avatar": False,
        })
        asyncio.run(inst.initialize())
        inst._forwarder = None

        comp = Component(kind="forward", meta={"nodes": [
            {"type": "node", "data": {
                "nickname": "alice",
                "user_id": "u1",
                "content": [{"type": "text", "data": {"text": "hello"}}],
            }},
        ]})
        save_ctx = SaveContext(
            platform="aiocqhttp", group_id="g1", sender_id="u1",
            sender_name="alice", msg_id="m1", idx=0, ts=1718600000,
        )
        results = asyncio.run(inst._save_forward(comp, save_ctx, None))
        assert results, "expected JSON fallback result"
        assert all(r.ok for r in results), results
        assert any(r.path and r.path.endswith(".json") for r in results), results
        print("  OK -> JSON fallback path:", results[0].path)


def test_store_dl_records_true_media_kind():
    """落盘时按字节复核动图:多帧 GIF / 动画 WebP 记 gif,单帧记 image。"""
    banner("plugin: _store_dl 按字节复核动图,索引 kind 记真实类型")
    import types

    class _Storage:
        def save(self, data, comp, ctx, *, mime=None):
            return types.SimpleNamespace(path=ctx.msg_id + ".bin", size=len(data),
                                         blake16="b" * 16, reused=False)

    class _Index:
        def __init__(self):
            self.kinds = []

        def record(self, *, platform, group_id, sender_id, msg_id, idx, kind,
                   path, size, sha16=None, forward_meta=None):
            self.kinds.append(kind)

    inst = plugin_main.MediaWardenStar.__new__(plugin_main.MediaWardenStar)
    inst._storage = _Storage()
    inst._index = _Index()
    inst._log = lambda msg: None
    save_ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                           sender_name="alice", msg_id="m1", idx=0, ts=1718600000)
    # 两帧 GIF(含两个 image descriptor)
    gif = b"GIF89a" + b"\x00" * 10 + b"\x2c" + b"\x00" * 4 + b"\x2c" + b"\x00" * 4
    res = inst._store_dl(Component(kind="image", url="https://x/a.gif"), save_ctx,
                         Downloaded(data=gif, mime="image/gif", size=len(gif)))
    assert inst._index.kinds == ["gif"], inst._index.kinds
    assert res.kind == "gif" and res.preview_path, res
    # 静态 WebP(没有 ANIM 块):扩展名像动图,但字节说是静态 -> image
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
    inst._index.kinds.clear()
    res2 = inst._store_dl(Component(kind="image", url="https://x/b.webp"), save_ctx,
                          Downloaded(data=webp, mime="image/webp", size=len(webp)))
    assert inst._index.kinds == ["image"], inst._index.kinds
    assert res2.kind == "image", res2
    # 普通 png:ext 不判动图,走原路径
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    inst._index.kinds.clear()
    inst._store_dl(Component(kind="image", url="https://x/c.png"), save_ctx,
                   Downloaded(data=png, mime="image/png", size=len(png)))
    assert inst._index.kinds == ["image"], inst._index.kinds
    print("  OK -> 多帧 GIF -> gif,静态 WebP -> image,png -> image")

def test_store_dl_isolates_oserror():
    banner("plugin: _store_dl isolates OSError as failed AssetResult")

    class _Storage:
        def save(self, data, comp, ctx, *, mime=None):
            raise OSError("disk full")

    inst = plugin_main.MediaWardenStar.__new__(plugin_main.MediaWardenStar)
    inst._storage = _Storage()
    inst._index = None
    inst._log = lambda msg: None

    comp = Component(kind="image", name="a.png")
    save_ctx = SaveContext(
        platform="aiocqhttp", group_id="g1", sender_id="u1",
        sender_name="alice", msg_id="m1", idx=0, ts=1718600000,
    )
    dl = Downloaded(data=b"abc", mime="image/png", size=3)
    res = inst._store_dl(comp, save_ctx, dl)
    assert res.ok is False
    assert "OSError" in (res.err or "")
    print("  OK ->", res.err)


PHASE4 = [
    test_downloader_retry_transient,
    test_downloader_no_retry_on_non_transient,
    test_downloader_give_up_after_max,
    test_plugin_download_all_concurrent,
    test_component_fetcher_local_url_reads_file,
    test_component_fetcher_fileurl_and_encoded,
    test_storage_path_safety_rejects_outside_root,
    test_storage_works_for_legit_paths,
    test_storage_pointer_escape_ignored,
    test_storage_pointer_content_mismatch_ignored,
    test_forward_avatar_platform_whitelist,
    test_plugin_warden_lookup_command,
    test_plugin_warden_prune_command,
    test_index_find_by_msg_filters,
    test_index_count_and_prune_older_than,
    test_plugin_e2e_concurrent_download_two_images,
    test_forwarder_render_with_images,
    test_forwarder_transparent_image_shows_bubble_bg,
    test_downloader_retries_aiohttp_client_error,
    test_save_forward_falls_back_when_forwarder_missing,
    test_store_dl_isolates_oserror,
    test_store_dl_records_true_media_kind,
]


def test_recover_raw_url_overrides_local_jpeg():
    banner("recover: raw_message http url overrides PreProcessStage-jpeg'd component")
    from warden.components import Component
    # 复现 AstrBot 4.26 PreProcessStage 的覆盖: url/file 都成了 jpeg 静帧
    comp = Component(kind="image", url=r"C:\\temp\\media_image_x.jpg",
                     file_id=r"C:\\temp\\media_image_x.jpg")
    media = [comp]
    # raw_message 里是 napcat 的原始段: 真实 gif 的 http 链接
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

    # 恢复后组件应拿到原始链接、名字、大小
    _ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    star = plugin_main.MediaWardenStar(_ctx, config=None)
    star._recover_original_urls(ev, media)
    assert comp.url == "https://multimedia.nt.qq.com.cn/download?fileid=xyz", comp.url
    assert comp.name == "ABCD.gif"
    assert comp.size == 1226503
    # v1.9.1: segment 原始 file 必须留下来 —— PreProcessStage 覆盖后的 component.file_id
    # 是 jpeg 静帧名,get_image 拿它查不到(retcode=1200 file not found)
    assert comp.meta.get("raw_file") == "ABCD.gif", comp.meta
    assert comp.meta.get("fileid") == "xyz", comp.meta
    print("  OK -> component.url restored to original http gif link")


def test_recover_raw_no_http_keeps_local():
    banner("recover: no http in raw seg -> component untouched (no degrade)")
    from warden.components import Component
    comp = Component(kind="image", url=r"C:\\temp\\media_image_y.jpg")
    media = [comp]
    # 只有本地路径没有 http url(非 napcat 平台) -> 不改动
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
    # 没有 raw_message 时保持原样
    ev2 = _fake_event(message=[{"type": "image", "data": {}}], raw_message=None)
    star._recover_original_urls(ev2, [Component(kind="image", url="x")])
    print("  OK -> local-only raw and no-raw both leave component unchanged")


def test_recover_pairs_refs_by_identity_not_position():
    banner("recover: two images out of order -> each component keeps its own url")
    from warden.components import Component
    # 两个组件都已被 PreProcessStage 覆盖成静帧名,只有 file_id 留着原始 md5 名
    c1 = Component(kind="image", file_id="AAA.jpg", url=r"C:\\temp\\a_still.jpg")
    c2 = Component(kind="image", file_id="BBB.jpg", url=r"C:\\temp\\b_still.jpg")
    raw = {"message": [
        {"type": "image", "data": {"file": "BBB.jpg", "url": "https://cdn/b.jpg"}},
        {"type": "image", "data": {"file": "AAA.jpg", "url": "https://cdn/a.jpg"}},
    ]}
    ev = _fake_event(message=[{"type": "image", "data": {}},
                              {"type": "image", "data": {}}], raw_message=raw)
    _ctx = plugin_main.MediaWardenStar.__init__.__globals__["Context"]()
    star = plugin_main.MediaWardenStar(_ctx, config=None)
    star._recover_original_urls(ev, [c1, c2])
    assert c1.url == "https://cdn/a.jpg", c1.url
    assert c2.url == "https://cdn/b.jpg", c2.url
    assert c1.meta.get("raw_file") == "AAA.jpg", c1.meta
    assert c2.meta.get("raw_file") == "BBB.jpg", c2.meta
    print("  OK -> identity matching beats positional order")

    # 没有可配对的标识时退回顺序取:行为与 v1.9 一致,不会丢链接
    d1 = Component(kind="image", url=r"C:\\temp\\x.jpg")
    d2 = Component(kind="image", url=r"C:\\temp\\y.jpg")
    raw2 = {"message": [
        {"type": "image", "data": {"file": "p.jpg", "url": "https://cdn/p.jpg"}},
        {"type": "image", "data": {"file": "q.jpg", "url": "https://cdn/q.jpg"}},
    ]}
    ev2 = _fake_event(message=[{"type": "image", "data": {}},
                               {"type": "image", "data": {}}], raw_message=raw2)
    star._recover_original_urls(ev2, [d1, d2])
    assert d1.url == "https://cdn/p.jpg", d1.url
    assert d2.url == "https://cdn/q.jpg", d2.url
    print("  OK -> positional fallback still assigns one url per component")


PHASE4 += [
    test_recover_raw_url_overrides_local_jpeg,
    test_recover_raw_no_http_keeps_local,
    test_recover_pairs_refs_by_identity_not_position,
    test_forwarder_emoji_mixed_layout,
]


# =========================================================
# Phase 5 测试 (v1.9.0: 源链接失效补救 / 失败分类 / 失败入库 / 索引复用)
# =========================================================


def test_url_fileid_extraction():
    banner("main: _url_fileid pulls fileid out of a QQ rkey url")
    from urllib.parse import urlparse, parse_qs
    f = plugin_main._url_fileid
    assert f("https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=ABC&rkey=xyz") == "ABC"
    assert f("https://x/d?file_id=QQ1") == "QQ1"
    assert f("https://x/d?a=1") is None
    assert f("") is None
    assert f(None) is None
    print("  OK -> fileid extracted; non-qq url returns None")


def test_reporter_classify_fetch_vs_save():
    banner("reporter: fetch vs save failures are classified apart")
    from warden import classify_error as ce
    assert ce("download: http 400 for 'https://x/y'") == "fetch"
    assert ce("download: timeout after 30.0s for 'https://x/y'") == "fetch"
    assert ce("image download failed (2/2)") == "fetch"
    assert ce("save: OSError: disk full") == "save"
    assert ce("save: ValueError: path escapes storage root") == "save"
    assert ce("json save: nope") == "save"
    assert ce("something odd") == "other"

    br = BatchResult(items=[
        AssetResult(kind="image", ok=False, err="download: http 404 for 'u'"),
        AssetResult(kind="forward_render", ok=False, err="image download failed (1/1)"),
        AssetResult(kind="file", ok=False, err="save: OSError: x"),
    ], duration_s=0.5)
    txt = format_batch(br)
    assert "下载失败 2 + 落盘失败 1" in txt, txt
    assert br.failure_breakdown() == {"fetch": 2, "save": 1}, br.failure_breakdown()
    br2 = BatchResult(items=[
        AssetResult(kind="image", ok=False, err="download: http 400 for 'u'"),
        AssetResult(kind="file", ok=False, err="save: OSError: x"),
    ], duration_s=0.1)
    assert br2.failure_phrase().startswith("2/2 个媒体保存失败"), br2.failure_phrase()
    print("  OK -> head + 原因 line distinguish 源链接 vs 落盘")


def test_reporter_failure_phrase_fetch_only():
    banner("reporter: @ trigger phrase says 源链接已失效 for fetch-only failures")
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="/d/a.jpg", size=1),
        AssetResult(kind="image", ok=False, err="download: http 400 for 'u'"),
    ], duration_s=0.2)
    assert "下载失败(源链接已失效)" in br.failure_phrase(), br.failure_phrase()
    assert "1/2 已保存" in br.failure_phrase()
    txt = format_batch(br)
    assert "部分保存失败 (1/2) — 下载失败（源链接失效）" in txt, txt
    assert "成功 (1)" in txt and "失败 (1)" in txt, txt
    print("  OK ->", br.failure_phrase())


def test_downloader_falls_back_to_local_copy():
    banner("downloader: http failure falls back to the component's local copy")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    local = os.path.join(tmp, "cached.gif")
    payload = b"GIF89a-local-copy"
    with open(local, "wb") as fh:
        fh.write(payload)

    async def http_400(c, **kw):
        raise DownloadError(f"http 400 for {c.url!r}")

    comp = _make_component(url="https://multimedia.nt.qq.com.cn/download?fileid=A")
    comp.meta["path"] = local
    # astrbot_component_fetcher 内部查的是 downloader 模块全局的 aiohttp_fetcher
    orig = plugin_warden.downloader.aiohttp_fetcher
    plugin_warden.downloader.aiohttp_fetcher = http_400
    try:
        dl = asyncio.run(astrbot_component_fetcher(comp, max_bytes=1024 * 1024))
    finally:
        plugin_warden.downloader.aiohttp_fetcher = orig
    assert dl.data == payload, dl
    assert dl.size == len(payload)
    print("  OK -> local copy used after http 400")


def test_downloader_fallback_keeps_primary_error():
    banner("downloader: 本地副本也失败时保留原始错误 + fallback 说明")
    import asyncio
    comp = _make_component(url="https://x/a.png", file_id="f1")
    comp.meta["fileid"] = "f1"

    async def http_400(c, **kw):
        raise DownloadError("http 400 for 'https://x/a.png'")

    orig = plugin_warden.downloader.aiohttp_fetcher
    plugin_warden.downloader.aiohttp_fetcher = http_400
    try:
        try:
            asyncio.run(download(comp, fetcher=astrbot_component_fetcher, retries=0))
            raise AssertionError("expected DownloadError")
        except DownloadError as e:
            msg = str(e)
            assert "400" in msg, msg
            # 原始 400 保留,本地副本失败的原因挂在 fallback_error 上(不覆盖主错误)
            fb = getattr(e, "fallback_error", "")
            assert fb, "fallback_error should record why the rescue chain failed"
    finally:
        plugin_warden.downloader.aiohttp_fetcher = orig
    print("  OK -> primary 400 preserved, fallback note attached")


def test_main_get_media_fallback_uses_local_copy():
    banner("plugin: _download_one 在 url 与 get_image 都失败后回退本地副本")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    local = os.path.join(tmp, "cached.jpg")
    with open(local, "wb") as fh:
        fh.write(b"JPEG-LOCAL")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)

    async def http_400(c, **kw):
        raise DownloadError("http 400 for 'https://multimedia.nt.qq.com.cn/d'")

    # main.py 的 astrbot_component_fetcher 是模块级绑定,打 main 上的名字
    orig = plugin_main.astrbot_component_fetcher
    plugin_main.astrbot_component_fetcher = http_400
    try:
        comp = _make_component(
            url="https://multimedia.nt.qq.com.cn/download?fileid=A")
        comp.meta.update({"path": local, "fileid": "A"})
        dl = asyncio.run(pl._download_one(comp, event=None))
        assert dl.data == b"JPEG-LOCAL", dl
    finally:
        plugin_main.astrbot_component_fetcher = orig
    print("  OK -> local copy rescued the save")


def test_main_get_media_fallback_uses_get_image_url():
    banner("plugin: _download_one 用 OneBot get_image 重取链接后成功")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    pl.cfg.download_retries = 0
    seen = {}

    class _Bot:
        async def call_action(self, action, **kw):
            seen["action"] = action
            seen["file"] = kw.get("file")
            if action != "get_image":
                raise RuntimeError("no other action in this stub")
            return {"status": "ok", "data": {"url": "https://fresh.example/a.gif"}}

    class _Ev:
        bot = _Bot()

    async def fetcher(c, **kw):
        if "fresh" in c.url:
            return Downloaded(data=b"FRESH-GIF", mime="image/gif", size=9)
        raise DownloadError(f"http 400 for {c.url!r}")

    # 三级回退里,第一级走 downloader 模块全局的 aiohttp_fetcher,
    # 第二级(main.py 自己的模块级绑定)走 plugin_main.aiohttp_fetcher —— 两处都要打桩
    orig = plugin_warden.downloader.aiohttp_fetcher
    orig_main = plugin_main.aiohttp_fetcher
    plugin_warden.downloader.aiohttp_fetcher = fetcher
    plugin_main.aiohttp_fetcher = fetcher
    try:
        comp = _make_component(
            url="https://multimedia.nt.qq.com.cn/download?fileid=FF1&rkey=old")
        comp.meta["fileid"] = "FF1"
        dl = asyncio.run(pl._download_one(comp, _Ev()))
        assert dl.data == b"FRESH-GIF", dl
        assert dl.origin == "https://fresh.example/a.gif"
        assert seen == {"action": "get_image", "file": "FF1"}, seen
    finally:
        plugin_warden.downloader.aiohttp_fetcher = orig
        plugin_main.aiohttp_fetcher = orig_main
    print("  OK -> get_image returned a fresh url; original rkey was dead")


def test_main_media_refetch_candidates_prefer_raw_file():
    banner("plugin: 补救候选优先用 segment 原始 file,并兼容多种 get_image 参数形态")
    from astrbot_plugin_media_warden_smoke import main as _m
    comp = _make_component(url="https://multimedia.nt.qq.com.cn/download?fileid=FID7&rkey=dead")
    comp.file_id = "C0C9610D3203EB3B404638B24226AE23.jpg"  # PreProcessStage 覆盖后的值
    comp.meta["fileid"] = "FID7"
    comp.meta["raw_file"] = "C0C9610D3203EB3B404638B24226AE23.jpg"
    cands = _m._media_refetch_candidates(comp)
    assert cands[0] == "C0C9610D3203EB3B404638B24226AE23.jpg", cands
    assert cands[1] == "C0C9610D3203EB3B404638B24226AE23", cands  # 去扩展名的 basename
    assert "FID7" in cands, cands
    assert len(cands) <= 3, cands
    # 没有原始 file 时退到 fileid
    comp2 = _make_component(url="https://x/d?fileid=FID8")
    comp2.file_id = "jpeg.jpg"
    comp2.meta.clear()
    assert _m._media_refetch_candidates(comp2)[0] == "FID8"
    assert _m._b64_payload("data:image/png;base64,UE5H") == b"PNG"
    print("  OK -> raw segment file ranked first; fileid is the fallback")


def test_main_refetch_tries_param_forms_and_base64():
    banner("plugin: get_image 逐形态尝试(file/file_id/id/image)并支持 base64 返回")
    import asyncio
    import base64
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)

    calls = []
    payload = base64.b64encode(b"PNG-FROM-BASE64").decode()

    class _Api:
        async def call_action(self, action, **kw):
            calls.append((action, kw))
            if action != "get_image":
                raise RuntimeError("unsupported action")
            if kw.get("file_id") == "RAW-1":
                return {"status": "ok", "retcode": 0,
                        "data": {"base64": payload, "file_size": "15"}}
            raise RuntimeError("ActionFailed retcode=1200 file not found")

    class _Bot:
        api = _Api()

    class _Ev:
        bot = _Bot()

    comp = _make_component(url="https://multimedia.nt.qq.com.cn/download?fileid=FID9")
    comp.file_id = None
    comp.meta["raw_file"] = "RAW-1"
    dl, _trace = asyncio.run(pl._refetch_media(comp, _Ev()))
    assert dl is not None, "base64 branch should rescue the image"
    assert dl.data == b"PNG-FROM-BASE64", dl.data
    assert (dl.origin or "").startswith("base64:"), dl.origin
    # 第一个形态 file= 先试(线上就是它返回 file not found),随后 file_id= 命中
    assert calls[0] == ("get_image", {"file": "RAW-1"}), calls
    assert ("get_image", {"file_id": "RAW-1"}) in calls, calls
    print(f"  OK -> tried {len(calls)} (action, params) shape(s) before base64 hit")


def test_main_refetch_via_get_msg_regenerates_url():
    banner("plugin: get_image 全败后用 get_msg 让 napcat 重解析消息拿新链接")
    import asyncio
    from types import SimpleNamespace
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    pl.cfg.download_retries = 0

    # 消息里那条 url 已失效;get_msg 返回的段是 napcat 用当前 rkey 重新生成的
    dead = "https://multimedia.nt.qq.com.cn/download?fileid=FIDX&rkey=STALE"
    fresh = "https://multimedia.nt.qq.com.cn/download?fileid=FIDX&rkey=FRESH"
    seen_actions = []

    class _Api:
        async def call_action(self, action, **kw):
            seen_actions.append((action, {k: str(v)[:40] for k, v in kw.items()}))
            if action == "get_image":
                raise RuntimeError("ActionFailed retcode=1200 file not found")
            if action == "get_msg":
                # aiocqhttp 的 call_action 会把 OneBot 外层解包,这里照样返回「解包后」的形态
                return {
                    "message_id": 1795993480,
                    "message": [
                        {"type": "text", "data": {"text": "hi"}},
                        {"type": "image", "data": {"file": "C0C9610D.jpg",
                                                   "url": fresh,
                                                   "file_size": "1234"}},
                    ]}
            raise RuntimeError("unsupported action")

    class _Bot:
        api = _Api()

    msg_obj = SimpleNamespace(
        raw_message={"message_id": 1795993480, "group_id": 961583271},
        message_id="1795993480", group_id="961583271",
    )
    ev = SimpleNamespace(bot=_Bot(), message_obj=msg_obj, group_id="961583271")

    async def fetcher(c, **kw):
        if "FRESH" in (c.url or ""):
            return Downloaded(data=b"FRESH-FROM-GETMSG", mime="image/jpeg", size=17)
        raise DownloadError(f"http 400 for {c.url!r}")

    orig_dl = plugin_warden.downloader.aiohttp_fetcher
    orig_main = plugin_main.aiohttp_fetcher
    plugin_warden.downloader.aiohttp_fetcher = fetcher
    plugin_main.aiohttp_fetcher = fetcher
    try:
        comp = _make_component(url=dead)
        comp.meta["raw_file"] = "C0C9610D.jpg"
        dl, trace = asyncio.run(pl._refetch_media(comp, ev))
        assert dl is not None, "get_msg should rescue the image"
        assert dl.data == b"FRESH-FROM-GETMSG", dl.data
        assert dl.origin == fresh, dl.origin
        assert any(a == "get_msg" for a, _ in seen_actions), seen_actions
        # get_msg 的入参必须带 message_id(带 group_id 更稳)
        gm = [kw for a, kw in seen_actions if a == "get_msg"][0]
        assert gm.get("message_id") == "1795993480", gm
        # trace 会说清是靠 get_msg 救回来的(回执 note 用的就是它)
        assert any("get_msg" in ln for ln in trace), trace
    finally:
        plugin_warden.downloader.aiohttp_fetcher = orig_dl
        plugin_main.aiohttp_fetcher = orig_main
    # 多图消息里靠 file 名挑对那一段;挑不到就退回第一个候选
    resp = {"message": [
        {"type": "image", "data": {"file": "A.jpg", "url": "https://cdn/a.jpg"}},
        {"type": "image", "data": {"file": "B.jpg", "url": "https://cdn/b.jpg"}},
    ]}
    got = plugin_main._media_urls_from_resp(resp, old="", hint="B.jpg")
    assert got == ["https://cdn/b.jpg", "https://cdn/a.jpg"], got
    # aiocqhttp 解包后拿到的就是 {"message": [...]}(生产形态);带外层 {"data": {...}} 也要认
    assert plugin_main._segment_list(resp) == resp["message"]
    assert plugin_main._segment_list({"status": "ok", "data": resp}) == resp["message"]
    assert plugin_main._segment_list("not a dict") == []
    assert plugin_main._media_urls_from_resp({"status": "ok", "data": resp},
                                            old="", hint="A.jpg") == \
        ["https://cdn/a.jpg", "https://cdn/b.jpg"]
    print("  OK -> get_msg regenerated the rkey url and it downloaded")


def test_main_download_failure_records_to_index():
    banner("plugin: 下载全败 -> 失败入库(subject=fileid) 且回执带原因")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    asyncio.run(pl.initialize())

    async def http_400(c, **kw):
        raise DownloadError("http 400 for 'https://x/d?fileid=FID9'")

    orig = plugin_main.astrbot_component_fetcher
    plugin_main.astrbot_component_fetcher = http_400
    try:
        comp = _make_component(url="https://x/d?fileid=FID9")
        comp.meta["fileid"] = "FID9"
        ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                          sender_name="u", msg_id="m1", idx=0, ts=0)
        dl = asyncio.run(pl._download_all([comp], None))
        res = pl._store_dl(comp, ctx, dl[0])
        assert not res.ok
        assert res.err.startswith("download:"), res.err
        assert res.failure_kind == "fetch", res.failure_kind
        rows = pl._index.recent_failures(5)
        assert len(rows) == 1, rows
        assert rows[0]["subject"] == "fileid:FID9", rows[0]["subject"]
        assert rows[0]["err"].startswith("download:"), rows[0]["err"]
        assert rows[0]["attempts"] == 1
        # 同一 fileid 二次失败 -> attempts 累加(回执 note 提到"历史第 2 次")
        dl2 = asyncio.run(pl._download_all([comp], None))
        res2 = pl._store_dl(comp, ctx, dl2[0])
        assert not res2.ok
        assert "历史第 2 次" in (res2.note or ""), res2.note
        rows = pl._index.recent_failures(5)
        assert rows[0]["attempts"] == 2, rows[0]
        # 回执把它们显示出来
        txt = format_batch(BatchResult(items=[res, res2], duration_s=0.1))
        assert "失败 (2)" in txt
        assert "下载失败（源链接失效）" in txt, txt
    finally:
        plugin_main.astrbot_component_fetcher = orig
        pl._index.close()
    print("  OK -> failure persisted with fileid subject + attempts counter")


def test_main_index_reuse_saved_media():
    banner("plugin: index_reuse_saved 命中同 msg#idx 时不再下载")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    asyncio.run(pl.initialize())
    real = os.path.join(tmp, "already.png")
    with open(real, "wb") as fh:
        fh.write(b"PNG-ALREADY")
    pl._index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m7", idx=0, kind="image", path=real, size=11,
                     sha16="deadbeef")
    ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                      sender_name="u", msg_id="m7", idx=0, ts=0)
    comp = _make_component(url="https://x/dead-link.png")
    hit = pl._find_saved_asset("m7", 0)
    assert hit is not None, "index hit expected"
    res, _dl = pl._reuse_saved(comp, ctx, hit)
    assert res.ok and res.reused and res.path == real
    assert "索引复用" in (res.note or "")
    # 索引命中但文件不在 -> 明确报错,不伪装成功
    miss = dict(hit)
    miss["path"] = os.path.join(tmp, "gone.png")
    res2, _dl2 = pl._reuse_saved(comp, ctx, miss)
    assert not res2.ok and res2.failure_kind == "save", res2
    assert "missing" in res2.err, res2.err
    pl._index.close()
    print("  OK -> reuse skips download; missing file reported honestly")


def test_plugin_warden_fail_command():
    banner("plugin: /warden fail 列出失败记录")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    asyncio.run(pl.initialize())
    pl._index.record_failure(platform="aiocqhttp", group_id="g1", sender_id="u1",
                             msg_id="m9", idx=1, kind="image",
                             err="download: http 404 for 'x'", subject="fileid:Q")
    ev = _fake_event()
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_failures(e, "5"))
    text = "\n".join(getattr(r, "text", str(r)) for r in out)
    assert "保存失败" in text, text
    assert "fileid:Q" in text and "attempts=1" in text, text
    # stats 也带上失败数
    out2 = _drive_command(pl, ev, lambda e: pl.cmd_warden_stats(e))
    assert "failures: 1" in "\n".join(getattr(r, "text", str(r)) for r in out2), out2
    pl._index.close()
    print("  OK -> /warden fail + stats show failures")


def test_index_failure_stats_and_prune():
    banner("index: failure_stats / prune 覆盖 failures 表")
    import time as _t
    from warden.index import AssetIndex
    tmp = tempfile.mkdtemp(prefix="warden_v19_")
    idx = AssetIndex(os.path.join(tmp, "i.db"))
    idx.open()
    for i in range(3):
        idx.record_failure(platform="p", group_id="g", sender_id="u",
                           msg_id=f"m{i}", idx=0, kind="image",
                           err="download: http 400 for 'x'", subject="fileid:S")
    idx.record_failure(platform="p", group_id="g", sender_id="u",
                       msg_id="m9", idx=0, kind="forward_render",
                       err="image download failed (1/1)", subject="url:qpic/x")
    st = idx.failure_stats()
    assert st["total"] == 4, st
    assert st["by_subject"]["fileid:S"] == 3, st
    assert st["top"][0]["subject"] == "fileid:S" and st["top"][0]["count"] == 3, st
    # prune 覆盖 failures
    old = int(_t.time()) - 10 * 86400
    # 用「一分钟前」当 cutoff: 刚插入的行一定在它之后,与秒边界无关
    n = idx.prune_older_than(int(_t.time()) - 60)
    assert n["assets"] == 0 and n["failures"] == 0, n
    assert idx.failure_stats()["total"] == 4
    idx._conn.execute("UPDATE failures SET ts = ?", (old - 86400,))
    idx._conn.commit()
    n2 = idx.prune_older_than(int(_t.time()))
    assert n2["failures"] == 4, n2
    assert idx.failure_stats()["total"] == 0, idx.failure_stats()
    idx.close()
    print("  OK -> failure_stats aggregates by subject; prune clears failures too")


def test_localcache_finds_qq_image_by_md5():
    banner("localcache: 按消息段 MD5 找 QQ 本地图片缓存(大小写/扩展名不敏感)")
    tmp = tempfile.mkdtemp(prefix="warden_lc_")
    acct = os.path.join(tmp, "2510536451")
    c2c = os.path.join(acct, "Image", "C2C", "Image2")
    emo = os.path.join(acct, "nt_qq", "nt_data", "Emoji", "emoji-recv", "2026-10", "Ori")
    os.makedirs(c2c)
    os.makedirs(emo)
    md5 = "86bcfd43fed302fa3bf498f39e8c593e"
    c2c_path = os.path.join(c2c, md5 + ".gif")
    with open(c2c_path, "wb") as f:
        f.write(b"GIF89a-cached")
    emo_md5 = "a0a79925f70d32648280a014c0776cc1"
    emo_path = os.path.join(emo, emo_md5 + ".jpg")
    with open(emo_path, "wb") as f:
        f.write(b"JPEG-cached")
    # 更晚的月份目录里放同一 md5 的 Thumb,验证按月份倒序且 Ori 优先
    newer = os.path.join(acct, "nt_qq", "nt_data", "Emoji", "emoji-recv", "2026-11", "Thumb")
    os.makedirs(newer)
    newer_path = os.path.join(newer, md5 + ".jpg")
    with open(newer_path, "wb") as f:
        f.write(b"THUMB-not-preferred")

    from warden.localcache import find_cached_image
    orig = find_cached_image.__globals__["_account_dirs"]
    find_cached_image.__globals__["_account_dirs"] = lambda: [acct]
    try:
        # 扩展名大小写 / 有无都不影响命中 C2C
        assert find_cached_image(md5.upper() + ".GIF") == c2c_path, find_cached_image(md5 + ".gif")
        assert find_cached_image(md5) == c2c_path
        # 不在 C2C 的走 emoji/Ori,不吃更晚月份的 Thumb
        assert find_cached_image(emo_md5 + ".jpg") == emo_path
        assert find_cached_image(emo_md5) == emo_path
        # 不瞎猜
        assert find_cached_image("deadbeef" * 4 + ".jpg") is None
        assert find_cached_image("short.jpg") is None
        assert find_cached_image("") is None
        assert find_cached_image(None) is None
    finally:
        find_cached_image.__globals__["_account_dirs"] = orig
    print("  OK -> C2C hit by md5 (case/扩展名无关); emoji Ori 兜底; 未命中不误报")


def test_main_download_prefers_qq_local_cache():
    banner("plugin: 下载链第 ⓪ 级 —— QQ 本地缓存命中就不碰网络")
    import asyncio
    lg = plugin_warden.localcache
    tmp = tempfile.mkdtemp(prefix="warden_lc2_")
    acct = os.path.join(tmp, "2510536451")
    c2c = os.path.join(acct, "Image", "C2C", "Image2")
    os.makedirs(c2c)
    md5 = "a8b291afcf912ba3445a94c438bd210b"
    cached_path = os.path.join(c2c, md5 + ".gif")
    with open(cached_path, "wb") as f:
        f.write(b"GIF-from-QQ-cache")

    pl = _build_plugin_for_command(tempfile.mkdtemp(prefix="warden_lc2s_"))
    hits = {"n": 0}

    async def _boom(c, **kw):
        hits["n"] += 1
        raise DownloadError("http 400 for 'https://multimedia.nt.qq.com.cn/download?rkey=dead'")

    orig_acct = lg._account_dirs
    orig_dl = plugin_main.download
    lg._account_dirs = lambda: [acct]
    plugin_main.download = _boom
    try:
        comp = _make_component(
            url="https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=FIDX&rkey=dead")
        comp.file_id = md5.upper() + ".JPG"          # PreProcessStage 覆盖后的值
        comp.meta["raw_file"] = md5 + ".gif"         # segment 原始 file
        dl = asyncio.run(pl._download_one(comp, None))
        assert dl.data == b"GIF-from-QQ-cache", dl.data
        assert dl.origin == f"localcache:{cached_path}", dl.origin
        assert hits["n"] == 0, f"不该走网络: {hits['n']}"
        # 本地没有这个 md5 时,照旧走网络(并如实报错)
        comp2 = _make_component(
            url="https://multimedia.nt.qq.com.cn/download?fileid=FIDY&rkey=dead")
        comp2.file_id = "ffffffffffffffffffffffffffffffff.jpg"
        comp2.meta["raw_file"] = "ffffffffffffffffffffffffffffffff.jpg"
        try:
            asyncio.run(pl._download_one(comp2, None))
        except DownloadError as e:
            assert "http 400" in str(e), e
        else:
            raise AssertionError("miss 时应走网络并失败")
        assert hits["n"] >= 1, hits

        # get_media_fallback=false -> 关掉整条补救链(v1.8.x 行为),缓存不再被使用
        off = _build_plugin_for_command(tempfile.mkdtemp(prefix="warden_lc2o_"),
                                        extra={"get_media_fallback": False})
        assert off.cfg.get_media_fallback is False
        try:
            asyncio.run(off._download_one(comp, None))
        except DownloadError as e:
            assert "http 400" in str(e), e
        else:
            raise AssertionError("关闭补救后不应再命中本地缓存")
        assert hits["n"] >= 2, hits

        # 候选顺序:raw_file -> file_id -> meta.path -> url 尾段
        c3 = _make_component(
            url="https://multimedia.nt.qq.com.cn/download?fileid=FIDZ")
        c3.file_id = "BEEF.jpg"
        c3.meta["raw_file"] = "CAFE.gif"
        c3.meta["path"] = os.path.join(tmp, "somewhere", "F00D.png")
        cands = plugin_main._local_cache_candidates(c3)
        assert cands[0] == "CAFE.gif", cands
        assert "BEEF.jpg" in cands and "F00D.png" in cands, cands
        assert len(cands) <= 4, cands
        c4 = _make_component(url="")
        c4.file_id = None
        c4.meta.clear()
        assert plugin_main._local_cache_candidates(c4) == []
    finally:
        lg._account_dirs = orig_acct
        plugin_main.download = orig_dl
    print(f"  OK -> cache hit served {len(dl.data)}B without network; "
          f"miss fell through to http ({hits['n']} attempt(s)); "
          f"get_media_fallback=false 时缓存被忽略")


def test_forward_local_cached_uses_node_file_md5():
    banner("plugin: 转发节点图也走 QQ 本地缓存(节点 segment 的 file=MD5)")
    import asyncio
    lg = plugin_warden.localcache
    tmp = tempfile.mkdtemp(prefix="warden_lcf_")
    acct = os.path.join(tmp, "2510536451")
    c2c = os.path.join(acct, "Image", "C2C", "Image2")
    os.makedirs(c2c)
    md5 = "74760010d20c862b46a660054f601ead"
    path = os.path.join(c2c, md5 + ".jpg")
    with open(path, "wb") as f:
        f.write(b"JPEG-NODE-CACHED")

    pl = _build_plugin_for_command(tempfile.mkdtemp(prefix="warden_lcf2_"))
    hits = {"n": 0}

    async def _boom(c, **kw):
        hits["n"] += 1
        raise DownloadError("http 400 for 'https://multimedia.nt.qq.com.cn/download?rkey=x'")

    orig_acct = lg._account_dirs
    orig_dl = plugin_main.download
    lg._account_dirs = lambda: [acct]
    plugin_main.download = _boom
    try:
        ref = {"file": md5 + ".jpg", "url": "https://multimedia.nt.qq.com.cn/download?rkey=x",
               "file_size": "18749"}
        # 直接调 helper:命中本地缓存,返回 bytes,不碰网络
        data = asyncio.run(pl._forward_local_cached(ref))
        assert data == b"JPEG-NODE-CACHED", data
        assert hits["n"] == 0, hits

        # 整条 _save_forward 路径:节点图同样应在网络之前命中本地缓存
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            {"storage_root": tempfile.mkdtemp(prefix="warden_lcf8_"),
             "forward_render_image": True,
             "enable_index_db": False,
             "enable_user_avatar": False})
        asyncio.run(inst.initialize())
        assert inst._forwarder is not None, "需要可用的 forwarder 才能测渲染路径"
        url = "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=X&rkey=x"
        comp = Component(kind="forward", meta={"nodes": [
            {"type": "node", "data": {
                "nickname": "alice", "user_id": "u1", "time": 1718600000,
                "content": [
                    {"type": "text", "data": {"text": "看这图"}},
                    {"type": "image", "data": {"file": md5 + ".jpg", "url": url,
                                               "file_size": "18749"}},
                ],
            }},
        ]})
        save_ctx = SaveContext(
            platform="aiocqhttp", group_id="g1", sender_id="u1",
            sender_name="alice", msg_id="mf1", idx=0, ts=1718600000)
        results = asyncio.run(inst._save_forward(comp, save_ctx, None))
        assert results, results
        assert all(r.ok for r in results), [(r.kind, r.ok, r.err) for r in results]
        assert hits["n"] == 0, f"节点图应从本地缓存来,却走了网络 {hits['n']} 次"
        assert any(r.path and r.path.endswith(".png") for r in results), results

        # 没有 file 字段 -> 返回 None(照旧走网络)
        assert asyncio.run(pl._forward_local_cached({"url": "https://x/a.jpg"})) is None
        # 非 dict -> None
        assert asyncio.run(pl._forward_local_cached(None)) is None
        # 关掉补救开关 -> None
        off = _build_plugin_for_command(tempfile.mkdtemp(prefix="warden_lcf3_"),
                                        extra={"get_media_fallback": False})
        assert asyncio.run(off._forward_local_cached(ref)) is None
        # 文件不在缓存里 -> None
        assert asyncio.run(pl._forward_local_cached(
            {"file": "ffffffffffffffffffffffffffffffff.jpg"})) is None
    finally:
        lg._account_dirs = orig_acct
        plugin_main.download = orig_dl
    print(f"  OK -> 节点图 {len(data)}B 从本地缓存来(整条 _save_forward 路径网络调用 0 次); "
          f"无 file / 关开关 / 未命中都退回网络")


def test_main_late_local_cache_retry():
    banner("main: 网络全败后「等客户端落盘再查缓存」")
    import asyncio
    orig_dl = plugin_main.download
    orig_delays = plugin_main._LATE_CACHE_DELAYS_S
    hits = {"n": 0, "lookups": 0}
    small = os.path.join(tempfile.mkdtemp(prefix="warden_latef_"), "late.bin")
    with open(small, "wb") as fh:
        fh.write(b"late-bytes")

    async def boom(*a, **k):
        hits["n"] += 1
        raise plugin_main.DownloadError(
            "http 400 for 'https://multimedia.nt.qq.com.cn/x'")

    def fake_find(name):
        # ⓪ 级第一次查(cands 两个都是 miss) → 走网络 → 全败 → 迟到重查时命中
        hits["lookups"] += 1
        if hits["lookups"] >= 3:
            return small
        return None

    # 注意: main.py 的 `_cached_copy` 里是**函数内局部导入**
    # `from .warden.localcache import find_cached_image`, 所以打桩要打
    # 插件那一份 `warden.localcache` 模块对象本身的属性。
    lc_mod = importlib.import_module(_pname + ".warden.localcache")
    orig_find = lc_mod.find_cached_image

    try:
        plugin_main.download = boom
        plugin_main._LATE_CACHE_DELAYS_S = (0.0,)
        lc_mod.find_cached_image = fake_find
        pl = _build_plugin_for_command(tempfile.mkdtemp(prefix="warden_late_"))
        asyncio.run(pl.initialize())
        med = plugin_main.Component(
            kind="image", url="https://multimedia.nt.qq.com.cn/x",
            file_id="ABCDEF0123456789ABCDEF0123456789.jpg",
            meta={"raw_file": "ABCDEF0123456789ABCDEF0123456789.jpg"})
        dl = asyncio.run(pl._download_one(med, event=None))
        assert dl is not None and dl.data == b"late-bytes", (dl, hits)
        assert hits["n"] >= 1, hits
        assert hits["lookups"] >= 2, hits

        # 客户端始终没落盘 -> 仍抛原来的下载错误
        hits["n"] = 0
        hits["lookups"] = 0
        lc_mod.find_cached_image = lambda name: None
        try:
            asyncio.run(pl._download_one(med, event=None))
        except DownloadError as e:
            assert "http 400" in str(e), e
        else:
            raise AssertionError("应该抛 DownloadError")
    finally:
        lc_mod.find_cached_image = orig_find
        plugin_main.download = orig_dl
        plugin_main._LATE_CACHE_DELAYS_S = orig_delays
    print("  OK -> 首次 miss/网络 400 → 等待后第二次查到本地文件即成功; 始终没有仍报原错误")


# =========================================================
# v1.10.0: 审计补齐的覆盖(归在 PHASE5 里跑)
# =========================================================


def test_conf_schema_covers_all_config_fields():
    banner("schema: _conf_schema.json 与 MediaWardenConfig 字段一一对应")
    import json
    path = os.path.join(HERE, "_conf_schema.json")
    with open(path, encoding="utf-8") as fh:
        schema = json.load(fh)
    fields = set()
    for group, spec in schema.items():
        assert "description" in spec, group
        assert "items" in spec and isinstance(spec["items"], dict), group
        fields |= set(spec["items"].keys())
    expected = {f.name for f in MediaWardenConfig.__dataclass_fields__.values() if f.init}
    missing = expected - fields
    extra = fields - expected
    assert not missing, f"schema 缺少字段: {sorted(missing)}"
    assert not extra, f"schema 多出字段: {sorted(extra)}"
    # v1.10.0: log_to_stdout 以前只存在于代码里,WebUI 里根本配不到
    assert schema["logging"]["items"]["log_to_stdout"]["type"] == "bool"
    # v1.11.0: 枚举项给 WebUI 下拉选项,省得手敲字符串拼错
    for grp, key, want in (
        ("filter", "match_mode", ["whitelist", "blacklist"]),
        # v1.15.0: 引擎只剩 pil 一个实现，下拉不再假装有三个选项
        ("forward", "forward_image_engine", ["pil"]),
        ("auto_forward", "auto_forward_mode", ["native", "rebuild"]),
    ):
        got = schema[grp]["items"][key].get("options")
        assert got == want, (key, got)
    # schema 的默认值必须被 config 原样接受(拼错/类型不符会被整项静默丢弃)
    defaults = {k: spec.get("default")
                for grp in schema.values() for k, spec in grp["items"].items()}
    cfg, errs = MediaWardenConfig.from_raw_lenient(defaults)
    assert errs == [], errs
    assert cfg.match_mode == "whitelist" and cfg.download_retries == 2
    # v1.15.0 schema 重排:顺序就是用户读的顺序(监听 → 存什么 → 存哪 → 下载 → 渲染 → 样式 → 转发 → 回执 → 日志)
    assert list(schema) == ["filter", "save_types", "storage", "download",
                            "forward", "render_style", "auto_forward",
                            "reply", "logging"], list(schema)
    # 画布宽度只有一个旋钮(旧的 forward_image_width 已删,它总被 style_width 覆盖)
    assert "forward_image_width" not in fields, "重复的宽度旋钮应已删除"
    assert cfg.auto_forward_mode == "native", cfg.auto_forward_mode
    # v1.16.0: 三态 forward_render_mode -> 布尔 forward_render_image;
    # v1.18.0 去补丁化:旧键不再派生,给了也当未知键忽略
    assert cfg.forward_render_image is True, cfg.forward_render_image
    _stale, _e = MediaWardenConfig.from_raw_lenient({"forward_render_mode": "json"})
    assert _stale.forward_render_image is True and not _e, (_stale, _e)
    assert cfg.style_width == 720
    print(f"  OK -> {len(expected)} 个可配字段全覆盖,{len(schema)} 个分组")


def test_components_dict_and_object_paths_agree():
    banner("components: OneBot dict 段与 AstrBot 对象走同一条归一化路径")
    # face: 对象形态与 dict 形态必须得到同一个 Component
    class _CT:
        def __init__(self, v): self.value = v

    class _Comp:
        def __init__(self, type_name, **kw):
            self.type = _CT(type_name)
            for k, v in kw.items():
                setattr(self, k, v)

    obj = extract_components(_fake_event(message=[_Comp("Face", id=178)]))[0]
    dic = extract_components(_fake_event(
        message=[{"type": "face", "data": {"id": 178}}]))[0]
    assert (obj.kind, dic.kind) == ("json", "json"), (obj.kind, dic.kind)
    assert obj.meta == dic.meta == {"data": {"face_id": 178}}, (obj.meta, dic.meta)

    # image: dict 段里的 path 也要进 meta(以前只有对象形态保留)
    img = extract_components(_fake_event(message=[{
        "type": "image",
        "data": {"file": "a.jpg", "url": "https://x/a.png", "path": "/tmp/a.jpg"},
    }]))[0]
    assert img.kind == "image" and img.url == "https://x/a.png"
    assert img.file_id == "a.jpg"
    assert img.meta.get("path") == "/tmp/a.jpg", img.meta
    print("  OK -> face/image 两种形态字段一致")


def test_avatar_cache_disk_hit_and_lru():
    banner("avatar: 磁盘命中不重复抓取 + 超限按 mtime LRU 淘汰")
    import asyncio
    from warden.avatar import AvatarCache
    root = tempfile.mkdtemp(prefix="warden_avatar_")
    payload = b"JPEG" + b"a" * 60
    calls = []

    async def fetch(url):
        calls.append(url)
        return payload

    cache = AvatarCache(root, max_bytes=2 * len(payload), timeout_s=5)
    p1 = asyncio.run(cache.get_or_fetch("aiocqhttp", "u1", fetcher=fetch))
    assert p1 and os.path.isfile(p1)
    assert len(calls) == 1 and "nk=u1" in calls[0], calls
    os.utime(p1, (1000, 1000))
    # 第二次命中磁盘,不再调用 fetcher
    p1b = asyncio.run(cache.get_or_fetch("aiocqhttp", "u1", fetcher=fetch))
    assert p1b == p1 and len(calls) == 1, calls

    p2 = asyncio.run(cache.get_or_fetch("aiocqhttp", "u2", fetcher=fetch))
    os.utime(p2, (2000, 2000))
    p3 = asyncio.run(cache.get_or_fetch("aiocqhttp", "u3", fetcher=fetch))
    assert p3 and os.path.isfile(p3)
    assert not os.path.exists(p1), "最旧的头像应被 LRU 淘汰"
    assert os.path.exists(p2), "较新的头像应保留"

    async def boom(url):
        raise RuntimeError("network down")

    assert asyncio.run(cache.get_or_fetch("aiocqhttp", "u9", fetcher=boom)) is None

    async def huge(url):
        return b"x" * (cache.max_file_bytes + 1)

    assert asyncio.run(cache.get_or_fetch("aiocqhttp", "u8", fetcher=huge)) is None
    print(f"  OK -> 磁盘命中 1 次抓取;LRU 保留新文件;失败/超限返回 None")


def test_avatar_fetch_many_isolates_failures():
    banner("avatar: fetch_many 并发抓取,单项失败映射 None")
    import asyncio
    from warden.avatar import fetch_many

    class _Stub:
        async def get_or_fetch(self, platform, sender_id):
            return None if sender_id == "bad" else f"/cache/{sender_id}.jpg"

    out = asyncio.run(fetch_many(_Stub(), [("p", "ok"), ("p", "bad")], concurrency=2))
    assert out == {("p", "ok"): "/cache/ok.jpg", ("p", "bad"): None}, out
    assert asyncio.run(fetch_many(_Stub(), [], concurrency=2)) == {}
    print("  OK -> 单项失败不拖累其他项")


def test_index_migrates_legacy_schema():
    banner("index: 旧库缺列时自动补列(PRAGMA user_version + ALTER TABLE)")
    import sqlite3
    tmp = tempfile.mkdtemp(prefix="warden_mig_")
    db = os.path.join(tmp, "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts INTEGER NOT NULL, platform TEXT, group_id TEXT, sender_id TEXT,"
        " msg_id TEXT, idx INTEGER, kind TEXT, path TEXT, size INTEGER, sha16 TEXT);"
        "CREATE TABLE failures (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts INTEGER NOT NULL, platform TEXT, group_id TEXT, sender_id TEXT,"
        " msg_id TEXT, idx INTEGER, kind TEXT);"
    )
    conn.commit()
    conn.close()

    from warden.index import AssetIndex
    idx = AssetIndex(db)
    idx.open()
    assert "assets.forward_meta" in idx.migrated, idx.migrated
    assert "failures.subject" in idx.migrated, idx.migrated
    assert "failures.attempts" in idx.migrated, idx.migrated
    idx.record(platform="p", group_id="g", sender_id="u", msg_id="m", idx=0,
               kind="forward", path="/x", size=1, sha16="a" * 16,
               forward_meta={"node_count": 3})
    _id, attempts, _ts = idx.record_failure(
        platform="p", group_id="g", sender_id="u", msg_id="m", idx=0,
        kind="image", err="download: http 400", subject="fileid:S")
    assert attempts == 1
    rows = idx.find_by_msg("m")
    assert rows and rows[0]["forward_meta"] == {"node_count": 3}, rows
    idx.close()

    # 再开一次:已经补齐,不应重复 ALTER
    idx2 = AssetIndex(db)
    idx2.open()
    assert idx2.migrated == [], idx2.migrated
    idx2.close()
    print("  OK -> 补列 + 再开幂等")


def test_plugin_forward_without_nodes_reports_failure():
    banner("plugin: 转发节点取不到内容 -> 明确失败(不再落 nodes:null 的假成功)")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_fwdempty_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    asyncio.run(pl.initialize())
    comp = Component(kind="forward", meta={"nodes": [], "forward_id": "FID-EMPTY"})
    ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                      sender_name="u", msg_id="m-empty", idx=0, ts=0)
    results = asyncio.run(pl._save_forward(comp, ctx, None))
    assert results and not results[0].ok, results
    assert results[0].err.startswith("forward:"), results[0].err
    assert results[0].failure_kind == "fetch", results[0].failure_kind
    jsons = [f for _r, _d, fs in os.walk(tmp) for f in fs if f.endswith(".json")]
    assert not jsons, jsons
    rows = pl._index.recent_failures(5)
    assert rows and rows[0]["kind"] == "forward", rows
    pl._index.close()
    print(f"  OK -> {results[0].err[:40]}... 且已入库")


def test_config_rejects_unimplemented_render_engine():
    banner("config: forward_image_engine 只接受 pil（旧值报错并回落默认）")
    cfg, errs = MediaWardenConfig.from_raw_lenient({"forward_image_engine": "playwright"})
    assert errs and "forward_image_engine" in errs[0], errs
    assert cfg.forward_image_engine == "pil", cfg.forward_image_engine
    ok, e2 = MediaWardenConfig.from_raw_lenient({"forward_image_engine": "pil"})
    assert e2 == [] and ok.forward_image_engine == "pil", (ok.forward_image_engine, e2)
    print("  OK -> 旧值报错并回落 pil:", errs[0][:70])


def test_plugin_reply_to_original_quotes_message():
    banner("plugin: reply_to_original=true 时回执 chain 以 Reply 引用原消息")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_reply_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    pl.cfg.reply_to_original = True
    asyncio.run(pl.initialize())

    async def fake_download(comp, **kw):
        return Downloaded(data=b"PNG-DATA", mime="image/png", size=8)

    orig = plugin_main.download
    plugin_main.download = fake_download
    try:
        ev = _fake_event(
            group_id="g1", user_id="u1", message_id="m42",
            message=[{"type": "image",
                      "data": {"file": "a.jpg", "url": "https://x/a.png"}}],
        )

        async def _drive():
            gen = pl.on_group_message(ev)
            return await anext(gen)

        out = asyncio.run(_drive())
    finally:
        plugin_main.download = orig

    assert isinstance(out.text, list), out.text
    assert type(out.text[0]).__name__ == "Reply", out.text
    assert str(out.text[0].id) == "m42", out.text[0].id
    assert "✅" in out.text[-1].text, out.text[-1].text

    # 开关关闭(默认) -> 普通文本;message_id 不可用也不炸
    pl.cfg.reply_to_original = False
    assert pl._reply_prefix("m42") == []
    pl.cfg.reply_to_original = True
    assert pl._reply_prefix("nomsg") == []
    print("  OK -> Reply(id=m42) + Plain,关闭退化为 plain_result")


def test_main_index_reuse_is_scoped_to_platform_and_group():
    banner("plugin: 索引复用按 platform+group 限定,不跨群顶同名 msg_id")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_scope_")
    pl = _build_plugin_for_command(tmp, enable_index=True, log_to_stdout=False)
    asyncio.run(pl.initialize())
    real = os.path.join(tmp, "g2.png")
    with open(real, "wb") as fh:
        fh.write(b"X")
    pl._index.record(platform="aiocqhttp", group_id="g2", sender_id="u9",
                     msg_id="m77", idx=0, kind="image", path=real, size=1)
    assert pl._find_saved_asset("m77", 0, platform="aiocqhttp", group_id="g1") is None
    hit = pl._find_saved_asset("m77", 0, platform="aiocqhttp", group_id="g2")
    assert hit is not None and hit["path"] == real, hit
    pl._index.close()
    print("  OK -> g1 查不到 g2 的 m77")


# =========================================================
# Phase 6 (v1.11.0): 第二轮审计补齐的覆盖
# 这些用例单独成组 —— 它们查的是「真实平台行为」(Windows 路径、索引里的
# 失败行、临时文件残骸),与 v1.10 的功能补齐不是一回事。
# =========================================================


def test_reporter_windows_paths_show_common_dir():
    banner("reporter: Windows 反斜杠路径也能算出公共目录(以前 '路径:' 行永不出现)")
    br = BatchResult(items=[
        AssetResult(kind="image", ok=True,
                    path=r"C:\data\warden\g1\u1\a.jpg", size=1),
        AssetResult(kind="image", ok=True,
                    path=r"C:\data\warden\g1\u2\b.jpg", size=1),
    ])
    assert br.storage_root == "C:/data/warden/g1/", br.storage_root
    assert "路径: C:/data/warden/g1/" in format_batch(br)
    # 按字符比较会把 "C:/a/x.jpg" 与 "C:/ab/y.jpg" 切成 "C:/a";按目录段比较必须拒绝
    near = BatchResult(items=[
        AssetResult(kind="image", ok=True, path="C:/a/x.jpg"),
        AssetResult(kind="image", ok=True, path="C:/ab/y.jpg"),
    ])
    assert near.storage_root is None, near.storage_root
    print("  OK -> 反斜杠归一 + 按目录段比较,不再切出半截目录")


def test_components_file_kind_keeps_http_url_in_file():
    banner("components: file 段把 http 链接放在 file 上时仍能当下载源")
    comp = extract_components(_fake_event(message=[{
        "type": "file",
        "data": {"file": "https://x/a.pdf", "name": "a.pdf", "size": 12},
    }]))[0]
    assert comp.kind == "file" and comp.url == "https://x/a.pdf", comp
    assert comp.file_id == "https://x/a.pdf", comp.file_id
    assert comp.size == 12, comp.size
    print("  OK -> file.url 从 file 字段兜底(与 image/video/voice 一致)")


def test_plugin_forward_render_failure_is_indexed():
    banner("plugin: 转发渲染失败也入 failures 表(kind=forward_render, idx+100)")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_fwdfail_")
    pl = _build_plugin_for_command(
        tmp, enable_index=True, log_to_stdout=False,
        extra={"forward_render_image": True, "enable_user_avatar": False},
    )
    asyncio.run(pl.initialize())
    comp = Component(kind="forward", meta={"nodes": [
        {"type": "node", "data": {
            "nickname": "alice", "user_id": "u1",
            "content": [
                {"type": "text", "data": {"text": "see"}},
                {"type": "image",
                 "data": {"url": "https://x/dead.png", "file": "dead.md5"}},
            ],
        }},
    ]})
    ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                      sender_name="u", msg_id="m-ff", idx=3, ts=0)

    async def boom(*a, **kw):
        raise DownloadError("http 500 boom")

    orig = plugin_main.download
    plugin_main.download = boom
    try:
        results = asyncio.run(pl._save_forward(comp, ctx, None))
    finally:
        plugin_main.download = orig

    fails = [r for r in results if not r.ok]
    assert fails and fails[0].kind == "forward_render", results
    assert fails[0].failure_kind == "fetch", fails[0].failure_kind
    rows = pl._index.recent_failures(5)
    assert rows and rows[0]["kind"] == "forward_render", rows
    assert rows[0]["idx"] == 103, rows[0]
    assert rows[0]["subject"] == "forward_render:m-ff#103", rows[0]
    pngs = [f for _r, _d, fs in os.walk(tmp) for f in fs if f.endswith(".png")]
    assert not pngs, pngs
    pl._index.close()
    print("  OK -> failures 行 kind=forward_render idx=103 subject=forward_render:m-ff#103")


def test_storage_write_leaves_no_partial_files():
    banner("storage: 落盘走临时文件 + os.replace,不留 .part- 残骸")
    with tempfile.TemporaryDirectory() as td:
        st = Storage(td, dedupe=False)
        comp = Component(kind="image", url="https://x/a.bin", name="a.bin")
        ctx = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                          sender_name="u", msg_id="m-atomic", idx=0, ts=0)
        res = st.save(b"hello", comp, ctx, mime="application/octet-stream")
        with open(res.path, "rb") as fh:
            assert fh.read() == b"hello"
        leftovers = [f for _r, _d, fs in os.walk(td)
                     for f in fs if f.startswith(".part-")]
        assert not leftovers, leftovers
    print("  OK -> 写入落定且无临时残骸")


PHASE5 = [
    test_url_fileid_extraction,
    test_reporter_classify_fetch_vs_save,
    test_reporter_failure_phrase_fetch_only,
    test_downloader_falls_back_to_local_copy,
    test_downloader_fallback_keeps_primary_error,
    test_main_get_media_fallback_uses_local_copy,
    test_main_get_media_fallback_uses_get_image_url,
    test_main_media_refetch_candidates_prefer_raw_file,
    test_main_refetch_tries_param_forms_and_base64,
    test_main_refetch_via_get_msg_regenerates_url,
    test_main_download_failure_records_to_index,
    test_main_index_reuse_saved_media,
    test_plugin_warden_fail_command,
    test_index_failure_stats_and_prune,
    test_localcache_finds_qq_image_by_md5,
    test_main_download_prefers_qq_local_cache,
    test_forward_local_cached_uses_node_file_md5,
    test_main_late_local_cache_retry,
    # v1.10.0: 审计补齐的覆盖
    test_conf_schema_covers_all_config_fields,
    test_components_dict_and_object_paths_agree,
    test_avatar_cache_disk_hit_and_lru,
    test_avatar_fetch_many_isolates_failures,
    test_index_migrates_legacy_schema,
    test_plugin_forward_without_nodes_reports_failure,
    test_config_rejects_unimplemented_render_engine,
    test_plugin_reply_to_original_quotes_message,
    test_main_index_reuse_is_scoped_to_platform_and_group,
]

PHASE6 = [
    # v1.11.0: 第二轮审计补齐的覆盖
    test_reporter_windows_paths_show_common_dir,
    test_components_file_kind_keeps_http_url_in_file,
    test_plugin_forward_render_failure_is_indexed,
    test_storage_write_leaves_no_partial_files,
]


# =========================================================
# Phase 7 测试: 聊天记录存档 (v1.12 原型)
# =========================================================


def test_record_capture_roundtrip():
    import asyncio
    banner("record: capture 落盘 assets+record.json, build_nodes 还原")
    tmp = tempfile.mkdtemp(prefix="wd_record_")
    nodes = [
        {
            "sender": {"user_id": 206216334, "nickname": "橘雪莉", "card": ""},
            "time": 1720000000,
            "message": [
                {"type": "text", "data": {"text": "看图"}},
                {"type": "image", "data": {"url": "http://example/a.jpg?term=1"}},
                {"type": "face", "data": {"id": "14"}},
                {"type": "at", "data": {"qq": "all"}},
                {"type": "image", "data": {"file": "deadbeef.jpg"}},
            ],
        },
        {
            "sender": {"user_id": 2510536451, "nickname": "風見かずき", "card": "卡兹"},
            "time": 1720000001,
            "message": [{"type": "text", "data": {"text": "第二层"}}],
        },
    ]
    calls = []

    async def fetch(data):
        calls.append(data.get("url"))
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8

    rec = asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id=961583271, msg_id="m1",
        storage_root=tmp, fetch_image=fetch, ts=1720000000,
    ))
    assert rec["record_id"].endswith("-961583271-m1"), rec["record_id"]
    assert rec["node_count"] == 2, rec["node_count"]
    assert rec["nodes"][0]["uin"] == "206216334"
    assert rec["nodes"][1]["name"] == "卡兹"  # card 优先于 nickname
    # 只有一张图带可用 url;另一张只有 file=md5 -> missing(no_url)
    assert calls == ["http://example/a.jpg?term=1"], calls
    assert len(rec["assets"]) == 1, rec["assets"]
    assert rec["missing"] == [
        {"node": 0, "seg": 4, "kind": "image", "reason": "no_url"}
    ], rec["missing"]
    asset = rec["assets"][0]
    assert asset["path"].startswith("assets/") and asset["path"].endswith(".png")
    base = record_store.record_dir(tmp, rec["record_id"])
    assert os.path.exists(os.path.join(base, "record.json"))
    blob_path = os.path.join(base, asset["path"].replace("/", os.sep))
    assert os.path.getsize(blob_path) == len(b"\x89PNG\r\n\x1a\n" + b"0" * 8)

    loaded = record_store.load_record(tmp, rec["record_id"])
    assert loaded["origin_sha256"] == rec["origin_sha256"]

    comps = record_store.build_nodes(loaded, base)
    assert len(comps) == 2
    assert comps[0].uin == "206216334" and comps[0].name == "橘雪莉"
    kinds = [type(c).__name__ for c in comps[0].content]
    assert kinds == ["Plain", "Image", "Face", "At"], kinds
    assert comps[0].content[0].text == "看图"
    assert comps[0].content[1].file.startswith(b"\x89PNG")
    assert comps[0].content[2].id == 14
    assert comps[0].content[3].qq == "all"
    assert comps[1].content[0].text == "第二层"
    print("  OK")


def test_record_capture_dedupes_identical_bytes():
    import asyncio
    banner("record: 相同字节的两张图只落一个文件")
    tmp = tempfile.mkdtemp(prefix="wd_record_")

    async def fetch(data):
        return b"GIF89a" + b"2" * 4

    rec = asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "image", "data": {"url": "http://e/1.gif"}},
                      {"type": "image", "data": {"url": "http://e/2.gif"}}]}],
        platform="aiocqhttp", group_id=1, msg_id="d",
        storage_root=tmp, fetch_image=fetch, ts=1720000300,
    ))
    assert len(rec["assets"]) == 2, rec["assets"]
    assert rec["assets"][0]["path"] == rec["assets"][1]["path"]
    assets_dir = os.path.join(record_store.record_dir(tmp, rec["record_id"]),
                              "assets")
    assert len(os.listdir(assets_dir)) == 1
    print("  OK")


def test_record_capture_logs_download_failure():
    import asyncio
    banner("record: 单张图下载抛错只记 missing(不炸整条)")
    tmp = tempfile.mkdtemp(prefix="wd_record_")

    async def fetch(data):
        raise RuntimeError("boom")

    rec = asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "在吗"}},
                      {"type": "image", "data": {"url": "http://e/x.jpg"}}]}],
        platform="aiocqhttp", group_id=1, msg_id="f",
        storage_root=tmp, fetch_image=fetch, ts=1720000400,
    ))
    assert rec["assets"] == []
    assert rec["node_count"] == 1 and rec["nodes"][0]["segments"][0]["data"]["text"] == "在吗"
    assert rec["missing"] == [
        {"node": 0, "seg": 1, "kind": "image",
         "reason": "download_failed", "url": "http://e/x.jpg"}
    ], rec["missing"]
    print("  OK")


def test_record_capture_video_asset():
    import asyncio
    banner("record: 节点内视频落盘(以前只剩一段原始 JSON,url 过期就没)")
    tmp = tempfile.mkdtemp(prefix="wd_record_video_")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * 24

    async def fetch_image(data):
        raise AssertionError("视频不该走图片下载器")

    async def fetch_video(data):
        return mp4

    nodes = [{
        "sender": {"user_id": 1, "nickname": "白紫"},
        "time": 1720000500,
        "message": [
            {"type": "text", "data": {"text": "看视频"}},
            {"type": "video", "data": {"file": "5cd3.mp4",
                                       "url": "https://mm/a.mp4?rkey=x",
                                       "file_size": "10048828"}},
            # 只有文件名、没有可下载 url：记 missing(no_url),不猜
            {"type": "video", "data": {"file": "deadbeef.mp4"}},
        ],
    }]
    rec = asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id=961583271, msg_id="v1",
        storage_root=tmp, fetch_image=fetch_image, fetch_video=fetch_video,
        ts=1720000500,
    ))
    assert len(rec["assets"]) == 1, rec["assets"]
    asset = rec["assets"][0]
    assert asset["kind"] == "video", asset
    assert asset["path"].endswith(".mp4"), asset["path"]
    assert asset["bytes"] == len(mp4), asset
    base = record_store.record_dir(tmp, rec["record_id"])
    blob_path = os.path.join(base, asset["path"].replace("/", os.sep))
    assert open(blob_path, "rb").read() == mp4
    assert rec["missing"] == [
        {"node": 0, "seg": 2, "kind": "video", "reason": "no_url"}
    ], rec["missing"]

    # 详情页:视频要能拿到资产(页面才有得播),缺的那段带 reason
    detail = record_admin.record_detail(tmp, rec["record_id"])
    vseg = detail["nodes"][0]["segments"][1]
    assert vseg["type"] == "video" and vseg["asset"] == asset["path"], vseg
    assert vseg["bytes"] == len(mp4), vseg
    mseg = detail["nodes"][0]["segments"][2]
    assert mseg["missing"] is True and mseg["reason"] == "no_url", mseg

    # 重建转发:视频用 base64:// 一起发(图片同一条链路)
    payloads = record_admin.node_payloads(rec, base)
    content = payloads[0]["data"]["content"]
    vids = [c for c in content if c["type"] == "video"]
    assert len(vids) == 1, content
    assert vids[0]["data"]["file"].startswith("base64://"), vids[0]
    assert any(c["type"] == "text" and c["data"]["text"] == "[视频]"
               for c in content), content

    # AstrBot 组件重建:Video 组件指向落盘文件
    comps = record_store.build_nodes(rec, base)
    kinds = [type(c).__name__ for c in comps[0].content]
    assert "Video" in kinds, kinds
    print("  OK -> 视频落 mp4 / 详情可播 / 重建带 base64 + 占位")


def test_record_capture_without_video_fetcher_skips():
    import asyncio
    banner("record: 没给 fetch_video 时视频段静默跳过(旧行为,不进 missing)")
    tmp = tempfile.mkdtemp(prefix="wd_record_novideo_")

    async def fetch_image(data):
        return b"\x89PNG\r\n\x1a\n"

    rec = asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "video", "data": {"url": "https://mm/a.mp4"}}]}],
        platform="aiocqhttp", group_id=1, msg_id="nv",
        storage_root=tmp, fetch_image=fetch_image, ts=1720000600,
    ))
    assert rec["assets"] == [] and rec["missing"] == [], rec
    assert rec["nodes"][0]["segments"][0]["data"]["url"] == "https://mm/a.mp4"
    print("  OK -> 视频段保留原始 JSON,不记缺失")


def test_record_list_records_order_and_bad_dirs():
    import asyncio
    banner("record: list_records 按时间倒序, load_record 对坏目录返回 None")
    tmp = tempfile.mkdtemp(prefix="wd_record_")

    async def fetch(data):
        return b"\xff\xd8" + b"1" * 4

    for ts, mid in ((1720000100, "a"), (1720000200, "b")):
        asyncio.run(record_store.capture(
            [{"sender": {"user_id": 1, "nickname": "n"}, "time": ts,
              "message": [{"type": "image",
                          "data": {"url": "http://e/%s.jpg" % mid}}]}],
            platform="aiocqhttp", group_id=1, msg_id=mid,
            storage_root=tmp, fetch_image=fetch, ts=ts,
        ))
    rows = record_store.list_records(tmp, 10)
    assert [r["root_msg_id"] for r in rows] == ["b", "a"], rows
    assert rows[0]["asset_count"] == 1
    assert record_store.list_records(tmp, 1)[0]["root_msg_id"] == "b"
    assert record_store.load_record(tmp, "nope") is None
    os.makedirs(record_store.record_dir(tmp, "broken"), exist_ok=True)
    assert record_store.load_record(tmp, "broken") is None
    print("  OK")


def test_record_image_ref_rules():
    banner("record: image_ref 只认 http(s) 的 url / file")
    assert record_store.image_ref({"url": "https://x/1.jpg"}) == "https://x/1.jpg"
    assert record_store.image_ref({"file": "http://x/2.jpg"}) == "http://x/2.jpg"
    assert record_store.image_ref({"file": "abc.jpg"}) is None
    assert record_store.image_ref({"url": "base64://AAA"}) is None
    assert record_store.image_ref({}) is None
    print("  OK")

def test_plugin_record_from_quoted_forward():
    banner("plugin: 引用一条合并转发 -> /warden 存档 用 reply id 取节点")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_record_quote_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    nodes = [{"sender": {"user_id": 2510536451, "nickname": "風見かずき"},
              "time": 1718600000,
              "message": [{"type": "text", "data": {"text": "看图"}},
                          {"type": "image", "data": {"url": "https://x/a.jpg"}}]}]
    calls = {}

    class _Bot:
        async def call_action(self, action, **kw):
            calls.setdefault(action, []).append(kw)
            if action == "get_forward_msg":
                if kw.get("message_id") == "1481691615":
                    return {"messages": nodes}
                raise RuntimeError("unknown forward " + str(kw.get("message_id")))
            raise RuntimeError("unexpected " + action)

    async def fake_fetch(data):
        return b"\x89PNG\r\n\x1a\n" + b"9" * 6

    pl._record_fetch_image = fake_fetch
    reply_seg = {"type": "reply", "data": {"id": "1481691615"}}
    text_seg = {"type": "text", "data": {"text": "/warden 存档"}}
    ev = _fake_event(group_id="961583271", user_id="2510536451",
                     message_id="m-cmd", message=[reply_seg, text_seg],
                     raw_message={"message": [reply_seg, text_seg]})
    ev.bot = _Bot()
    out = _drive_command(pl, ev, pl.cmd_warden_record)[0]

    assert "已存档" in out.text, out.text
    assert calls.get("get_forward_msg") == [{"message_id": "1481691615"}], calls
    rows = record_store.list_records(tmp, 5)
    assert rows and rows[0]["node_count"] == 1, rows
    assert rows[0]["asset_count"] == 1, rows
    # 原消息 id 必须是那条转发自己的 id（重发 原地 要用），不是命令消息的 id
    assert rows[0]["root_msg_id"] == "1481691615", rows
    print("  OK ->", out.text.splitlines()[0])


def test_plugin_record_via_quoted_message_with_forward_seg():
    banner("plugin: 引用普通消息(内含 forward 段) -> get_msg 找到转发再取")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_record_getmsg_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    nodes = [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
              "message": [{"type": "text", "data": {"text": "hi"}}]}]
    seen = []

    class _Bot:
        async def call_action(self, action, **kw):
            seen.append((action, kw.get("message_id")))
            if action == "get_forward_msg":
                if kw.get("message_id") == "FWD-1":
                    return {"messages": nodes}
                raise RuntimeError("not a forward")
            if action == "get_msg":
                return {"message": [{"type": "forward",
                                    "data": {"id": "FWD-1"}}]}
            raise RuntimeError("unexpected " + action)

    async def fake_fetch(data):
        return b"x"

    pl._record_fetch_image = fake_fetch
    reply_seg = {"type": "reply", "data": {"id": "m-quoted"}}
    text_seg = {"type": "text", "data": {"text": "/warden 存档"}}
    ev = _fake_event(message=[reply_seg, text_seg],
                     raw_message={"message": [reply_seg, text_seg]})
    ev.bot = _Bot()
    out = _drive_command(pl, ev, pl.cmd_warden_record)[0]

    assert "已存档" in out.text, out.text
    assert ("get_msg", "m-quoted") in seen, seen
    assert ("get_forward_msg", "FWD-1") in seen, seen
    assert record_store.list_records(tmp, 5)[0]["node_count"] == 1
    print("  OK ->", out.text.splitlines()[0])


def test_plugin_record_hint_without_forward_or_reply():
    banner("plugin: 既没转发也没引用时 /warden 存档 给出可读提示")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_record_hint_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())
    text_seg = {"type": "text", "data": {"text": "/warden 存档"}}
    ev = _fake_event(message=[text_seg], raw_message={"message": [text_seg]})
    out = _drive_command(pl, ev, pl.cmd_warden_record)[0]
    assert "引用" in out.text, out.text
    assert not record_store.list_records(tmp, 5), "不该落任何存档"
    print("  OK ->", out.text)

def test_plugin_resend_native_forward():
    banner("plugin: /warden 重发 原地 -> forward_group_single_msg 用原消息 id")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_resend_native_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    rec = asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "hi"}}]}],
        platform="aiocqhttp", group_id="961583271", msg_id="1481691615",
        storage_root=tmp, fetch_image=lambda d: None, ts=1720000500,
    ))
    calls = []

    class _Bot:
        async def call_action(self, action, **kw):
            calls.append((action, kw))
            if action == "get_msg":
                # 原消息就是那条合并转发（forward 段的 id 才是真正要转发的目标）
                return {"message": [{"type": "forward",
                                     "data": {"id": "7701368493799210604"}}]}
            return None

    ev = _fake_event(group_id="961583271", user_id="2510536451")
    ev.bot = _Bot()
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_resend(e, "原地"))[0]

    # forward_group_single_msg 只认 OneBot 短 message_id, 段里的长 msgId 会
    # 回「无法找到消息」—— 所以转发的就是 root_msg_id 本身
    assert calls == [
        ("get_msg", {"message_id": 1481691615}),
        ("forward_group_single_msg",
         {"group_id": "961583271", "message_id": 1481691615}),
    ], calls
    assert "原地转发" in out.text, out.text
    assert not os.path.exists(os.path.join(tmp, "_records", rec["record_id"],
                                          "assets", "nope")), "不该写奇怪文件"
    print("  OK ->", out.text)


def test_plugin_resend_native_refuses_non_forward_target():
    banner("plugin: 老存档(root_msg_id 指向命令消息) -> 拒绝原地转发并说明原因")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_resend_stale_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "hi"}}]}],
        platform="aiocqhttp", group_id="g1", msg_id="1191941527",
        storage_root=tmp, fetch_image=lambda d: None, ts=1720000700,
    ))
    calls = []

    class _Bot:
        async def call_action(self, action, **kw):
            calls.append((action, kw))
            if action == "get_msg":
                # 这个 id 是一条普通文字消息(就是当时那条 /warden 存档)
                return {"message": [{"type": "text",
                                     "data": {"text": "/warden 存档"}}]}
            return None

    ev = _fake_event(group_id="g1", user_id="u1")
    ev.bot = _Bot()
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_resend(e, "原地"))[0]
    assert "不是合并转发" in out.text, out.text
    assert [c[0] for c in calls] == ["get_msg"], calls
    print("  OK ->", out.text)


def test_plugin_resend_native_reports_failure():
    banner("plugin: 原地转发失败 -> 错误原样回给用户")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_resend_native_err_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "hi"}}]}],
        platform="aiocqhttp", group_id="g1", msg_id="777",
        storage_root=tmp, fetch_image=lambda d: None, ts=1720000600,
    ))

    class _Bot:
        async def call_action(self, action, **kw):
            if action == "get_msg":
                return {"message": [{"type": "forward", "data": {"id": "777"}}]}
            raise RuntimeError("消息不存在")

    ev = _fake_event(group_id="g1", user_id="u1")
    ev.bot = _Bot()
    out = _drive_command(pl, ev, lambda e: pl.cmd_warden_resend(e, "native"))[0]
    assert "原地转发失败" in out.text and "消息不存在" in out.text, out.text
    print("  OK ->", out.text)


def test_plugin_resend_defaults_to_native_then_rebuild():
    banner("plugin: /warden 重发 默认先原地, 原消息 id 缺失时回退重建")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_resend_auto_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    asyncio.run(pl.initialize())

    # 无短 id 的存档（嵌套转发只能拿到长 msgId）: 原地不可用
    asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "hi"}}]}],
        platform="aiocqhttp", group_id="g1", msg_id="",
        storage_root=tmp, fetch_image=lambda d: None, ts=1720000800,
    ))
    ev = _fake_event(group_id="g1", user_id="u1")
    outs = _drive_command(pl, ev, lambda e: pl.cmd_warden_resend(e))
    texts = [getattr(o, "text", o) for o in outs]
    assert any("改用存档重建" in str(x) for x in texts), texts
    assert isinstance(texts[-1], list), texts[-1]  # 最后一次是 chain_result([Nodes])
    print("  OK ->", str(texts[0])[:60])


def test_record_admin_scan_stats_detail():
    banner("record_admin: 列表/搜索/汇总/详情/取图/删除")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_admin_")
    nodes1 = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1718600000,
               "message": [{"type": "text", "data": {"text": "看图"}},
                           {"type": "image", "data": {"url": "https://x/a.png"}}]}]
    nodes2 = [{"sender": {"user_id": 8, "nickname": "小八"}, "time": 1718600100,
               "message": [{"type": "text", "data": {"text": "你好"}}]}]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 8
    rid1 = record_store.make_record_id("g111", "1481691615", ts=1720000000)
    rid2 = record_store.make_record_id("g222", "m2", ts=1720000900)
    async def fetch_png(_data):
        return png

    async def fetch_none(_data):
        return None

    asyncio.run(record_store.capture(
        nodes1, platform="aiocqhttp", group_id="g111", msg_id="1481691615",
        storage_root=tmp, fetch_image=fetch_png, ts=1720000000, record_id=rid1))
    asyncio.run(record_store.capture(
        nodes2, platform="aiocqhttp", group_id="g222", msg_id="m2",
        storage_root=tmp, fetch_image=fetch_none, ts=1720000900, record_id=rid2))

    allrows = record_admin.scan_records(tmp)
    assert allrows["total"] == 2, allrows
    assert [r["record_id"] for r in allrows["items"]] == [rid2, rid1], allrows["items"]
    assert allrows["items"][1]["asset_count"] == 1, allrows["items"][1]
    assert allrows["items"][1]["can_native"] is True
    assert record_admin.scan_records(tmp, q="看图")["total"] == 1
    assert record_admin.scan_records(tmp, q="阿七")["total"] == 1
    assert record_admin.scan_records(tmp, q="g222")["total"] == 1
    assert record_admin.scan_records(tmp, q="不存在的词")["total"] == 0
    assert record_admin.scan_records(tmp, group="g111")["total"] == 1
    assert record_admin.scan_records(tmp, group="g111")["root"].endswith("_records")
    assert len(record_admin.scan_records(tmp, limit=1)["items"]) == 1
    assert record_admin.scan_records(tmp, offset=1)["items"][0]["record_id"] == rid1

    st = record_admin.stats(tmp)
    assert st["records"] == 2 and st["nodes"] == 2, st
    assert st["assets"] == 1 and st["missing"] == 0, st
    # v1.17.0: 占用包含资产 + 渲染图 + 结构化 json(record.json 等),不再只数 assets
    assert st["bytes"] > len(png), st
    expect = 0
    for name in os.listdir(record_store.records_root(tmp)):
        rec = record_store.load_record(tmp, name)
        if rec:
            expect += record_admin._entry_bytes(tmp, name, rec)
    assert st["bytes"] == expect, (st["bytes"], expect)
    assert st["groups"] == {"g111": 1, "g222": 1}, st

    d = record_admin.record_detail(tmp, rid1)
    assert d["node_count"] == 1 and len(d["nodes"]) == 1, d
    assert d["can_native"] is True, d
    assert record_admin.record_detail(tmp, rid2)["can_native"] is False
    segs = d["nodes"][0]["segments"]
    assert [s["type"] for s in segs] == ["text", "image"], segs
    assert segs[0]["text"] == "看图"
    assert segs[1]["asset"].startswith("assets/"), segs[1]
    assert segs[1]["sha16"], segs[1]
    full = record_admin.resolve_asset(tmp, rid1, segs[1]["asset"])
    assert full and os.path.isfile(full), full
    assert record_admin.resolve_asset(tmp, rid1, "record.json") is None
    assert record_admin.resolve_asset(tmp, rid1, "../record.json") is None
    assert record_admin.resolve_asset(tmp, rid1, segs[1]["asset"] + "x") is None
    assert record_admin.record_detail(tmp, "nope") is None

    gone = record_admin.delete_record(tmp, rid1)
    assert gone["deleted"] is True, gone
    assert record_store.load_record(tmp, rid1) is None
    assert record_admin.scan_records(tmp)["total"] == 1
    assert record_admin.delete_record(tmp, rid1)["deleted"] is False
    assert record_admin.delete_record(tmp, "../evil")["deleted"] is False
    print("  OK ->", "搜索/汇总/取图/删除均通过")


def _install_web_stub(*, query=None, body=None):
    web = types.ModuleType("astrbot.api.web")

    class _Q:
        def __init__(self, d):
            self._d = dict(d or {})

        def get(self, key, default=None, cast=None):
            v = self._d.get(key, default)
            if cast is not None and v is not None:
                try:
                    return cast(v)
                except Exception:
                    return default
            return v

    class _Req:
        def __init__(self):
            self.query = _Q(query)
            self.username = "tester"
            self._body = dict(body or {})

        async def json(self, default=None):
            return self._body if self._body else (default if default is not None else {})

    web.request = _Req()

    def file_response(path, **kw):
        return {"__file__": path, "filename": kw.get("filename")}

    web.file_response = file_response
    sys.modules["astrbot.api.web"] = web
    return web


def test_record_page_routes_and_handlers():
    banner("record_page: 路由注册 + 处理函数")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_page_")
    nodes = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1,
              "message": [{"type": "text", "data": {"text": "看图"}},
                          {"type": "image", "data": {"url": "https://x/a.png"}}]}]
    rid = record_store.make_record_id("g1", "m1", ts=1720000000)
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 4

    async def fetch_png(_data):
        return png

    asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="m1",
        storage_root=tmp, fetch_image=fetch_png,
        ts=1720000000, record_id=rid))

    class _Ctx:
        def __init__(self):
            self.routes = []

        def register_web_api(self, route, handler, methods=None, desc=""):
            self.routes.append((route, handler, tuple(methods or ())))

    class _Pl:
        def __init__(self):
            self.context = _Ctx()

        def _records_root(self):
            return tmp

    pl = _Pl()
    api = record_page.RecordPageApi(pl)
    assert api.register_routes() is True
    routes = {r[0]: r[2] for r in pl.context.routes}
    assert len(routes) == 13, routes
    assert record_page.PAGE_PREFIX + "/stats" in routes, routes
    assert routes[record_page.PAGE_PREFIX + "/render"] == ("POST",), routes
    assert record_page.PAGE_PREFIX + "/thumb" in routes, routes
    assert record_page.PAGE_PREFIX + "/groups" in routes, routes
    assert routes[record_page.PAGE_PREFIX + "/resend"] == ("POST",), routes
    assert record_page.PAGE_PREFIX + "/reveal" in routes, routes
    assert record_page.PAGE_PREFIX + "/style" in routes, routes
    assert routes[record_page.PAGE_PREFIX + "/style_save"] == ("POST",), routes

    class _NoReg:
        pass

    class _PlNoReg:
        def __init__(self):
            self.context = _NoReg()

        def _records_root(self):
            return tmp

    assert record_page.RecordPageApi(_PlNoReg()).register_routes() is False

    saved = sys.modules.pop("astrbot.api.web", None)
    try:
        _install_web_stub()
        assert asyncio.run(api.get_stats())["data"]["records"] == 1
        _install_web_stub(query={"q": "看图"})
        lst = asyncio.run(api.list_records())
        assert lst["status"] == "ok" and lst["data"]["total"] == 1, lst
        _install_web_stub(query={"q": "没有这个词"})
        assert asyncio.run(api.list_records())["data"]["total"] == 0

        _install_web_stub(query={"id": rid})
        detail = asyncio.run(api.get_record())
        assert detail["status"] == "ok" and detail["data"]["nodes"], detail
        asset = detail["data"]["nodes"][0]["segments"][1]["asset"]
        _install_web_stub(query={"id": "nope"})
        assert asyncio.run(api.get_record())["status"] == "error"

        _install_web_stub(query={"id": rid, "path": asset})
        fr = asyncio.run(api.get_asset())
        assert isinstance(fr, dict) and fr.get("__file__"), fr
        _install_web_stub(query={"id": rid, "path": "record.json"})
        assert asyncio.run(api.get_asset())["status"] == "error"

        _install_web_stub(query={"id": rid, "path": asset})
        thumb = asyncio.run(api.get_thumb())
        assert thumb["status"] == "ok", thumb
        assert thumb["data"]["data_url"].startswith("data:image/png;base64,"), thumb
        assert thumb["data"]["bytes"] == len(png), thumb
        _install_web_stub(query={"id": rid, "path": "../record.json"})
        assert asyncio.run(api.get_thumb())["status"] == "error"
        _install_web_stub(query={"id": "nope", "path": asset})
        assert asyncio.run(api.get_thumb())["status"] == "error"

        calls = []

        async def fake_native(bot, group_id, message_id):
            calls.append(("native", group_id, message_id))
            return {"ok": True, "mode": "native"}

        async def fake_rebuild(bot, group_id, rec, base):
            calls.append(("rebuild", group_id, rec.get("record_id")))
            return {"ok": True, "mode": "rebuild"}

        async def fail_native(bot, group_id, message_id):
            return {"ok": False, "error": "消息不存在"}

        orig_native = record_admin.native_forward
        orig_rebuild = record_admin.rebuild_forward
        record_admin.native_forward = fake_native
        record_admin.rebuild_forward = fake_rebuild
        try:
            _install_web_stub(body={"id": rid, "mode": "native"})
            r = asyncio.run(api.resend())
            assert r["status"] == "ok" and r["data"]["ok"] is True, r
            assert calls == [("native", "g1", "m1")], calls

            record_admin.native_forward = fail_native
            _install_web_stub(body={"id": rid, "mode": "native", "fallback": True})
            r2 = asyncio.run(api.resend())
            assert r2["data"]["ok"] is True, r2
            assert "已改用重建转发" in r2["data"].get("note", ""), r2
            assert calls[-1] == ("rebuild", "g1", rid), calls

            _install_web_stub(body={"id": rid, "mode": "native", "fallback": False})
            r3 = asyncio.run(api.resend())
            assert r3["data"]["ok"] is False, r3

            _install_web_stub(body={"id": rid, "mode": "rebuild"})
            r4 = asyncio.run(api.resend())
            assert r4["data"]["mode"] == "rebuild", r4

            _install_web_stub(body={"id": "nope"})
            assert asyncio.run(api.resend())["status"] == "error"
            _install_web_stub(body={})
            assert asyncio.run(api.resend())["status"] == "error"
        finally:
            record_admin.native_forward = orig_native
            record_admin.rebuild_forward = orig_rebuild

        _install_web_stub(body={"id": rid})
        d = asyncio.run(api.delete())
        assert d["status"] == "ok" and d["data"]["deleted"] is True, d
        assert record_store.load_record(tmp, rid) is None
        _install_web_stub(body={"id": rid})
        assert asyncio.run(api.delete())["status"] == "error"
        _install_web_stub(body={})
        assert asyncio.run(api.delete())["status"] == "error"
    finally:
        sys.modules.pop("astrbot.api.web", None)
        if saved is not None:
            sys.modules["astrbot.api.web"] = saved
    print("  OK ->", "13 条路由 + stats/records/record/asset/thumb/groups/resend/render/delete/reveal/style*")


def test_plugin_initialize_registers_page_api():
    banner("plugin: initialize() 注册存档页面 API")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_page_init_")
    pl = _build_plugin_for_command(tmp, enable_index=False, log_to_stdout=False)
    seen = []
    pl.context.register_web_api = (
        lambda route, handler, methods=None, desc="": seen.append(route))
    asyncio.run(pl.initialize())
    assert pl._page_api is not None, "page api 应已注册"
    assert len(seen) == 13, seen
    assert record_page.PAGE_PREFIX + "/stats" in seen, seen

    tmp2 = tempfile.mkdtemp(prefix="warden_page_init_none_")
    pl2 = _build_plugin_for_command(tmp2, enable_index=False, log_to_stdout=False)
    asyncio.run(pl2.initialize())
    assert pl2._page_api is None, "宿主没有 register_web_api 时应安静跳过"
    print("  OK ->", "14 条路由 / 无宿主支持时跳过")


PHASE7 = [
    # v1.12 原型: 聊天记录存档(保存再发送)
    test_record_capture_roundtrip,
    test_record_capture_dedupes_identical_bytes,
    test_record_capture_logs_download_failure,
    test_record_capture_video_asset,
    test_record_capture_without_video_fetcher_skips,
    test_record_list_records_order_and_bad_dirs,
    test_record_image_ref_rules,
    test_plugin_record_from_quoted_forward,
    test_plugin_record_via_quoted_message_with_forward_seg,
    test_plugin_record_hint_without_forward_or_reply,
    test_plugin_resend_native_forward,
    test_plugin_resend_native_refuses_non_forward_target,
    test_plugin_resend_native_reports_failure,
    test_plugin_resend_defaults_to_native_then_rebuild,
]


PHASE8 = [
    # v1.13 存档后台: 浏览/搜索/删除 + 插件页面 API
    test_record_admin_scan_stats_detail,
    test_record_page_routes_and_handlers,
    test_plugin_initialize_registers_page_api,
]

# =========================================================
# Phase 9 测试: v1.14.0 类型门控 + 纯文本条目
# =========================================================


def test_policy_media_kind_and_type_flags():
    import warden.policy as policy
    banner("policy: media_kind / kind_allowed / 文档白名单 / 动图判定")
    mk = policy.media_kind
    assert mk(Component(kind="image", url="https://x/a.png?x=1")) == "image"
    assert mk(Component(kind="image", url="https://x/a.GIF")) == "gif"
    assert mk(Component(kind="image", file_id="abc.apng")) == "gif"
    assert mk(Component(kind="video", url="https://x/a.mp4")) == "video"
    assert mk(Component(kind="file", name="a.pdf")) == "file"
    assert mk(Component(kind="voice", url="https://x/a.amr")) == "voice"

    assert policy.comp_is_animated(Component(kind="image", file_id="x.webp")) is True
    assert policy.comp_is_animated(Component(kind="image", url="https://x/x.png")) is False
    gif = b"GIF89a" + b"\x00\x21\xf9\x04" * 2 + b"\x2c" * 2
    assert policy.blob_is_animated(gif) is True
    assert policy.blob_is_animated(b"RIFF\x00\x00\x00\x00WEBPVP8 ANIM") is True
    assert policy.blob_is_animated(b"\x89PNG\r\n\x1a\n" + b"0" * 8 + b"acTL") is True
    assert policy.blob_is_animated(b"\x89PNG\r\n\x1a\n" + b"0" * 8) is False
    assert policy.blob_is_animated(b"GIF89a" + b"\x21\xf9\x04" * 1) is False

    assert policy.kind_allowed(MediaWardenConfig(), "image") is True
    off = MediaWardenConfig(save_images=False)
    assert policy.kind_allowed(off, "image") is False
    assert policy.kind_allowed(off, "gif") is True
    assert policy.kind_allowed(MediaWardenConfig(save_texts=False), "text") is False
    assert policy.kind_allowed(MediaWardenConfig(save_texts=True), "text") is True
    assert policy.kind_allowed(MediaWardenConfig(save_forwards=False), "forward") is False
    # v1.17.0: 语音以前不在开关表里 → 未知类型放行,六类全关也照存
    assert policy.kind_allowed(MediaWardenConfig(), "voice") is False
    assert policy.kind_allowed(MediaWardenConfig(save_voice=True), "voice") is True
    assert policy.kind_allowed(MediaWardenConfig(), "hologram") is False

    assert MediaWardenConfig().doc_ext_allowed("PDF") is True
    assert MediaWardenConfig().doc_ext_allowed(".docx") is True
    assert MediaWardenConfig().doc_ext_allowed("exe") is False
    assert MediaWardenConfig(doc_ext_whitelist=[]).doc_ext_allowed("exe") is True
    print("  OK -> image/gif/video/file/voice 分类, 六类开关, 白名单空=不限")


def test_record_text_entry_roundtrip():
    import asyncio
    banner("record: capture_text 落 entry.json + save_render + list_records 混排")
    tmp = tempfile.mkdtemp(prefix="wd_text_")
    segs = [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "at", "data": {"qq": "206216334"}},
        {"type": "face", "data": {"id": "14"}},
    ]
    entry = asyncio.run(record_store.capture_text(
        segs, platform="aiocqhttp", group_id="961583271", msg_id="m1",
        sender_id="2510536451", sender_name="卡兹", storage_root=tmp,
        ts=1720000000,
    ))
    rid = entry["record_id"]
    loaded = record_store.load_entry(tmp, rid)
    assert loaded is not None
    assert loaded["kind"] == "text"
    assert loaded["segments"] == segs
    assert loaded["sender_name"] == "卡兹"
    assert record_store.has_render(tmp, rid) is None

    name = record_store.save_render(tmp, rid, b"\x89PNG\r\n\x1a\nxx")
    assert name == "render.png"
    assert record_store.has_render(tmp, rid) == "render.png"
    assert record_store.load_entry(tmp, rid)["render"] == "render.png"
    # 没有 entry.json 的目录:不写、不炸
    assert record_store.save_render(tmp, "nope", b"x") is None

    # 混排:聊天记录 + 纯文本都能出现在同一列表里
    asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "a"}, "time": 1720000100,
          "message": [{"type": "text", "data": {"text": "x"}}]}],
        platform="aiocqhttp", group_id="961583271", msg_id="m2",
        storage_root=tmp, fetch_image=_noop_fetch_image,
        ts=1720000100,
    ))
    rows = record_store.list_records(tmp)
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["forward", "text"], rows
    print("  OK -> entry.json/render.png/混排列表都通")


async def _noop_fetch_image(data):
    return None


def test_plugin_e2e_text_message_saved_as_entry():
    import asyncio
    banner("plugin: 纯文本 -> _records 条目(entry.json + render.png)")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td,
                "match_mode": "blacklist",
                "save_texts": True,
                "enable_index_db": True,
                "reply_preview": False,
            },
        )
        asyncio.run(inst.initialize())
        raw = {"message": [
            {"type": "text", "data": {"text": "你好呀"}},
            {"type": "at", "data": {"qq": "206216334"}},
        ]}
        ev = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=list(raw["message"]), raw_message=raw,
            message_str="你好呀", message_id="mtext1", timestamp=1718600000,
        )
        out = _drive_command(inst, ev, inst.on_group_message)
        assert len(out) == 1, out
        text = out[0].text if hasattr(out[0], "text") else str(out[0])
        assert "✅" in text, text

        rows = record_store.list_records(td)
        assert len(rows) == 1, rows
        rid = rows[0]["record_id"]
        assert rows[0]["kind"] == "text"
        entry = record_store.load_entry(td, rid)
        assert entry["segments"] == raw["message"], entry["segments"]
        rdir = record_store.record_dir(td, rid)
        assert os.path.isfile(os.path.join(rdir, "entry.json"))
        has_png = os.path.isfile(os.path.join(rdir, "render.png"))
        if inst._forwarder is None:
            print("  SKIP render -> PIL unavailable")
        else:
            assert has_png, os.listdir(rdir)
            assert entry["render"] == "render.png"
        # v1.17.0: 纯文本不再往**扁平**索引写行 —— 条目本来就在 _records/ 里,
        # 页面按条目列它;写了会以「磁盘文件」出现在页面上,而 resolve_flat 拒读
        # _records/,点不开也删不掉。这里断言索引保持 0 行。
        if inst._index is not None:
            try:
                assert inst._index.count() == 0, inst._index.stats()
            finally:
                inst._index.close()
                inst._index = None
        print("  OK ->", text.replace(chr(10), " | ")[:70],
              "| render=" + ("on" if has_png else "off"))


def test_plugin_type_gating_drops_disabled_and_bad_ext():
    import asyncio
    banner("plugin: 类型开关 / 文档白名单 在下载之前就拦掉")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td,
                "match_mode": "blacklist",
                "save_images": False,
                "save_files": True,
                "doc_ext_whitelist": ["pdf"],
                "enable_index_db": False,
                "reply_preview": False,
            },
        )
        asyncio.run(inst.initialize())

        # 1) 图片关掉 -> 什么都不存, 也就不应该有回执
        ev_img = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "image", "data": {"file": "a.jpg",
                                                 "url": "https://x/a.png"}}],
            message_id="mimg", timestamp=1718600000,
        )
        out = _drive_command(inst, ev_img, inst.on_group_message)
        assert out == [], out

        # 2) 文档扩展名不在白名单 -> 也不存
        ev_exe = _fake_event(
            group_id="g1", user_id="u1", nickname="alice",
            message=[{"type": "file", "data": {"file": "setup.exe",
                                               "name": "setup.exe",
                                               "url": "https://x/setup.exe"}}],
            message_id="mexe", timestamp=1718600001,
        )
        out = _drive_command(inst, ev_exe, inst.on_group_message)
        assert out == [], out

        # 3) 白名单内的 pdf -> 正常落盘(下载打桩, 离线可跑)
        orig_download = plugin_main.download

        async def _fake_download(comp, *a, **k):
            return Downloaded(data=b"%PDF-1.4 offline", mime="application/pdf",
                              size=16)

        plugin_main.download = _fake_download
        try:
            ev_pdf = _fake_event(
                group_id="g1", user_id="u1", nickname="alice",
                message=[{"type": "file", "data": {"file": "doc.pdf",
                                                   "name": "doc.pdf",
                                                   "url": "https://x/doc.pdf"}}],
                message_id="mpdf", timestamp=1718600002,
            )
            out = _drive_command(inst, ev_pdf, inst.on_group_message)
        finally:
            plugin_main.download = orig_download
        assert len(out) == 1, out
        text = out[0].text if hasattr(out[0], "text") else str(out[0])
        assert "✅" in text and "file" in text, text
        pdfs = []
        for root, _, fs in os.walk(td):
            pdfs += [os.path.join(root, f) for f in fs if f.endswith(".pdf")]
        assert pdfs, os.listdir(td)
        print("  OK -> image off=false, exe dropped, pdf saved:", os.path.basename(pdfs[0]))


def test_plugin_forward_saved_as_single_entry():
    import asyncio
    banner("plugin: 聊天记录 -> 一个条目(record.json + render.png + assets)")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td,
                "match_mode": "blacklist",
                "forward_render_image": True,
                "enable_user_avatar": False,
                "enable_index_db": False,
                "reply_preview": False,
            },
        )
        asyncio.run(inst.initialize())
        if inst._forwarder is None:
            print("  SKIP -> PIL unavailable")
            return
        nodes = [
            {"sender": {"user_id": 206216334, "nickname": "橘雪莉"},
             "time": 1718600000,
             "message": [
                 {"type": "text", "data": {"text": "看图"}},
                 {"type": "image", "data": {"url": "https://x/a.png"}},
             ]},
        ]
        orig_download = plugin_main.download

        async def _fake_download(comp, *a, **k):
            return Downloaded(data=b"\x89PNG\r\n\x1a\n" + b"0" * 8,
                              mime="image/png", size=16)

        plugin_main.download = _fake_download
        try:
            ctx = SaveContext(platform="aiocqhttp", group_id="961583271",
                              sender_id="2510536451", sender_name="卡兹",
                              msg_id="mfwd1", idx=0, ts=1718600000)
            comp = Component(kind="forward", meta={"nodes": nodes})
            results = asyncio.run(inst._save_forward(comp, ctx, None))
        finally:
            plugin_main.download = orig_download

        # v1.16.0: 条目里 record.json 与 render.png 是同一件事的两种形态,
        # 一条转发只产生一个结果(以前还会多出一条 forward 的扁平 JSON)。
        kinds = sorted(r.kind for r in results if r.ok)
        assert kinds == ["forward_render"], results

        rows = record_store.list_records(td)
        assert len(rows) == 1 and rows[0]["kind"] == "forward", rows
        rid = rows[0]["record_id"]
        rdir = record_store.record_dir(td, rid)
        assert os.path.isfile(os.path.join(rdir, "record.json"))
        assert record_store.has_render(td, rid) == "render.png"
        assert os.path.isdir(os.path.join(rdir, "assets"))
        rec = record_store.load_record(td, rid)
        assert rec["kind"] == "forward" and rec["render"] == "render.png"
        assert rec["node_count"] == 1
        # 节点内图片落进了条目 assets(与渲染共用同一批字节)
        assets = rec.get("assets") or []
        assert assets and all(os.path.isfile(
            os.path.join(rdir, a["path"].replace("/", os.sep))) for a in assets), assets
        # 渲染图只住在条目里:不再多落一份扁平 forward_image_*.png
        flat_renders = [
            os.path.join(dp, fn) for dp, _dn, fns in os.walk(td) for fn in fns
            if "forward_image" in fn
        ]
        assert flat_renders == [], flat_renders
        print("  OK -> 条目", rid, "| assets", len(assets), "| 无扁平副本")


def test_record_admin_categories_and_flat():
    import asyncio
    banner("record_admin: 分类管理(条目 + 扁平文件), 详情/取图/删除")
    tmp = tempfile.mkdtemp(prefix="wd_cat_")
    nodes = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1720000000,
              "message": [{"type": "text", "data": {"text": "聊天记录内容"}}]}]
    fwd_rid = record_store.make_record_id("g1", "m1", ts=1720000000)

    async def _no_png(_data):
        return None

    asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="m1",
        storage_root=tmp, fetch_image=_no_png, ts=1720000000,
        record_id=fwd_rid))
    text_entry = asyncio.run(record_store.capture_text(
        [{"type": "text", "data": {"text": "一句纯文本"}}],
        platform="aiocqhttp", group_id="g2", msg_id="m2",
        sender_id="u2", sender_name="小八", storage_root=tmp,
        ts=1720000002))
    text_rid = text_entry["record_id"]
    assert record_store.save_render(tmp, text_rid, b"\x89PNG\r\n\x1a\n01") == "render.png"

    # 老的扁平落盘文件 + 索引行
    flat_dir = os.path.join(tmp, "g1", "u1")
    os.makedirs(flat_dir, exist_ok=True)
    flat_path = os.path.join(flat_dir, "20260623_181908_image_abc.png")
    with open(flat_path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n02")
    from warden.index import AssetIndex
    index = AssetIndex(db_path=os.path.join(tmp, "_warden.db"))
    index.open()
    index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                 msg_id="m3", idx=0, kind="image", path=flat_path, size=8,
                 sha16="a" * 16)

    rows = index.list_assets(limit=10)["items"]
    assert rows and index.list_assets(kinds=["image"])["total"] == 1
    assert index.list_assets(kinds=["video"])["total"] == 0

    data = record_admin.scan_items(tmp, flat_rows=rows)
    assert data["total"] == 3, data["items"]
    kinds = sorted(i["kind"] for i in data["items"])
    assert kinds == ["forward", "image", "text"], kinds

    only_text = record_admin.scan_items(tmp, kind="text", flat_rows=rows)
    assert only_text["total"] == 1 and only_text["items"][0]["record_id"] == text_rid
    only_flat = record_admin.scan_items(tmp, source="flat", flat_rows=rows)
    assert only_flat["total"] == 1 and only_flat["items"][0]["source"] == "flat"
    by_group = record_admin.scan_items(tmp, group="g1", flat_rows=rows)
    assert by_group["total"] == 2, by_group["items"]
    assert record_admin.scan_items(tmp, q="纯文本", flat_rows=rows)["total"] == 1

    st = record_admin.stats(tmp)
    assert st["records"] == 1 and st["texts"] == 1, st
    assert st["kinds"] == {"forward": 1, "text": 1}, st

    detail = record_admin.record_detail(tmp, text_rid)
    assert detail["kind"] == "text" and detail["render"] == "render.png"
    assert detail["nodes"][0]["segments"][0]["text"] == "一句纯文本"
    assert detail["nodes"][0]["name"] == "小八"

    # 条目内的渲染图可取;扁平的其它文件不许被当条目资产读
    assert record_admin.entry_file(tmp, text_rid, "render.png")
    assert record_admin.entry_file(tmp, text_rid, "entry.json") is None
    flat_rel = os.path.relpath(flat_path, tmp).replace(os.sep, "/")
    assert record_admin.resolve_flat(tmp, flat_rel) == flat_path
    assert record_admin.resolve_flat(tmp, "../escape.png") is None
    assert record_admin.resolve_flat(tmp, "_records/" + text_rid + "/render.png") is None

    res = record_admin.delete_flat(tmp, flat_rel)
    assert res["deleted"] and not os.path.exists(flat_path), res
    assert index.forget_path(flat_path) == 1
    assert record_admin.scan_items(tmp, flat_rows=index.list_assets()["items"])["total"] == 2
    index.close()
    print("  OK -> 3 类混排 / kind+group+q 过滤 / 详情 / 取图白名单 / 删除+索引清理")


def test_plugin_forward_auto_forward_to_target_groups():
    import asyncio
    banner("plugin: 自动转发(重建) -> send_group_forward_msg 逐目标群")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td,
                "match_mode": "blacklist",
                "forward_render_image": False,
                "enable_index_db": False,
                "reply_preview": False,
                "auto_forward_enabled": True,
                "auto_forward_mode": "rebuild",
                "forward_target_groups": ["961583271", "256678737"],
            },
        )
        asyncio.run(inst.initialize())
        calls = []

        class _Bot:
            async def call_action(self, action, **kw):
                calls.append((action, kw))
                return {"status": "ok"}

        inst._qq_bot = lambda: _Bot()
        nodes = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1718600000,
                  "message": [{"type": "text", "data": {"text": "hi"}}]}]
        ctx = SaveContext(platform="aiocqhttp", group_id="999",
                          sender_id="u1", sender_name="alice",
                          msg_id="mauto1", idx=0, ts=1718600000)
        comp = Component(kind="forward", meta={"nodes": nodes})
        results = asyncio.run(inst._save_forward(comp, ctx, None))
        assert any(r.ok for r in results), results
        actions = [c for c in calls if c[0] == "send_group_forward_msg"]
        assert len(actions) == 2, calls
        assert [c[1].get("group_id") for c in actions] == ["961583271", "256678737"]
        assert actions[0][1].get("messages"), actions[0][1]

        # 开关关掉 = 一条也不发
        calls.clear()
        inst.cfg.auto_forward_enabled = False
        asyncio.run(inst._save_forward(comp, ctx, None))
        assert calls == [], calls
        print("  OK -> 2 个目标群各发一次; 关掉开关后 0 次")


def test_plugin_auto_forward_native_first_then_rebuild():
    import asyncio
    banner("plugin: 自动转发先试 native(保装扮),缓存失效才回退重建")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td, "match_mode": "blacklist",
                "forward_render_image": False, "enable_index_db": False,
                "reply_preview": False, "auto_forward_enabled": True,
                "auto_forward_mode": "native",
                "forward_target_groups": ["961583271"],
            },
        )
        asyncio.run(inst.initialize())
        calls = []
        fail_native = [False]

        class _Bot:
            async def call_action(self, action, **kw):
                calls.append(action)
                if action == "get_msg":
                    return {"message": [{"type": "forward",
                                         "data": {"id": "FWD-1"}}]}
                if action == "forward_group_single_msg":
                    if fail_native[0]:
                        raise RuntimeError("消息不存在")
                    return {"status": "ok", "data": {"message_id": 5}}
                return {"status": "ok"}

        inst._qq_bot = lambda: _Bot()
        rec = {"root_msg_id": "1481691615", "nodes": [
            {"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1718600000,
             "message": [{"type": "text", "data": {"text": "hi"}}]}]}
        ctx = SaveContext(platform="aiocqhttp", group_id="999",
                          sender_id="u1", sender_name="alice",
                          msg_id="1481691615", idx=0, ts=1718600000)
        # 缓存里还有原消息 -> 原地转发到别的群也能保装扮
        done = asyncio.run(inst._auto_forward(rec, "rid", ctx))
        assert done == ["961583271(native)"], done
        assert calls == ["get_msg", "forward_group_single_msg"], calls
        # 缓存失效(重启/过期/LRU) -> 回退重建,不重复上报成功
        calls.clear()
        fail_native[0] = True
        done = asyncio.run(inst._auto_forward(rec, "rid", ctx))
        assert done == ["961583271(rebuild)"], done
        assert calls == ["get_msg", "forward_group_single_msg",
                         "send_group_forward_msg"], calls
        print("  OK -> 先 native,失败才 rebuild")


def test_plugin_auto_forward_skips_reused_entry():
    import asyncio
    banner("plugin: 复用旧条目(同内容再发)不再往目标群重复转发")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={
                "storage_root": td, "match_mode": "blacklist",
                "forward_render_image": False, "enable_index_db": False,
                "reply_preview": False, "auto_forward_enabled": True,
                "auto_forward_mode": "native",
                "forward_target_groups": ["961583271"],
            },
        )
        asyncio.run(inst.initialize())
        calls = []

        class _Bot:
            async def call_action(self, action, **kw):
                calls.append(action)
                if action == "get_msg":
                    return {"message": [{"type": "forward",
                                         "data": {"id": "FWD-1"}}]}
                return {"status": "ok"}

        inst._qq_bot = lambda: _Bot()
        nodes = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1718600000,
                  "message": [{"type": "text", "data": {"text": "hi"}}]}]
        ctx = SaveContext(platform="aiocqhttp", group_id="999",
                          sender_id="u1", sender_name="alice",
                          msg_id="1481691615", idx=0, ts=1718600000)
        comp = Component(kind="forward", meta={"nodes": nodes})
        asyncio.run(inst._save_forward(comp, ctx, None))
        assert calls == ["get_msg", "forward_group_single_msg"], calls
        # 同一段内容在源群再发一次:存档复用原条目 -> 目标群不该收到第二条
        calls.clear()
        ctx2 = SaveContext(platform="aiocqhttp", group_id="999",
                           sender_id="u1", sender_name="alice",
                           msg_id="1481691616", idx=0, ts=1718600000)
        asyncio.run(inst._save_forward(comp, ctx2, None))
        assert calls == [], calls
        print("  OK -> 复用条目不再转发,首次仍走 native")


def test_forwarder_apply_style_and_avatar_shape():
    import asyncio, io
    banner("forwarder: apply_style(颜色/圆角/字号/时间格式/头像形状)")
    try:
        from PIL import Image
    except ImportError:
        print("  SKIP -> PIL unavailable")
        return
    from warden.forwarder import Forwarder, _coerce_nodes

    nodes = _coerce_nodes([
        {"sender": {"user_id": 1, "nickname": "阿七"}, "time": 1718600000,
         "message": [{"type": "text", "data": {"text": "样式预览"}}]},
    ])
    fw = Forwarder(width=720, max_nodes=5)
    fw.apply_style(width=540, bg="#000000", bubble="#ffffff", text="#112233",
                   dim="#445566", radius=24, font_size=22,
                   avatar_shape="square", time_format="%m-%d %H:%M")
    assert fw.cfg.width == 540 and fw.cfg.bg == (0, 0, 0), fw.cfg
    assert fw.cfg.bubble_bg == (255, 255, 255)
    assert fw.cfg.text_color == (17, 34, 51)
    assert fw.cfg.time_color == (68, 85, 102)
    assert fw.cfg.bubble_radius == 24 and fw.cfg.font_size == 22
    assert fw.cfg.avatar_shape == "square"
    assert fw.cfg.time_format == "%m-%d %H:%M"
    png = asyncio.run(fw.render(nodes))
    im = Image.open(io.BytesIO(png))
    assert im.width == 540, im.size
    assert im.convert("RGB").getpixel((5, 5)) == (0, 0, 0), "背景色没生效"

    # 深色只替换「还是默认值」的颜色,用户显式挑的色不动
    fw2 = Forwarder(width=720, max_nodes=5)
    fw2.apply_style(bg="#f5f6f7", text="#ff0000", dark=True)
    assert fw2.cfg.bg == (20, 22, 26), fw2.cfg.bg
    assert fw2.cfg.text_color == (255, 0, 0), fw2.cfg.text_color

    # 非法色值不炸,退回默认
    fw3 = Forwarder(width=720, max_nodes=5)
    fw3.apply_style(bg="not-a-color", radius="x", font_size=None)
    assert fw3.cfg.bg == (245, 246, 247), fw3.cfg.bg  # "#f5f6f7" 兜底
    print("  OK -> 宽度/颜色/圆角/字号/形状/时间格式 + 深色只动默认色")


def test_plugin_style_update_and_page():
    import asyncio
    banner("plugin: 样式保存(校验/生效) + 页面 style 接口 + 预览渲染")
    tmp = tempfile.mkdtemp(prefix="wd_style_")
    inst = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
        config={"storage_root": tmp, "match_mode": "blacklist",
                "enable_index_db": False, "reply_preview": False},
    )
    asyncio.run(inst.initialize())
    snap = inst.style_snapshot()
    assert snap["style_width"] == 720 and snap["style_text_color"] == "#1c1f24", snap

    updated, errors, persisted = inst.update_style({
        "style_width": 640, "style_bg_color": "#101010",
        "style_font_size": 20, "style_avatar_shape": "square",
        "style_time_format": "%H:%M",
    })
    assert errors == [], errors
    assert isinstance(persisted, list)
    assert updated["style_width"] == 640 and updated["style_bg_color"] == "#101010"
    assert inst.cfg.style_width == 640
    if inst._forwarder is not None:
        assert inst._forwarder.cfg.width == 640
        assert inst._forwarder.cfg.bg == (16, 16, 16)
        assert inst._forwarder.cfg.avatar_shape == "square"

    # 非法值被拦住,且不污染当前配置
    bad, bad_errors, bad_persisted = inst.update_style({"style_bg_color": "#zzzzzz"})
    assert bad_errors and not bad and not bad_persisted, bad_errors
    assert inst.cfg.style_bg_color == "#101010"

    api = record_page.RecordPageApi(inst)
    saved = sys.modules.pop("astrbot.api.web", None)
    try:
        _install_web_stub()
        got = asyncio.run(api.get_style())
        assert got["status"] == "ok" and got["data"]["style"]["style_width"] == 640, got
        _install_web_stub(body={"values": {"style_radius": 30}})
        res = asyncio.run(api.save_style())
        assert res["status"] == "ok" and res["data"]["style"]["style_radius"] == 30, res
        assert inst.cfg.style_radius == 30
        _install_web_stub(body={"values": {"style_bg_color": "bogus"}})
        assert asyncio.run(api.save_style())["status"] == "error"
        if inst._forwarder is not None:
            _install_web_stub(body={"values": {"style_width": 480}})
            prev = asyncio.run(api.style_preview())
            assert prev["status"] == "ok", prev
            assert prev["data"]["data_url"].startswith("data:image/png;base64,"), prev
        else:
            print("  SKIP preview -> PIL unavailable")
    finally:
        if saved is not None:
            sys.modules["astrbot.api.web"] = saved
    print("  OK -> 保存生效 / 非法值拒绝 / 页面读取与预览均通")


def test_config_reads_nested_schema_groups():
    banner("config: 宿主按 schema 分组嵌套传配置时能读到(以前的静默忽略)")
    cfg, errors = MediaWardenConfig.from_raw_lenient({
        "filter": {"target_groups": ["961583271"], "target_users": ["*"],
                   "match_mode": "whitelist"},
        "storage": {"storage_root": "D:/wd", "dedupe": False},
        "save_types": {"save_texts": True, "save_images": False},
        "render_style": {"style_width": 800, "style_bg_color": "#000000"},
        "logging": {"log_to_stdout": False},
    })
    assert errors == [], errors
    assert cfg.target_groups == ["961583271"], cfg.target_groups
    assert cfg.target_users == ["*"]
    assert cfg.storage_root == "D:/wd" and cfg.dedupe is False
    assert cfg.save_texts is True and cfg.save_images is False
    assert cfg.style_width == 800 and cfg.style_bg_color == "#000000"
    assert cfg.log_to_stdout is False
    # 顶层同名键优先:样式页等直接写顶层的调用方仍然有效
    cfg2, _ = MediaWardenConfig.from_raw_lenient({
        "render_style": {"style_width": 800}, "style_width": 640})
    assert cfg2.style_width == 640, cfg2.style_width
    # 扁平配置(老形态/测试)照旧
    cfg3, _ = MediaWardenConfig.from_raw_lenient(
        {"match_mode": "blacklist", "save_texts": True})
    assert cfg3.match_mode == "blacklist" and cfg3.save_texts is True
    print("  OK -> 嵌套分组摊平 / 顶层优先 / 扁平兼容")


def test_plugin_persist_config_writes_schema_group():
    import asyncio
    banner("plugin: 样式保存写进宿主的 schema 分组(不在顶层留重复键)")
    calls = []

    class FakeCfg(dict):
        def save_config(self, *a, **kw):
            calls.append(1)
            return True

    live = FakeCfg({
        "filter": {"target_groups": ["961583271"], "target_users": ["*"],
                   "match_mode": "whitelist"},
        "render_style": {
            "style_width": 720, "style_bg_color": "#f5f6f7",
            "style_bubble_color": "#ffffff", "style_text_color": "#1c1f24",
            "style_dim_color": "#8a9099", "style_radius": 12,
            "style_font_size": 15, "style_avatar_shape": "circle",
            "style_time_format": "%H:%M", "style_dark": False,
        },
    })
    tmp = tempfile.mkdtemp(prefix="wd_persist_")
    live["storage"] = {"storage_root": tmp, "enable_index_db": False}
    inst = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
        config=live,
    )
    asyncio.run(inst.initialize())
    # 分组带的 whitelist 群号这次必须真的生效(以前读顶层,永远是空)
    assert inst.cfg.target_groups == ["961583271"], inst.cfg.target_groups
    snap, errors, persisted = inst.update_style({"style_width": 800})
    assert errors == [], errors
    assert "style_width" in persisted, persisted
    assert live["render_style"]["style_width"] == 800, live["render_style"]
    assert "style_width" not in live, "不应在顶层留下重复键"
    assert calls, "save_config 没被调用"
    assert inst._forwarder is None or inst._forwarder.cfg.width == 800
    print("  OK -> 写进 render_style 分组并落盘")

def test_record_admin_forward_returns_new_message_id():
    banner("record_admin: 转发结果带回新消息 id（页面/回执可以拿它去撤回或核对）")
    import asyncio
    calls = []

    class _Bot:
        def __init__(self, payload):
            self.payload = payload

        async def call_action(self, action, **kw):
            calls.append((action, kw))
            if action == "get_msg":
                return {"message": [{"type": "forward", "data": {"id": "1"}}]}
            if action == "forward_group_single_msg":
                return self.payload
            if action == "send_group_forward_msg":
                return self.payload
            return None

    ok = asyncio.run(record_admin.native_forward(_Bot({"data": {"message_id": 900001}}),
                                                "961583271", "1481691615"))
    assert ok["ok"] and ok["new_message_id"] == "900001", ok
    assert ok["origin_message_id"] == "1481691615", ok

    tmp = tempfile.mkdtemp(prefix="warden_fwd_id_")
    rec = asyncio.run(record_store.capture(
        [{"sender": {"user_id": 1, "nickname": "n"}, "time": 1,
          "message": [{"type": "text", "data": {"text": "hi"}}]}],
        platform="aiocqhttp", group_id="961583271", msg_id="1481691615",
        storage_root=tmp, fetch_image=lambda d: None, ts=1720000600,
    ))
    base = record_store.record_dir(tmp, rec["record_id"])
    rb = asyncio.run(record_admin.rebuild_forward(
        _Bot({"status": "ok", "data": {"message_id": 900002}}),
        "256678737", rec, base))
    assert rb["ok"] and rb["new_message_id"] == "900002", rb
    assert rb["group_id"] == "256678737" and rb["node_count"] == 1, rb

    # 取不到 id 不算失败，只是回执里没得报
    quiet = asyncio.run(record_admin.rebuild_forward(_Bot(None), "1", rec, base))
    assert quiet["ok"] and quiet["new_message_id"] == "", quiet
    # 动作返回 failed 视为失败（不再静默当成功）
    bad = asyncio.run(record_admin.rebuild_forward(
        _Bot({"status": "failed", "message": "没权限"}), "1", rec, base))
    assert not bad["ok"] and "没权限" in bad["error"], bad
    print("  OK -> native/rebuild 都带回 new_message_id，failed 不再静默")

def test_record_detail_render_and_uin_reliability():
    banner("record_admin: 详情带 has_render / uin_reliable（渲染图能点开、假 uin 不显示）")
    import asyncio
    tmp = tempfile.mkdtemp(prefix="warden_detail_")

    async def _no_png(_data):
        return None

    # 1) NapCat 的「所有节点同一个 user_id」—— 不可信
    same = [
        {"sender": {"user_id": 1094950020, "nickname": "甲"}, "time": 1,
         "message": [{"type": "text", "data": {"text": "a"}}]},
        {"sender": {"user_id": 1094950020, "nickname": "乙"}, "time": 2,
         "message": [{"type": "text", "data": {"text": "b"}}]},
    ]
    rid = record_store.make_record_id("g1", "m1", ts=1720000000)
    rec = asyncio.run(record_store.capture(
        same, platform="aiocqhttp", group_id="g1", msg_id="m1",
        storage_root=tmp, fetch_image=_no_png, ts=1720000000, record_id=rid))
    assert rec["uin_reliable"] is False, rec.get("uin_reliable")
    d = record_admin.record_detail(tmp, rid)
    assert d["uin_reliable"] is False, d["uin_reliable"]
    assert d["has_render"] is False and d["render"] == "", d
    # 详情以前没有 has_render/render —— 列表标了「有渲染图」，点进去却没有切换标签
    assert record_store.save_render(tmp, rid, b"\x89PNG\r\n\x1a\n00") == "render.png"
    d2 = record_admin.record_detail(tmp, rid)
    assert d2["has_render"] is True and d2["render"] == "render.png", d2

    # 2) uin 不同 -> 可信；名字相同、uin 不同也算可信
    diff = [
        {"sender": {"user_id": 11, "nickname": "同名"}, "time": 1,
         "message": [{"type": "text", "data": {"text": "a"}}]},
        {"sender": {"user_id": 22, "nickname": "同名"}, "time": 2,
         "message": [{"type": "text", "data": {"text": "b"}}]},
    ]
    rid2 = record_store.make_record_id("g1", "m2", ts=1720000010)
    rec2 = asyncio.run(record_store.capture(
        diff, platform="aiocqhttp", group_id="g1", msg_id="m2",
        storage_root=tmp, fetch_image=_no_png, ts=1720000010, record_id=rid2))
    assert rec2["uin_reliable"] is True, rec2.get("uin_reliable")
    assert record_admin.record_detail(tmp, rid2)["uin_reliable"] is True

    # 3) 纯文本条目：有渲染图时 has_render=True（单节点 uin 一律可信）
    entry = asyncio.run(record_store.capture_text(
        [{"type": "text", "data": {"text": "一句话"}}],
        platform="aiocqhttp", group_id="g2", msg_id="m3", sender_id="u3",
        sender_name="丙", storage_root=tmp, ts=1720000020))
    etid = entry["record_id"]
    ed = record_admin.record_detail(tmp, etid)
    assert ed["kind"] == "text" and ed["has_render"] is False
    record_store.save_render(tmp, etid, b"\x89PNG\r\n\x1a\n01")
    assert record_admin.record_detail(tmp, etid)["has_render"] is True
    print("  OK -> 同 uin 多昵称=不可信 / 有 render 才给标签 / 纯文本同理")


def test_page_stats_and_kind_filter_use_real_counts():
    banner("record_page: 统计与类型筛选用真实总数（不再是一页的行数）")
    import asyncio
    from warden.index import AssetIndex
    tmp = tempfile.mkdtemp(prefix="warden_counts_")
    base = os.path.join(tmp, "g1", "u1")
    os.makedirs(base, exist_ok=True)
    index = AssetIndex(db_path=os.path.join(tmp, "_warden.db"))
    index.open()
    total_bytes = 0

    def _mk(name, kind, size, ts):
        nonlocal total_bytes
        path = os.path.join(base, name)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        total_bytes += size
        index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m" + name, idx=0, kind=kind, path=path, size=size,
                     sha16="b" * 16)

    # 700 张图（时间较新）+ 60 个转发 JSON（时间最老）: 老实现先取最新 500 行
    # 再在 Python 里筛类型，于是「聊天记录」一条都出不来、「图片」永远 500。
    # v1.18.0 起 kind 由写入方定死（不再按扩展名/文件名猜），这里直接写规范值。
    for i in range(700):
        _mk("i%04d.png" % i, "image", 4, 1720000000 + i)
    for i in range(60):
        _mk("f%04d_forward_json.json" % i, "forward", 8, 1719000000 + i)
    # GIF 行必须是 kind=gif(升级时 upgrade.normalize 会把老 image+.gif 改过来)
    for i in range(3):
        _mk("g%02d.gif" % i, "gif", 4, 1719800000 + i)

    class _Cfg:
        def save_type_flags(self):
            return {"image": True, "gif": False, "video": True,
                    "file": True, "text": False, "forward": True}

    class _Pl:
        def __init__(self):
            self._index = index
            self.cfg = _Cfg()

        def _records_root(self):
            return tmp

    api = record_page.RecordPageApi(_Pl())
    saved = sys.modules.pop("astrbot.api.web", None)
    try:
        _install_web_stub()
        st = asyncio.run(api.get_stats())["data"]
        assert st["flat_total"] == 763, st["flat_total"]
        # 页面靠 save_types 说「这一类是关的」——六类都有开关，没有默认自动保存的
        assert st["save_types"]["gif"] is False, st["save_types"]
        assert st["save_types"]["forward"] is True, st["save_types"]
        assert st["flat_bytes"] == total_bytes, (st["flat_bytes"], total_bytes)
        _install_web_stub(query={"kind": "forward", "limit": "1000"})
        fwd = asyncio.run(api.list_records())["data"]
        assert fwd["total"] == 60, fwd["total"]
        assert all(i["kind"] == "forward" for i in fwd["items"]), fwd["items"][:2]
        _install_web_stub(query={"kind": "image", "limit": "1000"})
        img = asyncio.run(api.list_records())["data"]
        assert img["total"] == 700, img["total"]
        _install_web_stub(query={"kind": "gif", "limit": "1000"})
        gifs = asyncio.run(api.list_records())["data"]
        assert gifs["total"] == 3, gifs
        assert all(i["kind"] == "gif" for i in gifs["items"]), gifs["items"][:3]
        _install_web_stub(query={"kind": "text"})
        assert asyncio.run(api.list_records())["data"]["total"] == 0
    finally:
        if saved is not None:
            sys.modules["astrbot.api.web"] = saved
        index.close()
    print("  OK -> stats 763 行 / forward 60 / image 700 / gif 3 / text 0")

def test_page_native_failure_marks_stale():
    """原消息已失效要记住（页面不再把它当可点的主按钮）；偶发失败不记。"""
    import asyncio
    import json
    banner("record_page: 原消息失效记为 stale / 网络抖动不记")
    tmp = tempfile.mkdtemp(prefix="warden_stale_")

    def _make(rid, msg_id):
        rdir = os.path.join(record_store.records_root(tmp), rid)
        os.makedirs(rdir, exist_ok=True)
        rec = {"schema_version": 1, "record_id": rid, "kind": "forward",
               "platform": "aiocqhttp", "group_id": "g1", "root_msg_id": msg_id,
               "captured_ts": 1720000000, "node_count": 1, "assets": [], "missing": [],
               "nodes": [{"index": 0, "uin": "", "name": "a", "time": 0,
                          "segments": [{"type": "text", "data": {"text": "hi"}}]}]}
        with open(os.path.join(rdir, "record.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        return rid

    class _Pl:
        def _records_root(self):
            return tmp

    api = record_page.RecordPageApi(_Pl())
    rid = _make("20260101-000000-g1-1481691615", "1481691615")
    d = record_admin.record_detail(tmp, rid)
    assert d["can_native"] is True and d["native_unavailable"] is False, d

    async def gone_native(bot, group_id, message_id):
        return {"ok": False, "error": f"取不到原消息 {message_id}: 消息不存在"}

    async def flaky_native(bot, group_id, message_id):
        return {"ok": False, "error": "网络超时"}

    async def ok_rebuild(bot, group_id, rec_, base):
        return {"ok": True, "mode": "rebuild", "new_message_id": "9001"}

    orig_native = record_admin.native_forward
    orig_rebuild = record_admin.rebuild_forward
    record_admin.native_forward = gone_native
    record_admin.rebuild_forward = ok_rebuild
    saved = sys.modules.pop("astrbot.api.web", None)
    try:
        _install_web_stub(body={"id": rid, "mode": "native", "fallback": True})
        out = asyncio.run(api.resend())
        assert out["status"] == "ok" and out["data"]["ok"] is True, out
        assert "已改用重建转发" in out["data"].get("note", ""), out
        # 元数据里落盘:刷新页面后按钮也不会再是「看起来能点的主操作」
        assert float(record_store.load_record(tmp, rid).get("native_unavailable") or 0) > 0
        d2 = record_admin.record_detail(tmp, rid)
        assert d2["can_native"] is False and d2["native_unavailable"] is True, d2

        rid2 = _make("20260101-000001-g1-777", "777")
        record_admin.native_forward = flaky_native
        _install_web_stub(body={"id": rid2, "mode": "native", "fallback": True})
        out2 = asyncio.run(api.resend())
        assert out2["status"] == "ok" and out2["data"]["ok"] is True, out2
        rec2 = record_store.load_record(tmp, rid2)
        assert not rec2.get("native_unavailable"), rec2
        assert record_admin.record_detail(tmp, rid2)["can_native"] is True
    finally:
        record_admin.native_forward = orig_native
        record_admin.rebuild_forward = orig_rebuild
        if saved is not None:
            sys.modules["astrbot.api.web"] = saved
    print("  OK -> 失效记 stale(can_native 变 False) / 超时不记")



def test_record_duplicate_dedupe_and_rerender():
    """同群同内容不新建目录（复用原条目）；插件按当前样式补/重画渲染图。"""
    import asyncio
    banner("record: 同群重复不建新目录；插件重画渲染图（补老存档）")
    tmp = tempfile.mkdtemp(prefix="wd_rerender_")
    png = b"\x89PNG\r\n\x1a\n" + b"9" * 8
    nodes = [{"sender": {"user_id": 7, "nickname": "阿七"}, "time": 1720000100,
              "message": [{"type": "text", "data": {"text": "同一段内容"}},
                          {"type": "image", "data": {"url": "https://x/a.png"}}]}]

    async def fetch_png(_data):
        return png

    rid1 = record_store.make_record_id("g1", "m1", ts=1720000100)
    rid2 = record_store.make_record_id("g1", "m2", ts=1720000200)
    rec1 = asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="m1",
        storage_root=tmp, fetch_image=fetch_png, ts=1720000100, record_id=rid1))
    rec2 = asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="m2",
        storage_root=tmp, fetch_image=fetch_png, ts=1720000200, record_id=rid2))
    assert rec1.get("duplicate_of") == "", rec1
    # 同群同内容:直接复用原条目,不建新目录、也不下图
    assert rec2.get("reused") is True, rec2
    assert rec2.get("record_id") == rid1, rec2
    assert rec2.get("duplicate_of_requested") == rid2
    assert not os.path.isdir(record_store.record_dir(tmp, rid2)), "重复内容不该建目录"

    segs = [{"type": "text", "data": {"text": "一句话"}}]
    e1 = asyncio.run(record_store.capture_text(
        segs, platform="aiocqhttp", group_id="g1", msg_id="t1", sender_id="7",
        sender_name="阿七", storage_root=tmp, ts=1720000300))
    e2 = asyncio.run(record_store.capture_text(
        segs, platform="aiocqhttp", group_id="g1", msg_id="t2", sender_id="7",
        sender_name="阿七", storage_root=tmp, ts=1720000400))
    assert e1.get("duplicate_of") == "", e1
    assert e2.get("reused") is True and e2["record_id"] == e1["record_id"], e2

    # 只有两条条目(两个队的重复都被合并),没有多出来的目录
    data = record_admin.scan_items(tmp)
    assert data["total"] == 2, data["total"]
    assert record_admin.record_detail(tmp, rid2) is None

    # 别的群同样的内容 = 另一条记录(每群各存一份),不算重复
    rec_other = asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g2", msg_id="m3",
        storage_root=tmp, fetch_image=fetch_png, ts=1720000500))
    assert rec_other.get("reused") is None and rec_other["record_id"] != rid1
    assert record_store.load_record(tmp, rec_other["record_id"]) is not None

    # 老存档没有 render.png —— 插件按当前样式补一张
    inst = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
        config={"storage_root": tmp, "match_mode": "blacklist",
                "enable_user_avatar": False, "enable_index_db": False,
                "reply_preview": False},
    )
    asyncio.run(inst.initialize())
    if inst._forwarder is None:
        print("  SKIP -> PIL unavailable")
        return
    assert record_store.has_render(tmp, rid1) is None
    out = asyncio.run(inst.rerender_record(rid1))
    assert out.get("ok") and out.get("has_render"), out
    assert record_store.has_render(tmp, rid1) == "render.png"
    assert record_admin.record_detail(tmp, rid1)["has_render"] is True
    out_text = asyncio.run(inst.rerender_record(e1["record_id"]))
    assert out_text.get("ok"), out_text
    assert asyncio.run(inst.rerender_record("nope"))["ok"] is False
    print("  OK -> 同群重复复用", rid1[:16], "| 纯文本复用", e1["record_id"][:16],
          "| 跨群另存", rec_other["record_id"][:16],
          "| rerender", out.get("bytes"), "bytes")


def test_plugin_forward_dedupe_reuses_entry():
    """同群同内容转发两次:插件层也只留一个条目目录(第二次复用,不建新目录)。"""
    import asyncio
    banner("plugin: 同群同内容转发两次只留一个条目目录")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={"storage_root": td, "match_mode": "blacklist",
                    "forward_render_image": True, "enable_user_avatar": False,
                    "enable_index_db": False, "reply_preview": False},
        )
        asyncio.run(inst.initialize())
        nodes = [{"sender": {"user_id": 206216334, "nickname": "橘雪莉"},
                  "time": 1718600000,
                  "message": [{"type": "text", "data": {"text": "同一段"}},
                               {"type": "image", "data": {"url": "https://x/a.png"}}]}]
        orig_download = plugin_main.download

        async def _fake_download(comp, *a, **k):
            return Downloaded(data=b"\x89PNG\r\n\x1a\n" + b"0" * 8,
                              mime="image/png", size=16)

        plugin_main.download = _fake_download
        try:
            def send(msg_id, ts):
                ctx = SaveContext(platform="aiocqhttp", group_id="961583271",
                                  sender_id="2510536451", sender_name="卡兹",
                                  msg_id=msg_id, idx=0, ts=ts)
                return asyncio.run(inst._save_forward(
                    Component(kind="forward", meta={"nodes": nodes}), ctx, None))

            first = send("fwd1", 1718600000)
            second = send("fwd2", 1718600100)
        finally:
            plugin_main.download = orig_download

        assert sorted(r.kind for r in first if r.ok) == ["forward_render"]
        assert sorted(r.kind for r in second if r.ok) == ["forward_render"]
        rows = record_store.list_records(td)
        assert len(rows) == 1, [r["record_id"] for r in rows]
        rid = rows[0]["record_id"]
        assert not os.path.isdir(record_store.record_dir(
            td, record_store.make_record_id("961583271", "fwd2", 1718600100)))
        # 第二条仍然写进了同一个条目(渲染图被复用那条的目录持有)
        assert record_store.has_render(td, rid) == "render.png"
        print("  OK -> 两条转发一个条目", rid)




# ---------------- PHASE 9 追加（v1.17.0 审计修复） ----------------

def test_index_delete_shared_file_keeps_bytes():
    banner("index: 删共享扁平文件只删一行,不动别人的字节 + resolve_flat 防绕过")
    from warden.index import AssetIndex
    tmp = tempfile.mkdtemp(prefix="wd_shared_")
    index = AssetIndex(db_path=os.path.join(tmp, "_warden.db"))
    index.open()
    try:
        path = os.path.join(tmp, "g1", "a.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"PNG")
        for i in range(3):
            index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                         msg_id="m%d" % i, idx=0, kind="image", path=path,
                         size=3, sha16="ab" * 8)
        rows = index.list_assets(kinds=["image"])["items"]
        assert len(rows) == 3 and index.count_path(path) == 3, rows
        assert index.delete_row(rows[0]["id"]) == 1
        assert index.count_path(path) == 2, "其余两条引用不该被带走"
        assert os.path.isfile(path), "还有人引用时文件不能删"
        # v1.17.0: normpath 之后判 _records 前缀, "./_records/..." 绕不过去
        assert record_admin.resolve_flat(tmp, "./_records/x/render.png") is None
        assert record_admin.resolve_flat(tmp, "g1/../_records/x") is None
        assert record_admin.resolve_flat(tmp, "g1/a.png") == os.path.abspath(path)
        assert record_admin.delete_flat(tmp, "g1/a.png", keep_file=True)["kept"] is True
        assert os.path.isfile(path)
        # 没有引用之后才由调用方删文件
        assert index.delete_row(rows[1]["id"]) == 1
        assert index.delete_row(rows[2]["id"]) == 1
        assert index.count_path(path) == 0
        assert record_admin.delete_flat(tmp, "g1/a.png")["deleted"] is True
        assert not os.path.isfile(path)
    finally:
        index.close()
    print("  OK -> 共享文件按行删,引用归零才删字节")


def test_index_paths_normalized_across_spellings():
    banner("index: 首存/去重两种路径写法归一 + shared 计数(不再漏数引用)")
    import sqlite3
    from warden.index import AssetIndex
    tmp = tempfile.mkdtemp(prefix="wd_sep_")
    st = Storage(root=tmp, dedupe=True,
                 pattern="{group_id}/{sender_id}/{short_id}{ext}", max_bytes=1000)
    comp = Component(kind="image", name="a.gif", url="http://x/a.gif")
    ctx1 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                       sender_name="alice", msg_id="m1", idx=0, ts=1718600000)
    first = st.save(b"GIF89a-bytes", comp, ctx1, mime="image/gif")
    # v1.18.2: 首次落盘返回的路径必须是 os.path.normpath 形态(和去重复用一致)
    assert first.path == os.path.normpath(first.path), first.path
    ctx2 = SaveContext(platform="aiocqhttp", group_id="g1", sender_id="u1",
                       sender_name="alice", msg_id="m2", idx=0, ts=1718600001)
    second = st.save(b"GIF89a-bytes", comp, ctx2, mime="image/gif")
    assert second.reused and second.path == first.path, (second.path, first.path)

    db = os.path.join(tmp, "_warden.db")
    index = AssetIndex(db_path=db)
    index.open()
    try:
        index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m1", idx=0, kind="gif", path=first.path,
                     size=len(b"GIF89a-bytes"), sha16="cd" * 8)
    finally:
        index.close()
    # 模拟老行:同一个文件,但路径里混着 "/"(首存写法)
    mixed = first.path.replace(os.sep, "/")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO assets (ts, platform, group_id, sender_id, msg_id, idx,"
            " kind, path, size, sha16) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (1, "aiocqhttp", "g1", "u1", "m2", 0, "gif", mixed,
             len(b"GIF89a-bytes"), "cd" * 8))
        con.commit()
    finally:
        con.close()
    index2 = AssetIndex(db_path=db)
    index2.open()
    try:
        # Windows: 混合写法会被启动归一; POSIX: 两种写法本来就相同 -> 归一空转
        if os.sep == "\\":
            assert index2.paths_normalized >= 1, index2.paths_normalized
        else:
            assert index2.paths_normalized == 0, index2.paths_normalized
        rows = index2.list_assets(kinds=["gif"])["items"]
        assert len(rows) == 2, rows
        assert {r["path"] for r in rows} == {first.path}, rows
        assert all(r["shared"] == 2 for r in rows), rows
        # 两种写法都要数到 2 行(以前混用写法只数到 1,删一行会误删字节)
        assert index2.count_path(first.path) == 2
        assert index2.count_path(mixed) == 2
        assert index2.forget_path(mixed) == 2
    finally:
        index2.close()
    print("  OK -> 两种写法归一,count/forget/shared 一致")

def test_record_text_dedupe_is_per_sender():
    import asyncio
    banner("record: 纯文本去重按发送人收窄(不同人发同一句话各自成条)")
    tmp = tempfile.mkdtemp(prefix="wd_textdup_")
    segs = [{"type": "text", "data": {"text": "666"}}]

    def cap(sender, mid, ts):
        return asyncio.run(record_store.capture_text(
            segs, platform="aiocqhttp", group_id="g1", msg_id=mid,
            sender_id=sender, sender_name=sender, storage_root=tmp, ts=ts))

    a = cap("u1", "m1", 1720000000)
    b = cap("u2", "m2", 1720000060)
    assert not b.get("reused"), "不同人发同一句话不能复用条目"
    assert b["record_id"] != a["record_id"]
    assert b["sender_id"] == "u2" and b["sender_name"] == "u2", b
    assert b["captured_ts"] == 1720000060, b
    assert len(record_store.list_records(tmp)) == 2
    c = cap("u1", "m3", 1720000120)
    assert c.get("reused") is True and c["record_id"] == a["record_id"], c
    print("  OK -> alice/bob 各一条, alice 重复才复用")


def test_plugin_text_receipt_has_preview():
    import asyncio
    banner("plugin: 纯文本回执带渲染图预览(以前只有一行文字)")
    with tempfile.TemporaryDirectory() as td:
        inst = plugin_main.MediaWardenStar(
            plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
            config={"storage_root": td, "match_mode": "blacklist",
                    "save_texts": True, "enable_index_db": True,
                    "reply_preview": True})
        asyncio.run(inst.initialize())
        try:
            if inst._forwarder is None:
                print("  SKIP -> PIL unavailable")
                return
            ev = _fake_event(group_id="g1", user_id="u1", nickname="alice",
                             message=[{"type": "text",
                                       "data": {"text": "预览我"}}],
                             message_str="预览我", message_id="mprev1",
                             timestamp=1718600000)
            out = _drive_command(inst, ev, inst.on_group_message)
            assert len(out) == 2, out
            assert "✅" in out[0].text, out[0].text
            assert str(out[1].text).endswith("render.png"), out[1].text
            assert os.path.isfile(out[1].text), out[1].text
        finally:
            if inst._index is not None:
                inst._index.close()
                inst._index = None
    print("  OK -> 文字回执 + render.png 预览")


def test_page_pagination_beyond_first_page():
    import asyncio
    from warden.index import AssetIndex
    banner("record_page: 分页能翻到第 2000 条之后(以前 offset 无效)")
    tmp = tempfile.mkdtemp(prefix="wd_paging_")
    index = AssetIndex(db_path=os.path.join(tmp, "_warden.db"))
    index.open()

    class _Pl:
        def __init__(self):
            self._index = index

        def _records_root(self):
            return tmp

    try:
        os.makedirs(os.path.join(tmp, "g1"), exist_ok=True)
        for i in range(2100):
            p = os.path.join(tmp, "g1", "f%d.png" % i)
            with open(p, "wb") as fh:
                fh.write(b"x")
            index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                         msg_id="m%d" % i, idx=0, kind="image", path=p, size=1)
        api = record_page.RecordPageApi(_Pl())
        saved = sys.modules.get("astrbot.api.web")
        try:
            _install_web_stub(query={"kind": "image", "limit": "50",
                                     "offset": "2050"})
            res = asyncio.run(api.list_records())
            data = res["data"]
            assert res["status"] == "ok" and data["total"] == 2100, data
            assert len(data["items"]) == 50, len(data["items"])
            _install_web_stub(query={"limit": "5", "offset": "1999"})
            got = asyncio.run(api.list_records())["data"]["items"]
            assert len(got) == 5, got
        finally:
            if saved is None:
                sys.modules.pop("astrbot.api.web", None)
            else:
                sys.modules["astrbot.api.web"] = saved
    finally:
        index.close()
    print("  OK -> offset 2050 取到 50 条, offset 1999 取到 5 条")




def test_plugin_style_preview_cleans_temp():
    import asyncio, glob
    banner("record_page: 样式预览不残留临时目录")
    tmp = tempfile.mkdtemp(prefix="wd_styletmp_")
    inst = plugin_main.MediaWardenStar(
        plugin_main.MediaWardenStar.__init__.__globals__["Context"](),
        config={"storage_root": tmp, "match_mode": "blacklist",
                "enable_index_db": False, "log_to_stdout": False})
    asyncio.run(inst.initialize())
    if inst._forwarder is None:
        print("  SKIP -> PIL unavailable")
        return
    api = record_page.RecordPageApi(inst)
    pattern = os.path.join(tempfile.gettempdir(), "wd_style_*")
    before = set(glob.glob(pattern))
    blob = asyncio.run(api._render_preview({}))
    after = set(glob.glob(pattern))
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", blob[:8]
    assert after == before, after - before
    print("  OK -> 预览渲染后临时目录已清理")


def test_record_admin_node_payloads_keep_time():
    import asyncio
    banner("record_admin: 重建转发的节点带原时间戳")
    tmp = tempfile.mkdtemp(prefix="wd_ntime_")
    nodes = [{"sender": {"user_id": 7, "nickname": "阿七"},
              "time": 1718600123,
              "message": [{"type": "text", "data": {"text": "hi"}}]}]

    async def _noimg(_d):
        return None

    rid = record_store.make_record_id("g1", "m1", ts=1720000000)
    asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="m1",
        storage_root=tmp, fetch_image=_noimg, ts=1720000000, record_id=rid))
    rec = record_store.load_record(tmp, rid)
    payloads = record_admin.node_payloads(rec, record_store.record_dir(tmp, rid))
    assert payloads and payloads[0]["data"]["time"] == 1718600123, payloads
    print("  OK -> time 原样带回", payloads[0]["data"]["time"])


def test_plugin_resend_native_failure_marks_unavailable():
    import asyncio
    banner("record_page: 原地转发失败(短 id 缓存失效)记成「原消息已失效」")
    tmp = tempfile.mkdtemp(prefix="wd_resend_")
    nodes = [{"sender": {"user_id": 7, "nickname": "a"}, "time": 1,
              "message": [{"type": "text", "data": {"text": "x"}}]}]
    rid = record_store.make_record_id("g1", "1481691615", ts=1720000000)

    async def _noimg(_d):
        return None

    asyncio.run(record_store.capture(
        nodes, platform="aiocqhttp", group_id="g1", msg_id="1481691615",
        storage_root=tmp, fetch_image=_noimg, ts=1720000000, record_id=rid))

    class _Bot:
        async def call_action(self, action, **kw):
            if action == "get_msg":
                return {"message": [{"type": "forward",
                                     "data": {"id": "F"}}]}
            raise RuntimeError("消息不存在")

    class _Pl:
        def _records_root(self):
            return tmp

    api = record_page.RecordPageApi(_Pl())
    api._bot = lambda: _Bot()
    saved = sys.modules.get("astrbot.api.web")
    try:
        _install_web_stub(body={"id": rid, "group_id": "other",
                               "mode": "native", "fallback": False})
        res = asyncio.run(api.resend())
        assert res["status"] == "ok" and res["data"]["ok"] is False, res
        # 「消息不存在」= 短 id 已不在 NapCat 缓存里,发哪个群都一样,标记失效
        rec = record_store.load_record(tmp, rid)
        assert rec.get("native_unavailable"), "缓存失效就要记成原消息不可用"
    finally:
        if saved is None:
            sys.modules.pop("astrbot.api.web", None)
        else:
            sys.modules["astrbot.api.web"] = saved
    print("  OK -> 失败即记失效(与目标群无关)")



def test_upgrade_v18_normalizes_and_purges():
    banner("upgrade: v1.18 一次性归一化（补 kind / 删 legacy / GIF / 空壳 / 备份后删旧渲染图）")
    import asyncio
    import json as _json
    import zipfile
    from warden.index import AssetIndex
    from warden import upgrade as upgrade_mod
    tmp = tempfile.mkdtemp(prefix="wd_upg_")

    # 1) 老条目:没有 kind,带 legacy 字段 + legacy 副本文件
    rid = record_store.make_record_id("g1", "m1", ts=1720000000)
    rdir = record_store.record_dir(tmp, rid)
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "record.json"), "w", encoding="utf-8") as fh:
        _json.dump({"schema_version": 1, "record_id": rid, "platform": "aiocqhttp",
                    "group_id": "g1", "root_msg_id": "m1",
                    "captured_ts": 1720000000, "node_count": 0, "nodes": [],
                    "assets": [], "missing": [],
                    "legacy_file": "legacy_forward.json", "legacy_migrated": 1,
                    "legacy_source": "961583271/2510536451/x.json"}, fh)
    legacy_path = os.path.join(rdir, upgrade_mod.LEGACY_FILENAME)
    with open(legacy_path, "wb") as fh:
        fh.write(b"{}")
    # 2) 新条目已经有 kind,不该被动
    rec2 = asyncio.run(record_store.capture_text(
        [{"type": "text", "data": {"text": "hi"}}], platform="aiocqhttp",
        group_id="g1", msg_id="m2", sender_id="u1", sender_name="甲",
        storage_root=tmp, ts=1720000010))
    rid2 = rec2["record_id"]
    assert not os.path.exists(os.path.join(tmp, upgrade_mod.MARKER_NAME))

    index = AssetIndex(db_path=os.path.join(tmp, "_warden.db"))
    index.open()
    try:
        flat = os.path.join(tmp, "g1", "u1")
        os.makedirs(flat, exist_ok=True)
        # 3) 老的 GIF 行是 kind=image
        gif = os.path.join(flat, "20250101_000000_gif_aaa.gif")
        with open(gif, "wb") as fh:
            fh.write(b"GIF89a")
        index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m3", idx=0, kind="image", path=gif, size=6,
                     sha16="a" * 16)
        # 4) 迁移失败的空壳(57 B、nodes:null)
        junk = os.path.join(flat, "20250101_000001_forward_json_dead.json")
        with open(junk, "w", encoding="utf-8") as fh:
            _json.dump({"forward": True, "node_count": 0, "nodes": None}, fh)
        index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                     msg_id="m4", idx=0, kind="forward", path=junk,
                     size=os.path.getsize(junk), sha16="b" * 16)
        # 5) 两张旧扁平渲染图,其中一张被两行引用
        render_a = os.path.join(flat, "20250101_000002_forward_image_aaa.png")
        render_b = os.path.join(flat, "20250101_000003_forward_image_bbb.png")
        with open(render_a, "wb") as fh:
            fh.write(b"PNG-A")
        with open(render_b, "wb") as fh:
            fh.write(b"PNG-B")
        # 另一个群有同名文件(模板相同):备份 zip 里必须改名,不能写两条同名 entry
        other_dir = os.path.join(tmp, "g2", "u2")
        os.makedirs(other_dir, exist_ok=True)
        render_c = os.path.join(other_dir, os.path.basename(render_a))
        with open(render_c, "wb") as fh:
            fh.write(b"PNG-C")
        for mid, idx, path, sha in (("m5", 100, render_a, "c" * 16),
                                    ("m5", 101, render_a, "c" * 16),
                                    ("m6", 100, render_b, "d" * 16),
                                    ("m7", 100, render_c, "e" * 16)):
            index.record(platform="aiocqhttp", group_id="g1", sender_id="u1",
                         msg_id=mid, idx=idx, kind="forward_render",
                         path=path, size=5, sha16=sha)
        before = index.count()
        assert before == 6, before

        zip_path = os.path.join(tempfile.mkdtemp(prefix="wd_upgzip_"), "backup.zip")
        rep = upgrade_mod.run_once(tmp, index, backup_zip=zip_path)
        assert rep["skipped"] is False, rep
        assert rep["entries"] == 1 and rep["legacy_removed"] == 1, rep
        assert rep["gif_rows"] == 1, rep
        assert rep["junk_rows"] == 1, rep
        assert rep["rows"] == 4 and rep["files"] == 3 and rep["bytes"] == 15, rep
        assert rep["packed"] == 3 and os.path.isfile(zip_path), rep

        # 条目:补了 kind、legacy 字段与副本都没了
        rec = record_store.load_record(tmp, rid)
        assert rec["kind"] == "forward", rec
        assert not [k for k in rec if k.startswith("legacy_")], rec
        assert not os.path.exists(legacy_path)
        assert record_store.load_entry(tmp, rid2)["kind"] == "text"

        # 索引:GIF 行换了 kind,空壳行/渲染行删掉了,共享文件只删一次
        assert [r["path"] for r in index.list_assets(kinds=["gif"])["items"]] == [gif]
        assert index.list_assets(kinds=["image"])["total"] == 0
        assert index.list_assets(kinds=["forward"])["total"] == 0, "空壳行应删"
        assert not os.path.exists(junk)
        assert not os.path.exists(render_a) and not os.path.exists(render_b)
        assert not os.path.exists(render_c)
        assert index.count() == 1, index.count()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            manifest = _json.loads(zf.read("manifest.json").decode("utf-8"))
        assert "manifest.json" in names and len(manifest["rows"]) == 4, names
        file_names = [n for n in names if n.startswith("files/")]
        assert len(file_names) == 3, names
        assert len(set(file_names)) == 3, "同名文件必须改名,不能两条同名 entry"

        # 闸门:marker + PRAGMA user_version,第二次直接跳过
        assert upgrade_mod.read_version(tmp) == 2
        assert int(index._conn.execute("PRAGMA user_version").fetchone()[0]) == 2
        again = upgrade_mod.run_once(tmp, index, backup_zip=zip_path)
        assert again["skipped"] is True, again
        assert index.count() == 1, "重跑不该二次删除"
    finally:
        index.close()
    print("  OK -> 1 条目归一化 / 删 1 legacy / GIF 1 行 / 空壳 1 行 / 渲染 4 行 3 文件已备份(同名已改名)")


PHASE9 = [
    # v1.14.0: 类型门控 + 纯文本条目
    test_policy_media_kind_and_type_flags,
    test_record_text_entry_roundtrip,
    test_plugin_e2e_text_message_saved_as_entry,
    test_plugin_type_gating_drops_disabled_and_bad_ext,
    test_plugin_forward_saved_as_single_entry,
    test_record_admin_categories_and_flat,
    test_plugin_forward_auto_forward_to_target_groups,
    test_plugin_auto_forward_native_first_then_rebuild,
    test_plugin_auto_forward_skips_reused_entry,
    test_forwarder_apply_style_and_avatar_shape,
    test_plugin_style_update_and_page,
    test_config_reads_nested_schema_groups,
    test_plugin_persist_config_writes_schema_group,
    test_record_admin_forward_returns_new_message_id,
    test_record_detail_render_and_uin_reliability,
    test_page_stats_and_kind_filter_use_real_counts,
    test_page_native_failure_marks_stale,
    test_record_duplicate_dedupe_and_rerender,
    test_plugin_forward_dedupe_reuses_entry,
    # v1.17.0: 审计修复的回归 + 之前定义了却没注册的 4 个用例
    test_upgrade_v18_normalizes_and_purges,
    test_index_delete_shared_file_keeps_bytes,
    test_index_paths_normalized_across_spellings,
    test_record_text_dedupe_is_per_sender,
    test_plugin_text_receipt_has_preview,
    test_page_pagination_beyond_first_page,
    test_plugin_style_preview_cleans_temp,
    test_record_admin_node_payloads_keep_time,
    test_plugin_resend_native_failure_marks_unavailable,
    test_forwarder_emoji_mixed_layout,
    test_recover_raw_url_overrides_local_jpeg,
    test_recover_raw_no_http_keeps_local,
    test_recover_pairs_refs_by_identity_not_position,
]


if __name__ == "__main__":
    # 离线冒烟不真等「迟到缓存重查」的 sleep,否则下载失败类用例每个多等 16s。
    # (需要验证等待逻辑本身的用例自己在内部把这一项恢复成真值)
    plugin_main._LATE_CACHE_DELAYS_S = ()
    for t in (PHASE1 + PHASE2 + PHASE3 + PHASE4 + PHASE5 + PHASE6 + PHASE7
              + PHASE8 + PHASE9):
        t()
    print("\nALL OK (phase1=%d phase2=%d phase3=%d phase4=%d phase5=%d phase6=%d "
          "phase7=%d phase8=%d phase9=%d, total=%d)"
          % (len(PHASE1), len(PHASE2), len(PHASE3), len(PHASE4), len(PHASE5),
             len(PHASE6), len(PHASE7), len(PHASE8), len(PHASE9),
             len(PHASE1) + len(PHASE2) + len(PHASE3) + len(PHASE4) + len(PHASE5)
             + len(PHASE6) + len(PHASE7) + len(PHASE8) + len(PHASE9)))
