# Changelog

## v1.6.0

### Added
- Forward text rendering now supports emoji via system emoji font (Windows `seguiemj.ttf` / Linux `NotoColorEmoji.ttf` / macOS `Apple Color Emoji.ttc`). Emoji codepoints are detected and routed through the emoji font while CJK/Latin text stays on the regular font. ZWJ chains and VS-16 variation selectors are kept atomic. Without a system emoji font the original plain-text layout runs unchanged.
- New module-level helpers in `warden/forwarder.py`: `_is_emoji_char`, `_find_emoji_font`, `_wrap_mixed`, `_line_h_mixed`, `_char_w`. New `Forwarder._font_emoji` attribute set in `_ensure_fonts`.
- Smoke test `forwarder: emoji mixed-layout render (v1.6.0)` covers codepoint detection, segment splitting, ZWJ atomicity, VS-16 handling, newline behaviour, and end-to-end render.

### Known limitations
- Pillow 12 does not parse the CBDT / Apple color emoji glyph tables — emoji render as **black-and-white vector glyphs** rather than color. This removes tofu boxes but does not deliver colored emoji. Color emoji is planned for v2.0 via the twemoji PNG set.

### Fixed
- Two regressions caught by the smoke test's `(400, 253) 10789B` plain-text byte-equivalence assertion and fixed in the same release:
  - `_wrap_mixed` originally treated `\n` as a soft wrap; now matches `wrap_text` and forces a new line at every `\n`.
  - `_wrap_mixed` originally split consecutive non-emoji characters into single-char segments; now merges them so plain-text draw calls remain byte-equivalent to v1.5.1.


## v1.5.0

### Added
- New config `download_timeout_s` (default 30): per-download timeout in seconds for media and forward-node images.
- New config `at_on_save_failure` (default true): when any asset fails to save, @-mention the listened sender as a reminder.

### Changed
- Forward render now treats a node image that fails after all retries as a hard failure: the whole forward image is not rendered or saved (`forward_render` -> ok=False), instead of silently dropping the image.

### Fixed
- Forward image collection mapped downloaded bytes to nodes via `all_urls.index(url)`, which mis-assigned images when the same URL appeared in multiple nodes; now mapped by position.


## v1.4.14

### Changed
- Restore a subtle 6px-per-level indentation for nested forwarded messages.
- Use distinct left-rail colors for different nested forwarded-message levels.

## v1.4.13

### Fixed
- Extract top-level forward ids from `resid` / `res_id` and `nodes` segments so fetched sender names can replace generic inline names like `QQ用户`.

## v1.4.12

### Changed
- Reduce nested forwarded-message indentation so framed replies keep more content width.

## v1.4.11

### Added
- Render nested forwarded chat records with indented framed bubbles.

### Fixed
- Prefer `get_forward_msg` nodes over inline nodes when a top-level forward id is available, so first-level sender names can use the full fetched metadata instead of generic `QQ用户`.

## v1.4.10

### Added
- Expand nested forwarded chat records up to 2 levels before JSON saving and image rendering.

## v1.4.9

### Fixed
- Keep forwarded portrait images at full bubble width and render their full height without cropping.
- Keep forward-render height pre-calculation consistent with the actual PIL image draw size.

## v1.4.8

### ??
- ??????????????????? QQ ???? url ? `is_origin=0` ??? `is_origin=1`?`gchat.qpic.cn` / `multimedia.nt.qq` ???????????? url?

### ??
- ???????? LANCZOS ????????????????????????
- ?????????`max_image_width` 360 ? 720????????????????
- ????????????????????????????

### ??
- ?????? JSON?napcat/OneBot ??? forward id ?????? `get_forward_msg` ??????????/???
- ?? forward / node ??? `forward_id`??? napcat `get_forward_msg` ?????
