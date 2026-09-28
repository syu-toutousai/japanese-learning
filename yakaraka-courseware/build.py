#!/usr/bin/env python3
"""Build the self-contained 「〜やか・らか 形容動詞図鑑」 courseware HTML.

用法：往 yakaraka.json 加一条 → 运行本脚本 → index.html 自动长出卡片、
汉字要素、发音与题库。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了句子自动重录；某条合成失败只跳过该条，不影响整体构建。
"""

import base64
import copy
import hashlib
import json
import random
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import pykakasi
    _kks = pykakasi.kakasi()
    HAS_KAKASI = True
except ImportError:
    HAS_KAKASI = False

ROOT = Path(__file__).parent
DATA = ROOT / "yakaraka.json"
AUDIO_DIR = ROOT / "audio"
NADE_DIR = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260928
MIN_MP3 = 300


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ────────────────────────────────────────────── furigana (ruby)

_KANJI = r"\u4e00-\u9fff\u3005\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")

_READING_OVERRIDES = [
    ("賑やか", "にぎやか"),
    ("静か", "しずか"),
    ("確か", "たしか"),
    ("豊か", "ゆたか"),
    ("緩やか", "ゆるやか"),
    ("冷ややか", "ひややか"),
    ("淑やか", "しとやか"),
    ("煌びやか", "きらびやか"),
    ("伸びやか", "のびやか"),
    ("晴れやか", "はれやか"),
    ("密やか", "ひそやか"),
    ("涼やか", "すずやか"),
    ("誇らか", "ほこらか"),
    ("大らか", "おおらか"),
    ("滑らか", "なめらか"),
    ("詳らか", "つまびらか"),
    ("暖か", "あたたか"),
    ("細か", "こまか"),
    ("密か", "ひそか"),
    ("愚か", "おろか"),
    ("疎か", "おろそか"),
    ("俄か", "にわか"),
    ("仄か", "ほのか"),
    ("大まか", "おおまか"),
    ("清か", "さやか"),
    ("軽々しい", "かるがるしい"),
    ("疎ましい", "うとましい"),
    ("誇らしい", "ほこらしい"),
    ("愚かしい", "おろかしい"),
    ("晴れがましい", "はれがましい"),
    ("艶めかしい", "なまめかしい"),
    ("荒々しい", "あらあらしい"),
    ("麗しい", "うるわしい"),
    ("入って", "はいって"),
    ("抱い", "いだい"),
    ("麗しく", "うるわしく"),
    ("晴れがましく", "はれがましく"),
    ("疎ましく", "うとましく"),
    ("軽々しく", "かるがるしく"),
    ("日ざし", "ひざし"),
    ("心地よい", "ここちよい"),
    ("口当たり", "くちあたり"),
    ("来な", "こな"),
    ("賑々しく", "にぎにぎしく"),
    ("賑々しい", "にぎにぎしい"),
    ("華やか", "はなやか"),
    ("華々しい", "はなばなしい"),
    ("艶やか", "あでやか"),
    ("艶々しい", "つやつやしい"),
    ("爽やか", "さわやか"),
    ("穏やか", "おだやか"),
    ("鮮やか", "あざやか"),
    ("健やか", "すこやか"),
    ("速やか", "すみやか"),
    ("細やか", "こまやか"),
    ("細々しい", "こまごましい"),
    ("軽やか", "かろやか"),
    ("和やか", "なごやか"),
    ("雅やか", "みやびやか"),
    ("円やか", "まろやか"),
    ("明らか", "あきらか"),
    ("清らか", "きよらか"),
    ("安らか", "やすらか"),
    ("平らか", "たいらか"),
    ("柔らか", "やわらか"),
    ("朗らか", "ほがらか"),
    ("麗らか", "うららか"),
    ("高らか", "たからか"),
    ("長閑", "のどか"),
    ("厳か", "おごそか"),
    ("微か", "かすか"),
    ("遥か", "はるか"),
    ("僅か", "わずか"),
    ("清々しい", "すがすがしい"),
    ("若々しく", "わかわかしく"),
    ("若々しい", "わかわかしい"),
    ("弱々しく", "よわよわしく"),
    ("弱々しい", "よわよわしい"),
    ("着飾っ", "きかざっ"),
    ("表通り", "おもてどおり"),
    ("通り", "とおり"),
    ("上った", "のぼった"),
    ("彼の", "かれの"),
    ("琴", "こと"),
]


def _furi(text):
    if not HAS_KAKASI:
        return text
    out = []
    for tok in _kks.convert(text):
        orig, hira = tok["orig"], tok["hira"]
        if orig == hira or not hira:
            out.append(orig)
            continue
        if _KANJI_RE.search(orig) and not _KANJI_RE.search(hira):
            out.append(f"<ruby>{orig}<rt>{hira}</rt></ruby>")
        else:
            out.append(orig)
    return "".join(out)


_HIRA_TAIL = re.compile(r"[ぁ-んー]+$")


def _split_ruby(phrase, reading):
    """Wrap only the kanji core: 賑やか/にぎやか -> (賑, にぎ, やか)."""
    m = _HIRA_TAIL.search(phrase)
    if not m:
        return phrase, reading, ""
    tail = m.group(0)
    if reading.endswith(tail) and len(reading) > len(tail):
        return phrase[:m.start()], reading[:len(reading) - len(tail)], tail
    return phrase, reading, ""


def add_furigana(text):
    if not HAS_KAKASI:
        return text
    if not _KANJI_RE.search(text):
        return text
    matches = []
    for phrase, reading in _READING_OVERRIDES:
        for m in re.finditer(re.escape(phrase), text):
            matches.append((m.start(), m.end(), phrase, reading))
    for m in _INLINE.finditer(text):
        matches.append((m.start(), m.end(), m.group(1), m.group(2)))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    clean = []
    for m in matches:
        if not clean or m[0] >= clean[-1][1]:
            clean.append(m)
    out = []
    pos = 0
    for start, end, kanji, reading in clean:
        out.append(_furi(text[pos:start]))
        base, rt, tail = _split_ruby(kanji, reading)
        out.append(f"<ruby>{base}<rt>{rt}</rt></ruby>{tail}")
        pos = end
    out.append(_furi(text[pos:]))
    return "".join(out)


# ────────────────────────────────────────────── load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] yakaraka.json 不是合法 JSON：{e}")

    meta = data.get("meta", {})
    groups = data.get("groups", [])
    items = data.get("items", [])
    contrasts = data.get("contrasts", [])
    kanji_families = data.get("kanji_families", [])
    shi_frames = data.get("shi_frames", [])
    errors = []

    gids = set()
    for g in groups:
        gid = g.get("id", "")
        if not gid:
            errors.append(f"group 缺少 id：{g}")
        elif gid in gids:
            errors.append(f"group id 重复：{gid}")
        gids.add(gid)
        for key in ("name", "color", "emoji"):
            if not g.get(key):
                errors.append(f"group「{gid}」缺少 {key}")

    seen = set()
    ids = set()
    for it in items:
        iid = it.get("id", "")
        label = iid or it.get("word", "?")
        if not iid:
            errors.append(f"条目「{label}」缺少 id")
        elif iid in seen:
            errors.append(f"条目 id 重复：{iid}")
        seen.add(iid)
        ids.add(iid)
        if it.get("group") not in gids:
            errors.append(f"「{label}」group「{it.get('group')}」不在 groups 里")
        for key in ("word", "read", "level", "mean", "core"):
            if not it.get(key):
                errors.append(f"「{label}」缺少 {key}")
        exs = it.get("examples") or []
        if not exs:
            errors.append(f"「{label}」至少需要一条例句")
        form = it.get("form") or ""
        if form and exs and form not in exs[0].get("jp", ""):
            errors.append(f"「{label}」form「{form}」不在首条例句里：{exs[0].get('jp','')}")

    for it in items:
        for ref_key in ("shi", "base"):
            ref = it.get(ref_key)
            if ref and ref not in ids:
                errors.append(f"「{it['id']}」{ref_key} 引用了未知 id：{ref}")
        fr = it.get("frame")
        if fr and fr not in {f.get("id") for f in shi_frames}:
            errors.append(f"「{it['id']}」frame 引用了未知 id：{fr}")

    if errors:
        print("[!] yakaraka.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, groups, items, contrasts, kanji_families, shi_frames


# ────────────────────────────────────────────── tts

def cache_path(logical_id, text):
    h = hashlib.sha1(text.encode()).hexdigest()[:10]
    return AUDIO_DIR / f"{logical_id}-{h}.mp3"


def gen_one(task):
    logical_id, text = task
    path = cache_path(logical_id, text)
    if path.exists() and path.stat().st_size > MIN_MP3:
        return True
    for _ in range(3):
        r = subprocess.run(
            ["edge-tts", "--voice", VOICE, f"--rate={RATE}", "--text", text,
             "--write-media", str(path)],
            capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > MIN_MP3:
            return True
    if path.exists():
        path.unlink()
    return False


def gen_audio(items):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []
    for it in items:
        tasks.append((f"{it['id']}-w", it["word"]))
        for i, ex in enumerate(it.get("examples") or []):
            tasks.append((f"{it['id']}-e{i}", ex["jp"]))

    todo = [(lid, txt) for lid, txt in tasks if not cache_path(lid, txt).exists()]
    print(f"[1/4] tts: {len(tasks)} clips ({len(items)} 词 + 例句), cached "
          f"{len(tasks)-len(todo)}, synth {len(todo)}")
    failed = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for t, ok in zip(todo, ex.map(gen_one, todo)):
            if not ok:
                failed.append(t[0])
    if failed:
        print(f"[!] {len(failed)} 条发音失败（课件照常生成）：{', '.join(failed)}")

    keep = {cache_path(lid, txt).name for lid, txt in tasks}
    removed = 0
    for p in AUDIO_DIR.glob("*.mp3"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    if removed:
        print(f"      cleaned {removed} stale mp3(s)")

    audio = {}
    for lid, txt in tasks:
        p = cache_path(lid, txt)
        if p.exists() and p.stat().st_size > MIN_MP3:
            audio[lid] = "data:audio/mpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    return audio


# ────────────────────────────────────────────── nadeshiko media

def fetch_media(url, logical_id, ext, min_size):
    NADE_DIR.mkdir(exist_ok=True)
    path = NADE_DIR / f"{logical_id}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.{ext}"
    if path.exists() and path.stat().st_size > min_size:
        return path.read_bytes()
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if len(data) > min_size:
                path.write_bytes(data)
                return data
        except Exception:
            pass
    return None


def gen_nade_media(items):
    tasks = []
    for it in items:
        for i, sc in enumerate(it.get("nadeshiko") or []):
            nid = f"{it['id']}-n{i}"
            if sc.get("audio"):
                tasks.append((nid, "a", sc["audio"], "mp3", 500))
            if sc.get("thumb"):
                tasks.append((nid, "t", sc["thumb"], "webp", 200))

    print(f"[2/4] nadeshiko: {len(tasks)} media files to fetch...")
    nade_audio, scenes = {}, {}
    failed = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_media, url, f"{nid}-{kind}", ext, minsize): (nid, kind)
                for nid, kind, url, ext, minsize in tasks}
        for fut in futs:
            nid, kind = futs[fut]
            data = fut.result()
            if data is None:
                failed.append(f"{nid}({'audio' if kind=='a' else 'thumb'})")
                continue
            if kind == "a":
                nade_audio[nid] = "data:audio/mpeg;base64," + base64.b64encode(data).decode()
            else:
                scenes[nid] = "data:image/webp;base64," + base64.b64encode(data).decode()
    if failed:
        print(f"  nade: {len(failed)} failed: {', '.join(failed)}")
    print(f"  nade: audio {len(nade_audio)}, thumbs {len(scenes)}")
    return nade_audio, scenes


# ────────────────────────────────────────────── auto quizzes

BANKS = [["recog", "📘 意味認識"], ["kanji", "🈶 漢字読み"], ["suffix", "🔤 語尾判別"],
         ["fill", "✍️ 例文填空"], ["listen", "🎧 聴解判別"]]
SUFFIX_OPTS = ["〜やか", "〜らか", "〜か（その他）", "〜しい"]
SUFFIX_IDX = {"yaka": 0, "raka": 1, "ka": 2, "shi": 3}


def build_questions(items, audio):
    rng = random.Random(SEED)
    kanji_pool = []
    for it in items:
        for k in it.get("kanji") or []:
            if k.get("c") and k.get("read"):
                kanji_pool.append((k["c"], k["read"]))

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    def others(own_id, field, n=3):
        cand = [x[field] for x in items if x["id"] != own_id and x.get(field)]
        rng.shuffle(cand)
        return cand[:n]

    for it in items:
        iid = it["id"]

        # 📘 意味認識
        add("recog", f"{iid}:recog", type="choice",
            q=f"「{it['word']}」（{it['read']}）の意味は？",
            opts=[it["mean"]] + others(iid, "mean"), ans=0,
            exp=f'{it["word"]}＝{it["mean"]}<br>🧭 {it["core"]}<br>例：{it["examples"][0]["jp"]}')

        # 🈶 漢字読み
        k = (it.get("kanji") or [{}])[0]
        if k.get("c") and k.get("read"):
            dist = []
            for c, r in kanji_pool:
                if c != k["c"] and r != k["read"] and r not in dist:
                    dist.append(r)
            rng.shuffle(dist)
            if len(dist) >= 3:
                add("kanji", f"{iid}:kanji", type="choice",
                    q=f"「{it['word']}」の「{k['c']}」の読みは？",
                    opts=[k["read"]] + dist[:3], ans=0,
                    exp=f'{k["c"]}（{k["read"]}）＝{k.get("mean","")}<br>語例：{it["word"]}')

        # 🔤 語尾判別
        add("suffix", f"{iid}:suffix", type="choice",
            q=f"「{it['word']}」の語尾タイプは？",
            opts=SUFFIX_OPTS, ans=SUFFIX_IDX.get(it["group"], 2),
            exp=f'{it["word"]} は「{SUFFIX_OPTS[SUFFIX_IDX.get(it["group"],2)]}」型（'
                f'{it.get("read","")}）')

        # ✍️ 例文填空
        form = it.get("form") or it["word"]
        ex0 = it["examples"][0]
        if form in ex0["jp"]:
            blanked = ex0["jp"].replace(form, "（　）", 1)
            add("fill", f"{iid}:fill", type="choice",
                q=f'{blanked}<br><span class="hint">（　）に入る語は？　{ex0["cn"]}</span>',
                opts=[it["word"]] + others(iid, "word"), ans=0,
                exp=f'完整句：{ex0["jp"]}<br>{ex0["cn"]}<br>'
                    f'「{it["word"]}」＝{it["mean"]}')

        # 🎧 聴解判別
        if f"{iid}-e0" in audio:
            add("listen", f"{iid}:listen", type="listen",
                aid=f"{iid}-e0",
                q="🎧 听音频：句子里用的是哪个词？",
                opts=[it["word"]] + others(iid, "word"), ans=0,
                exp=f'原句：{ex0["jp"]}<br>{ex0["cn"]}')

    return qs


# ────────────────────────────────────────────── html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>〜やか・らか 形容動詞図鑑</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#b45309,#e67e22 45%,#0f766e);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:6px;margin:-18px 0 16px;position:relative;z-index:2;flex-wrap:wrap}
nav button{flex:1;min-width:0;border:none;border-radius:12px;padding:12px 2px;font-size:13px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:2;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.intro{background:linear-gradient(135deg,#fff3e0,#e6f7f4)}
.intro h2{font-size:17px;margin-bottom:8px;color:#b45309}
.intro p{font-size:14px;line-height:1.8}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#b45309}
/* group hub */
.hub-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:8px}
.hub{border-radius:14px;padding:14px 12px;text-align:center;cursor:pointer;transition:.15s;
border:2px solid transparent;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.hub:hover{transform:translateY(-3px);border-color:var(--c)}
.hub .em{font-size:28px}
.hub .nm{font-weight:800;font-size:15px;margin:6px 0 4px;color:var(--c)}
.hub .sem{font-size:12px;color:var(--sub);line-height:1.5}
.hub .cnt{font-size:11px;color:var(--c);font-weight:700;margin-top:6px}
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#6a3de8)}
.mini .em{font-size:24px}
.mini .nm{font-weight:800;font-size:15px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
/* detail */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#6a3de8)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.mini-btn{width:26px;height:26px;font-size:12px}
.core-box{background:#eef6ff;border:1px solid #c0d8f0;border-radius:12px;padding:10px 13px;font-size:13.5px;line-height:1.7;margin:10px 0}
.core-box b{color:#2563a8}
.krow{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}
.kchip{background:#fff8e6;border:1px solid #f0d060;border-radius:12px;padding:8px 11px;font-size:13px;line-height:1.5}
.kchip .kc{font-size:19px;font-weight:800;color:#b8860b;margin-right:6px}
.kchip .kr{color:var(--ink);font-weight:700;font-size:12.5px}
.kchip .km{display:block;color:var(--sub);font-size:11.5px;margin-top:2px}
.rchips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 0}
.rchip{display:inline-block;background:#f1f3f8;color:var(--sub);border-radius:99px;
padding:2px 10px;font-size:12px;font-weight:600}
.linkchip{display:inline-block;background:#ede7f6;color:#5e35b1;border-radius:99px;
padding:3px 11px;font-size:12.5px;font-weight:700;cursor:pointer;margin:8px 6px 0 0;
border:1px solid #d8c8ee}
.linkchip:hover{background:#5e35b1;color:#fff}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);
padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* contrast */
.contrast-card{border-left:4px solid var(--c,#6a3de8);padding-left:14px;margin-bottom:18px}
.contrast-card h4{font-size:15px;color:var(--c);margin-bottom:6px}
.contrast-card p{font-size:13.5px;line-height:1.7}
/* quiz */
.q{font-size:16.5px;line-height:1.75;margin-bottom:14px}
.opt{display:block;width:100%;text-align:left;padding:12px 14px;margin:8px 0;font-size:15.5px;
border-radius:12px;border:2px solid var(--line);background:#fff;cursor:pointer;line-height:1.5}
.opt:hover:not(:disabled){border-color:var(--acc)}
.opt.right{border-color:var(--ok);background:var(--okbg)}
.opt.wrong{border-color:var(--ng);background:var(--ngbg)}
.opt:disabled{cursor:default;opacity:.92}
.exp{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:14px;line-height:1.65}
.exp.ok{background:var(--okbg);color:var(--ok)} .exp.ng{background:var(--ngbg);color:var(--ng)}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px;z-index:5}
.bar .wrap{display:flex;justify-content:space-between;align-items:center}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
.legend{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
.legend span{font-size:12px;border-radius:99px;padding:3px 10px;background:#f1f3f8;color:var(--sub);font-weight:600}
/* nadeshiko scene */
.nade-card{background:#faf5ff;border:1px solid #e0d0f0;border-radius:12px;padding:12px;margin:12px 0}
.nade-card .nade-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.nade-card .nade-media{font-weight:700;color:#5e35b1;font-size:13px}
.nade-card .nade-ep{font-size:11.5px;color:var(--sub)}
.nade-card .nade-jp{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:15px;line-height:2}
.nade-card .nade-jp ruby rt{font-size:.52em;color:var(--sub)}
.nade-card .nade-en{font-size:12.5px;color:var(--sub);margin-top:3px;font-style:italic}
.nade-card .nade-cn{font-size:13px;color:var(--ink);margin-top:2px}
.nade-card .nade-row{display:flex;gap:10px;align-items:flex-start}
.nade-card .nade-thumb{width:80px;height:50px;border-radius:8px;object-fit:cover;flex:none}
.nade-card a{color:#5e35b1;font-size:11.5px;text-decoration:none}
.nade-card a:hover{text-decoration:underline}
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;margin-left:6px;vertical-align:middle}
.src-nade{background:#ede7f6;color:#5e35b1}
</style>
</head>
<body>
<header><div class="wrap">
<h1>〜やか・らか 形容動詞図鑑</h1>
<div class="kana">「生きた状態」と「澄んだ状態」——接尾辞でつながるナ形容詞の一族</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">离线可用 · 点击🔊播放发音</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const NADE_AUDIO=__NADE_AUDIO__;
const SCENES=__SCENES__;
const GROUPS=__GROUPS__;
const ITEMS=__ITEMS__;
const CONTRASTS=__CONTRASTS__;
const KANJI_FAMILIES=__KANJI_FAMILIES__;
const SHI_FRAMES=__SHI_FRAMES__;
const BANKS=__BANKS__;
let QS=__QS__;
const $=s=>document.querySelector(s);
let curAudio=null,curBtn=null;
function play(id,btn){
  const src=AUDIO[id]||NADE_AUDIO[id];if(!src)return;
  if(curAudio){curAudio.pause();curAudio.currentTime=0;}
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('playing'));
  curAudio=new Audio(src);curBtn=btn||null;
  if(curBtn){curBtn.classList.add('playing');curAudio.onended=()=>curBtn.classList.remove('playing');}
  curAudio.play();
}
function rowHTML(sid,s){
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}
function nadeHTML(sc,iid,idx){
  const nid=`${iid}-n${idx}`;
  const hasAudio=!!NADE_AUDIO[nid];
  const playBtn=hasAudio?`<button class="btn" style="width:30px;height:30px;font-size:13px" onclick="play('${nid}',this)">▶</button>`:"";
  const thumb=SCENES[nid]
    ?`<img class="nade-thumb" src="${SCENES[nid]}" alt="">`
    :(sc.thumb?`<img class="nade-thumb" src="${sc.thumb}" alt="" onerror="this.style.display='none'">`:"");
  return `<div class="nade-card">
    <div class="nade-hdr"><span class="src src-nade">Nadeshiko</span>${playBtn}
      <span class="nade-media">${sc.media}</span><span class="nade-ep">${sc.ep} @ ${sc.at}</span></div>
    <div class="nade-row">${thumb}
      <div>
        <div class="nade-jp">${sc.jp}</div>
        <div class="nade-en">${sc.en}</div>
        ${sc.cn?`<div class="nade-cn">${sc.cn}</div>`:""}
        <a href="${sc.url}" target="_blank">nadeshiko.co ↗</a>
      </div>
    </div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["map","🗺️ 体系図"],["zukan","📖 図鑑"],["contrast","🔍 対比"],["quiz","🎯 クイズ"]];
let tab="map";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goItem(iid){
  goTab('zukan');
  setTimeout(()=>{const el=document.getElementById('n-'+iid);
    if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);
}
function groupOf(iid){const it=ITEMS.find(x=>x.id===iid);return it?GROUPS.find(g=>g.id===it.group):null;}

/* ---------- map ---------- */
function renderMap(){
  let h=`<div class="card intro"><h2>一つの接尾辞、二つの気配——それが「〜やか／〜らか」</h2>
  <p>和語のナ形容詞には、語幹末に <b>〜やか</b>（賑やか・爽やか）・<b>〜らか</b>（明らか・清らか）を
  持つ一群があります。どちらも「状態・様態」を作る接尾辞的要素で、活用はごく普通の形容動詞
  （〜な＋名詞／〜に＋動詞／〜だ）。<b>やか＝にぎわい・華やぎ・柔らかな動き</b>、
  <b>らか＝澄み・明るさ・平らかさ</b>、と覚えると語感が掴めます。</p>
  <p style="margin-top:8px">同根の <b>〜しい</b> 形（華やか↔華々しい、清らか↔清々しい）も一緒に覚えると、
  同じ漢字の「状態」と「感じ方」がペアで定着します。</p>
  <div class="steps">
    <div><b>接辞のレシピ</b><br>漢字語幹＋やか／らか（＝汉字写词干、接尾辞写假名）</div>
    <div><b>判定法</b><br>普通のナ形：連体「〜な」・連用「〜に」——特殊活用はない</div>
    <div><b>非能産</b><br>新語は作れない。漢語ではなく和語の語幹に付く閉じた一群</div>
  </div>
  <div class="legend"><span>✨ やか＝生気・華やぎ</span><span>🌿 らか＝澄み・平らか</span>
  <span>🪨 か（その他）＝基本状態</span><span>💠 しい＝そう感じる</span></div>
  </div>`;

  h+=`<div class="card"><h3 class="sec" style="margin-top:0">四つの一族</h3><div class="hub-grid">`;
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(x=>x.group===g.id);
    h+=`<div class="hub" style="--c:${g.color}" onclick="goTab('zukan')">
      <div class="em">${g.emoji}</div><div class="nm">${g.name}</div>
      <div class="sem">${g.note}</div><div class="cnt">${members.length} 語</div></div>`;
  });
  h+=`</div></div>`;

  GROUPS.forEach(g=>{
    const members=ITEMS.filter(x=>x.group===g.id);
    h+=`<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.emoji} ${g.name}</h3><span>${g.semantics}</span></div>
      <div class="mini-wrap">`+members.map(n=>`
        <button class="mini" style="--g:${g.color}" onclick="goItem('${n.id}')">
          <div class="em">${n.emoji}</div>
          <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
          <div class="im">${n.mean}</div></button>`).join("")+`</div></div>`;
  });

  h+=`<div class="card"><h3 class="sec" style="margin-top:0">🈶 漢字ファミリー：一字から語族ごと覚える</h3>
  <div class="krow">`+KANJI_FAMILIES.map(k=>
    `<div class="kchip"><span class="kc">${k.c}</span><span class="kr">${k.read}</span>
     <span class="km">${k.mean}｜${k.family}</span></div>`).join("")+`</div></div>`;

  h+=`<div class="card"><h3 class="sec" style="margin-top:0">🔗 同源対応の四つの型——ナ形 ↔ 〜しい</h3>
  <p class="hint">只有「同一词根＋不同接尾辞」才算形态同源；同汉字≠同词源（如 厳か／厳しい）。下面四种接尾辞，
  是把 〜やか／らか／か 与 〜しい 家族串起来的真正框架。</p>
  <div class="steps" style="flex-wrap:wrap">`+SHI_FRAMES.map(f=>
    `<div style="border-left:4px solid ${f.color};min-width:210px">
      <b style="color:${f.color}">${f.name}</b><br>${f.desc}
      <div style="margin-top:6px">`+f.pairs.map(p=>`<span class="rchip" style="margin:2px 4px 2px 0">${p}</span>`).join("")+`</div>
    </div>`).join("")+`</div></div>`;
  $("#main").innerHTML=h;
}

/* ---------- zukan ---------- */
function renderZukan(){
  let h="";
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.emoji} ${g.name} <span style="color:var(--sub);font-weight:400">— ${g.semantics}</span></h3>`;
    members.forEach(n=>{
      const iid=n.id;
      const fr=n.frame?SHI_FRAMES.find(f=>f.id===n.frame):null;
      let links="";
      if(n.shi){const s=ITEMS.find(x=>x.id===n.shi);
        if(s)links+=`<span class="linkchip" onclick="goItem('${s.id}')">同根の〜しい形 → ${s.emoji} ${s.word}（${s.read}）</span>`;}
      if(n.base){const b=ITEMS.find(x=>x.id===n.base);
        if(b)links+=`<span class="linkchip" onclick="goItem('${b.id}')">もとの形容動詞 → ${b.emoji} ${b.word}（${b.read}）</span>`;}
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta"><code>読 ${n.read}</code>
          ${AUDIO[`${iid}-w`]?`<button class="btn mini-btn" title="单词发音" onclick="play('${iid}-w',this)">▶</button>`:""}
          <span class="rchip" style="background:${g.color}22;color:${g.color}">${g.name}</span>
          ${fr?`<span class="rchip" style="background:${fr.color}22;color:${fr.color}">${fr.name}</span>`:""}
        </div>
        <div class="core-box"><b>🧭 核心意象</b>　${n.core}</div>
        <div class="krow">`+(n.kanji||[]).map(k=>
          `<div class="kchip"><span class="kc">${k.c}</span><span class="kr">${k.read}</span>
           <span class="km">${k.mean}</span></div>`).join("")+`</div>
        ${(n.related||[]).length?`<div class="rchips">`+(n.related||[]).map(r=>`<span class="rchip">${r}</span>`).join("")+`</div>`:""}
        ${links}
        <h3 class="sec">例文</h3>
        ${(n.examples||[]).map((ex,i)=>rowHTML(`${iid}-e${i}`,ex)).join("")}
        ${(n.nadeshiko||[]).map((sc,j)=>nadeHTML(sc,iid,j)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- contrast ---------- */
function renderContrast(){
  let h=`<div class="card intro"><h2>対比で覚える：同じ「状態」でも気配が違う</h2>
    <p>似た語同士を並べ、違いの軸（にぎわい／澄み／褒贬／日常・文語／形動・イ形）で切ると、
    語感の地図が一気に見えてきます。</p></div>`;
  CONTRASTS.forEach(sec=>{
    h+=`<div class="card contrast-card" style="--c:${sec.color}"><h4>${sec.title}</h4>`;
    sec.items.forEach(([name,mech,ex])=>{
      h+=`<div style="margin:8px 0;padding:8px 12px;background:#f8f9fc;border-radius:10px">
        <div style="font-weight:700;color:${sec.color}">${name}</div>
        <div style="font-size:13px;color:var(--sub);margin:3px 0">${mech}</div>
        <div style="font-size:12.5px;color:var(--gold)">例：${ex}</div>
      </div>`;
    });
    h+=`</div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("yakaraka-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("yakaraka-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("yakaraka-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:420px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 錯題重練 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">答对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">錯題本是空的——答錯的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">五层题库：意味→漢字→語尾→填空→聴解 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交錯 · 全量隨機<br><span style="font-size:12px;color:var(--sub)">跨题型交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空錯題本？')){localStorage.removeItem('yakaraka-wrong');renderQuizTab();}">🗑️ 清空錯題本</button>`:""}
    </div>`;
    $("#barinfo").textContent="离线可用 · 点击🔊播放发音";
    return;
  }
  renderQ();
}
function startQuiz(m){
  mode=m;
  if(m==="mix")pool=QS.slice();
  else if(m==="wrong"){const w=wrongBook();pool=QS.filter(q=>w[q.ref]);}
  else pool=QS.filter(q=>q.bank===m);
  order=shuffle(pool.map((_,x)=>x));
  qi=0;correct=0;answered=false;
  renderQ();
}
function curQ(){return pool[order[qi]];}
function renderQ(){
  const q=curQ();updateScore();
  let body="";
  if(q.type==="listen"){
    body=AUDIO[q.aid]?`<div style="text-align:center;margin:6px 0 14px">
      <button class="btn" style="width:56px;height:56px;font-size:24px;margin:auto" onclick="play('${q.aid}',this)">🔊</button>
      <div class="hint">可反復点擊重听</div></div>`
      :`<div class="hint" style="text-align:center;margin-bottom:10px">（這條发音还没生成）</div>`;
  }
  const optHTML=shuffle(q.opts.map((t,i)=>({t,ok:i===q.ans})))
    .map(o=>`<button class="opt" data-ok="${o.ok?1:0}" onclick="pick(this)">${o.t}</button>`).join("");
  showNext(false);
  const label=mode==="mix"?"混合":mode==="wrong"?"錯題本":(BANKS.find(([k])=>k===mode)||["",""])[1];
  $("#main").innerHTML=`<div class="card">
    <div class="hint">第 ${qi+1} 题 / 共 ${order.length} 题 · ${label}</div>
    <div class="q">${q.q}</div>${body}${optHTML}
    <div id="fb"></div></div>`;
}
function showNext(v){const b=$("#next");b.style.display=v?"inline-block":"none";b.disabled=!v;}
function pick(btn){
  if(answered)return;answered=true;
  const q=curQ();
  const ok=btn.dataset.ok==="1";
  if(ok)correct++;else addWrong(q.ref);
  if(ok&&mode==="wrong")delWrong(q.ref);
  document.querySelectorAll(".opt").forEach(b=>{b.disabled=true;if(b.dataset.ok==="1")b.classList.add("right");});
  if(!ok)btn.classList.add("wrong");
  $("#fb").innerHTML=`<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！"} ${q.exp||""}</div>`;
  showNext(true);updateScore();
  window.scrollTo(0,document.body.scrollHeight);
}
function nextQ(){
  answered=false;qi++;
  if(qi>=order.length)finish();else renderQ();
}
function finish(){
  showNext(false);
  const total=order.length,pct=Math.round(correct/total*100);
  const msg=pct===100?"🏆 完璧！語族ごと頭に入った！":pct>=70?"👍 かなりいい！錯题趁热打铁":"📖 図鑑タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">別的關卡選一選</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正確率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){mode=null;renderQuizTab();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="map")renderMap();
  else if(tab==="zukan")renderZukan();
  else if(tab==="contrast")renderContrast();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, groups, items, contrasts, kanji_families, shi_frames = load_data()
    n = len(items)
    n_kanji = len({k["c"] for it in items for k in (it.get("kanji") or [])})
    n_ex = sum(len(it.get("examples") or []) for it in items)
    n_nade = sum(len(it.get("nadeshiko") or []) for it in items)
    level_counts = {}
    for it in items:
        level_counts[it["level"]] = level_counts.get(it["level"], 0) + 1
    level_str = "・".join(f"{k}×{v}" for k, v in sorted(level_counts.items()))

    print(f"[1/4] audio: TTS for {n} words + {n_ex} examples...")
    audio = gen_audio(items)

    nade_audio, scenes = gen_nade_media(items)

    print("[3/4] generating quiz banks...")
    qs = build_questions(items, audio)
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k in dict.fromkeys(q["bank"] for q in qs)}
    for k, label in BANKS:
        print(f"      {label}: {counts.get(k,0)} 問")
    print(f"      合计: {sum(counts.values())} 問")

    display = copy.deepcopy(items)
    furi = 0
    if HAS_KAKASI:
        for it in display:
            it["word"] = add_furigana(it["word"])
            furi += 1
            for ex in it.get("examples") or []:
                ex["jp"] = add_furigana(ex["jp"])
                furi += 1
        print(f"      furigana: applied to {furi} strings")

    tags = (f"<span>{n} 語（4 語族）</span>"
            f"<span>{n_kanji} 漢字</span>"
            f"<span>{level_str}</span>"
            f"<span>{sum(counts.values())} 問 5 層題庫</span>"
            f"<span>{n_nade} Nadeshiko 原声</span>")

    print("[4/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__NADE_AUDIO__", j(nade_audio))
            .replace("__SCENES__", j(scenes))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(display))
            .replace("__CONTRASTS__", j(contrasts))
            .replace("__KANJI_FAMILIES__", j(kanji_families))
            .replace("__SHI_FRAMES__", j(shi_frames))
            .replace("__BANKS__", j(BANKS))
            .replace("__QS__", j(qs)))
    left = [m for m in ("__TAGS__", "__AUDIO__", "__NADE_AUDIO__", "__SCENES__",
                        "__GROUPS__", "__ITEMS__", "__CONTRASTS__", "__KANJI_FAMILIES__",
                        "__SHI_FRAMES__", "__BANKS__", "__QS__")
            if m in html]
    if left:
        sys.exit(f"[!] 模板占位符未替换：{left}")
    OUT.write_text(html, encoding="utf-8")
    print(f"      wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
