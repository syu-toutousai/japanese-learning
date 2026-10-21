#!/usr/bin/env python3
"""Build web-lite.html from a courseware index.html.

The lite page keeps the same app but replaces every base64 data URI with a URL:

- files already present under audio/, audio-moji/, moji_audio/, scenes/, images/,
  img/, assets/ are reused by SHA-256 (no duplication);
- Nadeshiko audio and thumbnails are mapped back to their CDN URLs;
- anything else is written to web-lite-assets/ as a fallback.

index.html remains the self-contained offline build.
"""

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_URI = re.compile(r"data:([a-zA-Z0-9.+/-]+);base64,([A-Za-z0-9+/=_-]+)")
ASSET_DIRS = ("audio", "audio-moji", "moji_audio", "scenes", "images", "img", "assets")
ASSET_EXTS = {".mp3", ".webp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".mp4", ".woff", ".woff2"}
EXT_BY_MIME = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
    "font/woff": "woff",
    "font/woff2": "woff2",
}
CUSTOM_LITE = {"keiji-courseware"}


def js_const(html, name):
    m = re.search(rf"(?:const|let|var)\s+{re.escape(name)}\s*=\s*(.*?);\s*\n", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def thumb_for(audio):
    return audio[:-4] + ".webp" if isinstance(audio, str) and audio.endswith(".mp3") else None


def nade_replacements(html):
    repl = {}
    clips = {}
    items = js_const(html, "ITEMS") or []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            iid = it.get("id")
            for i, sc in enumerate(it.get("nadeshiko") or []):
                if not isinstance(sc, dict):
                    continue
                clips[f"{iid}-n{i}"] = sc
                audio, thumb = sc.get("audio"), sc.get("thumb")
                if isinstance(thumb, str) and thumb.startswith("data:image"):
                    tu = thumb_for(audio)
                    if tu:
                        repl[thumb] = tu
                if isinstance(audio, str) and audio.startswith("data:audio") and isinstance(thumb, str):
                    if thumb.startswith("http") and thumb.endswith(".webp"):
                        repl[audio] = thumb[:-5] + ".mp3"

    nade_audio = js_const(html, "NADE_AUDIO")
    if isinstance(nade_audio, dict):
        for key, value in nade_audio.items():
            sc = clips.get(key)
            if not sc or not isinstance(value, str) or not value.startswith("data:audio"):
                continue
            audio = sc.get("audio")
            if isinstance(audio, str) and audio.startswith("http"):
                repl[value] = audio

    scenes = js_const(html, "SCENES")
    if isinstance(scenes, dict):
        for key, value in scenes.items():
            sc = clips.get(key)
            if not sc:
                continue
            audio, thumb = sc.get("audio"), sc.get("thumb")
            if isinstance(value, str) and value.startswith("data:image"):
                if isinstance(thumb, str) and thumb.startswith("http"):
                    repl[value] = thumb
            elif isinstance(value, dict):
                for name in ("mp3", "audio"):
                    if isinstance(value.get(name), str) and value[name].startswith("data:audio"):
                        if isinstance(audio, str) and audio.startswith("http"):
                            repl[value[name]] = audio
                for name in ("img", "thumb", "image"):
                    if isinstance(value.get(name), str) and value[name].startswith("data:image"):
                        if isinstance(thumb, str) and thumb.startswith("http"):
                            repl[value[name]] = thumb
    return {k: v for k, v in repl.items() if v}


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_index(cw):
    index = {}
    for sub in ASSET_DIRS:
        folder = cw / sub
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in ASSET_EXTS:
                continue
            try:
                index.setdefault(sha256_file(path), path.relative_to(cw).as_posix())
            except OSError:
                pass
    return index


def build_one(cw, force=False):
    source = cw / "index.html"
    if not source.exists():
        print(f"skip {cw.name}: no index.html")
        return False
    if cw.name in CUSTOM_LITE and not force:
        print(f"skip {cw.name}: custom web-lite.html (use --force to overwrite)")
        return False

    html = source.read_text(encoding="utf-8")
    matches = list(DATA_URI.finditer(html))
    if not matches:
        print(f"skip {cw.name}: no base64 data URI")
        return False

    unique = {}
    for m in matches:
        uri = m.group(0)
        if uri in unique:
            continue
        mime, b64 = m.groups()
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        unique[uri] = (mime, raw, hashlib.sha256(raw).hexdigest())

    cdn = nade_replacements(html)
    reused = asset_index(cw)
    assets_dir = cw / "web-lite-assets"
    replacements = {}
    used_assets = set()
    counts = {"cdn": 0, "reuse": 0, "copy": 0}
    bytes_copy = 0

    for uri, (mime, raw, digest) in unique.items():
        if uri in cdn:
            replacements[uri] = cdn[uri]
            counts["cdn"] += 1
            continue
        if digest in reused:
            replacements[uri] = reused[digest]
            counts["reuse"] += 1
            continue
        ext = EXT_BY_MIME.get(mime, "bin")
        name = f"{digest}.{ext}"
        assets_dir.mkdir(exist_ok=True)
        target = assets_dir / name
        if not target.exists():
            target.write_bytes(raw)
        replacements[uri] = f"web-lite-assets/{name}"
        used_assets.add(name)
        counts["copy"] += 1
        bytes_copy += len(raw)

    if assets_dir.is_dir():
        for old in assets_dir.iterdir():
            if old.is_file() and old.name not in used_assets:
                old.unlink()

    lite = DATA_URI.sub(lambda m: replacements[m.group(0)], html)
    out = cw / "web-lite.html"
    out.write_text(lite, encoding="utf-8")
    print(
        f"{cw.name:28} {source.stat().st_size/1048576:6.1f}M -> {out.stat().st_size/1048576:6.1f}M "
        f"(cdn {counts['cdn']}, reuse {counts['reuse']}, copy {counts['copy']}, "
        f"copy {bytes_copy/1048576:.1f}M)"
    )
    return True


def write_hub(extra=()):
    rows = []
    for path in sorted(ROOT.glob("*-courseware/web-lite.html")):
        cw = path.parent
        html = path.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        name = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else cw.name
        original = cw / "index.html"
        rows.append((original.stat().st_size if original.exists() else 0, name, cw.name, path.stat().st_size))
    rows.sort(reverse=True)
    cards = []
    for orig, name, dirname, lite_size in rows:
        cards.append(
            f'<a class="card" href="{dirname}/web-lite.html">'
            f'<span class="name">{name}</span>'
            f'<span class="meta">{orig/1048576:.1f}MB → {lite_size/1048576:.2f}MB</span>'
            f'<span class="go">開く →</span></a>'
        )
    for name, url, note in extra:
        cards.append(
            f'<a class="card" href="{url}">'
            f'<span class="name">{name}</span>'
            f'<span class="meta">{note}</span>'
            f'<span class="go">開く →</span></a>'
        )
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>日本語文法 ・ Lite 版一覧</title>
<style>
:root{{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;--acc:#0ca678}}
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 16px 60px;background:var(--bg);color:var(--ink);
font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:820px;margin:auto}}
h1{{font-size:24px;margin:0 0 6px}}
p{{color:var(--sub);font-size:13.5px;line-height:1.8;margin:0 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}}
.card{{display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:14px 16px;text-decoration:none;color:var(--ink);
box-shadow:0 2px 8px rgba(30,40,90,.05);transition:.15s}}
.card:hover{{transform:translateY(-2px);border-color:var(--acc)}}
.name{{font-weight:700;font-size:14.5px;line-height:1.5}}
.meta{{font-size:11.5px;color:var(--sub)}}
.go{{font-size:12px;color:var(--acc);font-weight:700}}
.back{{display:inline-block;margin-bottom:16px;color:var(--acc);text-decoration:none;font-size:13px;font-weight:700}}
</style>
</head>
<body><div class="wrap">
<a class="back" href="index.html">← 通常の索引</a>
<h1>⚡ Lite 版一覧</h1>
<p>初回 HTML だけを軽くし、音声・画像はクリック時または表示時に取得します。
Nadeshiko 原声は CDN 直リンクです。完全オフライン版は各课件の <code>index.html</code>。</p>
<div class="grid">
{''.join(cards)}
</div>
</div></body></html>
"""
    (ROOT / "lite.html").write_text(html, encoding="utf-8")
    print(f"wrote {ROOT/'lite.html'} ({len(rows)} coursewares)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="courseware directory names (default: index.html >= --min-mb)")
    ap.add_argument("--all", action="store_true", help="process every courseware with data URIs")
    ap.add_argument("--min-mb", type=float, default=2.0)
    ap.add_argument("--force", action="store_true", help="overwrite custom web-lite.html (keiji)")
    ap.add_argument("--no-hub", action="store_true")
    args = ap.parse_args()

    if args.dirs:
        targets = [ROOT / d for d in args.dirs]
    else:
        targets = []
        for cw in sorted(ROOT.glob("*-courseware")):
            source = cw / "index.html"
            if source.exists() and (args.all or source.stat().st_size >= args.min_mb * 1048576):
                targets.append(cw)

    ok = 0
    for cw in targets:
        if not cw.is_dir():
            print(f"skip {cw}: not a directory")
            continue
        ok += build_one(cw, force=args.force)

    if not args.no_hub:
        write_hub(extra=(
            ("和語アトラス（wago-atlas）", "https://syu-toutousai.github.io/wago-atlas/web-lite.html", "別リポジトリ・1342 音声を外部化"),
        ))
    print(f"done: {ok} generated")


if __name__ == "__main__":
    main()
