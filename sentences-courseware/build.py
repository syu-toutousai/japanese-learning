#!/usr/bin/env python3
"""Build the self-contained 「例文 跟読トレーナー」 courseware HTML.

用法：往 sentences.md 丢一句新句子 → 运行本脚本 → index.html 自动长出
卡片、2×2×2 训练音轨与题库。翻译/重点词/Nadeshiko 台词写在 sentences.json。

音频：edge-tts 日语神经网络语音（女 Nanami / 男 Keita）× 三档语速，
按文本哈希缓存到 audio/；训练音轨由 ffmpeg 拼接（慢×2+留白+中×2+留白+常×2+留白），
同样内容寻址缓存。某条合成失败只跳过该条，不影响整体构建。
"""

import base64
import copy
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
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
SENT_MD = ROOT / "sentences.md"
DATA = ROOT / "sentences.json"
AUDIO_DIR = ROOT / "audio"
NADE_DIR = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICES = {"f": "ja-JP-NanamiNeural", "m": "ja-JP-KeitaNeural"}
RATES = {"slow": "-40%", "med": "-18%", "norm": "+0%"}
SPEED_ORDER = ["slow", "med", "norm"]
GAP_REP = 0.8    # 同一档两遍之间的短停
SHADOW = 1.0     # 档末跟读留白 = 该档音频时长 × SHADOW
TAIL = 1.5       # 全句收尾空白
MIN_MP3 = 300
SEED = 20260928


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def sha(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


# ────────────────────────────────────────────── furigana (ruby)

_KANJI = r"\u4e00-\u9fff\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")

# 词干覆盖：pykakasi 语境误读时，先整词替换（rt 只盖汉字词干，假名留给 pykakasi）
_READING_OVERRIDES = [
    ("腐敗", "ふはい"),
    ("防止", "ぼうし"),
    ("粗", "あら"),
    ("粘膜", "ねんまく"),
    ("保護", "ほご"),
    ("医学", "いがく"),
    ("進歩", "しんぽ"),
    ("寿命", "じゅみょう"),
    ("戒", "いまし"),
    ("誓約書", "せいやくしょ"),
    ("署名", "しょめい"),
    ("遺跡", "いせき"),
    ("根底", "こんてい"),
    ("覆", "くつがえ"),
    ("返上", "へんじょう"),
    ("用件", "ようけん"),
    ("交錯", "こうさく"),
    ("期待", "きたい"),
    ("人々", "ひとびと"),
    ("落ち着", "おちつ"),
]


def _furi(text):
    """Wrap kanji tokens in <ruby>…<rt>reading</rt></ruby> using pykakasi."""
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


def add_furigana(text):
    """Convert nadeshiko-style 漢字(かな) to ruby, then add readings to
    remaining kanji via pykakasi. Falls back to plain text if unavailable."""
    if not HAS_KAKASI:
        return text
    if not _KANJI_RE.search(text):
        return text

    matches = []
    for phrase, reading in _READING_OVERRIDES:
        for m in re.finditer(re.escape(phrase), text):
            matches.append((m.start(), m.end(), "override", phrase, reading))
    for m in _INLINE.finditer(text):
        matches.append((m.start(), m.end(), "inline", m.group(1), m.group(2)))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    clean = []
    for m in matches:
        if not clean or m[0] >= clean[-1][1]:
            clean.append(m)

    out = []
    pos = 0
    for start, end, kind, kanji, reading in clean:
        out.append(_furi(text[pos:start]))
        out.append(f"<ruby>{kanji}<rt>{reading}</rt></ruby>")
        pos = end
    out.append(_furi(text[pos:]))
    return "".join(out)


# ────────────────────────────────────────────── load & merge

def load_data():
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] sentences.json 不是合法 JSON：{e}")
    except FileNotFoundError:
        sys.exit("[!] 找不到 sentences.json")

    meta = raw.get("meta", {})
    jitems = raw.get("items", [])
    if not isinstance(jitems, list):
        sys.exit("[!] sentences.json 的 items 必须是列表")

    try:
        md_text = SENT_MD.read_text(encoding="utf-8")
    except FileNotFoundError:
        sys.exit("[!] 找不到 sentences.md（句子清单）")

    md_sents = []
    for line in md_text.splitlines():
        s = line.strip().lstrip("\ufeff")
        if s and s not in md_sents:
            md_sents.append(s)
    if not md_sents:
        sys.exit("[!] sentences.md 里没有句子")

    by_jp = {}
    for it in jitems:
        jp = (it.get("jp") or "").strip()
        if not jp:
            sys.exit("[!] sentences.json 有条目缺少 jp")
        if jp in by_jp:
            sys.exit(f"[!] sentences.json 有重复句子：{jp}")
        by_jp[jp] = it

    items = []
    used = set()
    auto = []
    for s in md_sents:
        it = by_jp.get(s)
        if it is None:
            it = {"id": "x" + sha(s), "jp": s, "cn": "",
                  "words": [], "nadeshiko": [], "auto": True}
            auto.append(s)
        items.append(it)
        used.add(s)

    orphans = [jp for jp in by_jp if jp not in used]
    if orphans:
        print(f"[!] sentences.json 里 {len(orphans)} 条不在 sentences.md（已忽略）：")
        for jp in orphans:
            print("   -", jp)
    if auto:
        print(f"[!] {len(auto)} 条 sentences.md 句子还没有翻译/词汇条目，先出素文卡片：")
        for s in auto:
            print("   +", s)

    errors = []
    seen = set()
    for it in items:
        iid = it.get("id", "")
        if not iid:
            errors.append(f"条目缺少 id：{it.get('jp')}")
        elif iid in seen:
            errors.append(f"条目 id 重复：{iid}")
        seen.add(iid)
        if not it.get("jp"):
            errors.append(f"「{iid}」缺少 jp")
        for w in it.get("words") or []:
            for k in ("w", "read", "mean"):
                if not w.get(k):
                    errors.append(f"「{iid}」词汇条缺 {k}：{w}")
    if errors:
        print("[!] sentences.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, items


# ────────────────────────────────────────────── tts + ffmpeg mixing

def clip_path(iid, v, speed, text):
    return AUDIO_DIR / f"{iid}-{v}-{speed}-{sha(text, RATES[speed])}.mp3"


def train_path(iid, v, text):
    return AUDIO_DIR / (
        f"{iid}-{v}-train-"
        f"{sha(text, RATES['slow'], RATES['med'], RATES['norm'], str(GAP_REP), str(SHADOW), str(TAIL))}.mp3"
    )


def tts_one(task):
    path, text, rate, voice = task
    if path.exists() and path.stat().st_size > MIN_MP3:
        return True
    for _ in range(3):
        r = subprocess.run(
            ["edge-tts", "--voice", voice, f"--rate={rate}",
             "--text", text, "--write-media", str(path)],
            capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > MIN_MP3:
            return True
    if path.exists():
        path.unlink()
    return False


def dur_sec(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def mix_train(out_path, parts, silences):
    """Stitch 慢×2 / 中×2 / 常×2 + pauses in a single ffmpeg call.

    parts: [slow, med, norm] mp3 paths
    silences: [(gap, shadow) × 3] seconds
    """
    seq = []
    for i, p in enumerate(parts):
        gap, shadow = silences[i]
        seq += [("f", p), ("s", gap), ("f", p), ("s", shadow)]

    cmd = ["ffmpeg", "-y", "-v", "error"]
    for kind, val in seq:
        if kind == "f":
            cmd += ["-i", str(val)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{val:.3f}",
                    "-i", "anullsrc=r=24000:cl=mono"]
    n = len(seq)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    cmd += ["-filter_complex", filt, "-map", "[a]",
            "-c:a", "libmp3lame", "-b:a", "48k", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        if out_path.exists():
            out_path.unlink()
        print(f"      ffmpeg mix failed: {out_path.name}: {r.stderr.strip()[:200]}")
        return False
    return out_path.exists() and out_path.stat().st_size > MIN_MP3


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def gen_audio(items):
    AUDIO_DIR.mkdir(exist_ok=True)

    tasks = []
    for it in items:
        for v in VOICES:
            for sp in SPEED_ORDER:
                tasks.append((clip_path(it["id"], v, sp, it["jp"]),
                              it["jp"], RATES[sp], VOICES[v]))
    todo = [t for t in tasks
            if not (t[0].exists() and t[0].stat().st_size > MIN_MP3)]
    print(f"[1/4] tts: {len(tasks)} clips ({len(items)}句 × 2声 × 3速), "
          f"cached {len(tasks)-len(todo)}, synth {len(todo)}")
    failed = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for t, ok in zip(todo, ex.map(tts_one, todo)):
            if not ok:
                failed.append(t[0].name)

    mix_tasks = []
    for it in items:
        for v in VOICES:
            parts = [clip_path(it["id"], v, sp, it["jp"]) for sp in SPEED_ORDER]
            if all(p.exists() and p.stat().st_size > MIN_MP3 for p in parts):
                mix_tasks.append((it, v, parts))
    print(f"      mixing {len(mix_tasks)} training tracks (慢×2+留白+中×2+留白+常×2+留白)...")
    train_meta = {}

    def mix_one(task):
        it, v, parts = task
        tp = train_path(it["id"], v, it["jp"])
        ds, dm, dn = (dur_sec(p) for p in parts)
        sil = [(GAP_REP, ds * SHADOW), (GAP_REP, dm * SHADOW),
               (GAP_REP, dn * SHADOW + TAIL)]
        ok = mix_train(tp, parts, sil)
        # 中/常速起点（供前端高亮当前档位）
        med = 3 * ds + GAP_REP
        norm = med + 3 * dm + GAP_REP
        return task, ok, med, norm

    with ThreadPoolExecutor(max_workers=4) as ex:
        for (it, v, _), ok, med, norm in ex.map(mix_one, mix_tasks):
            if not ok:
                failed.append(train_path(it["id"], v, it["jp"]).name)
                continue
            train_meta[f"{it['id']}-{v}"] = {
                "medStart": round(med, 3), "normStart": round(norm, 3)}

    if failed:
        print(f"[!] {len(failed)} 条音频失败（课件照常生成，缺的部分没有播放键）：")
        for name in failed:
            print("   -", name)

    # stale cleanup
    keep = {t[0].name for t in tasks}
    keep |= {train_path(it["id"], v, it["jp"]).name
             for it in items for v in VOICES}
    removed = 0
    for p in AUDIO_DIR.glob("*.mp3"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    if removed:
        print(f"      cleaned {removed} stale mp3(s)")

    audio = {}
    for it in items:
        for v in VOICES:
            for sp in SPEED_ORDER:
                p = clip_path(it["id"], v, sp, it["jp"])
                if p.exists() and p.stat().st_size > MIN_MP3:
                    audio[f"{it['id']}-{v}-{sp}"] = data_uri(p, "audio/mpeg")
            tp = train_path(it["id"], v, it["jp"])
            if tp.exists() and tp.stat().st_size > MIN_MP3:
                audio[f"{it['id']}-{v}-train"] = data_uri(tp, "audio/mpeg")
    return audio, train_meta


# ────────────────────────────────────────────── nadeshiko media

def fetch_media(url, logical_id, ext, min_size):
    NADE_DIR.mkdir(exist_ok=True)
    path = NADE_DIR / f"{logical_id}-{sha(url)}.{ext}"
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

    print(f"  nade: {len(tasks)} media files to fetch...")
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

def build_questions(items, audio):
    rng = random.Random(SEED)
    words = []
    for it in items:
        for w in it.get("words") or []:
            words.append({**w, "sid": it["id"]})

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    def dist_words(own, n=3):
        cand = [w for w in words if w["w"] != own]
        rng.shuffle(cand)
        return cand[:n]

    for it in items:
        iid = it["id"]

        # 📘 語彙認識：词 → 释义
        for w in it.get("words") or []:
            ds = dist_words(w["w"])
            if len(ds) < 3:
                continue
            add("recog", f"{iid}:recog-{w['w']}", type="choice",
                q=f"「{w['w']}」の意味は？",
                opts=[w["mean"]] + [d["mean"] for d in ds], ans=0,
                exp=f"{w['w']}（{w['read']}）＝{w['mean']}<br>"
                    f"例：{it['jp']}<br>{it.get('cn','')}")

        # ✍️ 語彙填空：原句挖空选回
        for w in it.get("words") or []:
            form = w.get("form") or w["w"]
            if form not in it["jp"]:
                continue
            ds = dist_words(w["w"])
            if len(ds) < 3:
                continue
            blanked = it["jp"].replace(form, "（　）", 1)
            add("fill", f"{iid}:fill-{w['w']}", type="choice",
                q=f'{blanked}<br><span class="hint">（　）に入る語は？　{it.get("cn","")}</span>',
                opts=[w["w"]] + [d["w"] for d in ds], ans=0,
                exp=f'完整句：{it["jp"]}<br>{it.get("cn","")}<br>'
                    f'「{w["w"]}」（{w["read"]}）＝{w["mean"]}')

        # 🎧 聴解判別：听常速选句子
        if f"{iid}-f-norm" in audio:
            others = [x["jp"] for x in items if x["id"] != iid]
            rng.shuffle(others)
            if others:
                add("listen", f"{iid}:listen", type="listen",
                    aid=f"{iid}-f-norm", aidM=f"{iid}-m-norm",
                    q="🎧 听音频：播放的是哪一句？",
                    opts=[it["jp"]] + others[:3], ans=0,
                    exp=f'原句：{it["jp"]}<br>{it.get("cn","")}')

    return qs


# ────────────────────────────────────────────── html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>例文 跟読トレーナー</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#0f766e,#2f4bdb);color:#fff;padding:26px 20px 20px}
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
.jp{font-size:17px;line-height:2.1;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.intro{background:linear-gradient(135deg,#e6f7f4,#eef1ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#0f766e}
.intro p{font-size:14px;line-height:1.75}
/* trainer cards */
.scard{padding:16px 18px}
.scard.active{box-shadow:0 0 0 2px var(--acc),0 2px 10px rgba(30,40,90,.1)}
.shead{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:8px;flex-wrap:wrap}
.snum{font-weight:800;color:var(--acc);font-size:14px;font-family:ui-monospace,monospace}
.phases{display:flex;gap:6px;flex-wrap:wrap}
.ph{font-size:11.5px;border-radius:99px;padding:3px 9px;background:#f1f3f8;color:var(--sub);font-weight:600}
.ph.on{background:var(--acc);color:#fff}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 2px}
.tbtn{border:none;background:var(--acc);color:#fff;border-radius:12px;padding:9px 16px;
font-size:14px;font-weight:700;cursor:pointer}
.tbtn.playing{animation:pulse 1s infinite}
.sbtn{border:2px solid var(--line);background:#fff;border-radius:12px;padding:8px 12px;
font-size:13.5px;cursor:pointer;font-weight:600;color:var(--ink)}
.sbtn:hover{border-color:var(--acc);color:var(--acc)}
.sbtn.playing{border-color:var(--acc);background:var(--acc2);color:var(--acc);animation:pulse 1.2s infinite}
.wlist{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;margin:12px 0 2px}
.wcard{background:#f8f9fc;border:1px solid var(--line);border-radius:12px;padding:9px 12px}
.wcard .ww{font-weight:800;font-size:15.5px}
.wcard .ww ruby rt{font-size:.5em;color:var(--sub)}
.wcard .wpos{font-size:10.5px;color:#fff;background:var(--acc);border-radius:99px;
padding:1px 7px;margin-left:7px;vertical-align:middle}
.wcard .wmean{font-size:13px;color:var(--sub);margin-top:4px;line-height:1.55}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);
padding-top:9px;line-height:1.65}
.toolrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
.vbtn{border:2px solid var(--line);background:#fff;border-radius:99px;padding:7px 14px;
font-size:13.5px;font-weight:700;cursor:pointer;color:var(--sub)}
.vbtn.on{border-color:var(--acc);background:var(--acc2);color:var(--acc)}
/* blind mode */
body.blind .blurable{filter:blur(8px);cursor:pointer;user-select:none;transition:filter .18s}
body.blind .card.revealed .blurable{filter:none;cursor:text;user-select:auto}
/* source chips */
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;
margin-left:6px;vertical-align:middle}
.src-moji{background:#e8f5e9;color:#2e7d32}
.src-nade{background:#ede7f6;color:#5e35b1}
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
.bar .wrap{display:flex;justify-content:space-between;align-items:center;gap:8px}
.score{font-weight:700;color:var(--acc)}
.next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.jump{border:none;background:var(--acc2);color:var(--acc);border-radius:10px;
padding:10px 16px;font-size:14px;font-weight:700;cursor:pointer}
.seqbar{display:flex;gap:8px;align-items:center}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
@media(max-width:560px){.bar .wrap{flex-wrap:wrap}.barinfo{display:none}}
</style>
</head>
<body>
<header><div class="wrap">
<h1>例文 跟読トレーナー</h1>
<div class="kana">2×2×2 —— 慢・中・常、それぞれ二度ずつ、あいだに跟読の余白</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint barinfo" id="barinfo">离线可用 · 全部音声内嵌</span>
<span class="seqbar" id="seqbar" style="display:none">
  <button class="next" id="seqbtn" onclick="seqToggle()">▶ 連続再生</button>
  <button class="jump" onclick="seqNext()">次へ ⏭</button>
</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const NADE_AUDIO=__NADE_AUDIO__;
const SCENES=__SCENES__;
const ITEMS=__ITEMS__;
const TRAIN_META=__TRAIN_META__;
const BANKS=__BANKS__;
const META=__META__;
let QS=__QS__;
const $=s=>document.querySelector(s);
const SPEED_LABEL={slow:"🐢 慢",med:"🚶 中",norm:"🏃 常"};
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
let voice=lsGet("sent-voice","f"); if(voice!=="f"&&voice!=="m")voice="f";
let blind=lsGet("sent-blind",false);

/* ---------- player ---------- */
let curAudio=null,curBtn=null;
let seq=false,seqPaused=false,seqCur=null;
function hardStop(){
  if(curAudio){curAudio.onended=null;curAudio.ontimeupdate=null;try{curAudio.pause()}catch(e){}curAudio=null;}
  if(curBtn){curBtn.classList.remove("playing");curBtn=null;}
  document.querySelectorAll(".ph.on").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".card.active").forEach(c=>c.classList.remove("active"));
}
function play(id,btn){
  const src=AUDIO[id]||NADE_AUDIO[id];if(!src)return;
  seq=false;seqPaused=false;seqCur=null;hardStop();renderBar();
  curAudio=new Audio(src);curBtn=btn||null;
  if(btn)btn.classList.add("playing");
  curAudio.onended=()=>{if(btn)btn.classList.remove("playing");curAudio=null;curBtn=null;};
  curAudio.play();
}
function playTrain(iid,btn,keepSeq){
  const tid=`${iid}-${voice}-train`;const src=AUDIO[tid];if(!src)return;
  if(!keepSeq){seq=false;seqPaused=false;seqCur=null;}
  hardStop();seqCur=iid;
  const card=document.getElementById("c-"+iid);
  const tm=TRAIN_META[`${iid}-${voice}`]||{};
  const phases=card?card.querySelectorAll(".ph"):[];
  curAudio=new Audio(src);curBtn=btn||null;
  if(btn)btn.classList.add("playing");
  if(card)card.classList.add("active");
  const upd=()=>{
    if(!curAudio||!tm.medStart)return;
    const t=curAudio.currentTime;
    let p="slow";
    if(tm.normStart&&t>=tm.normStart)p="norm";else if(t>=tm.medStart)p="med";
    phases.forEach(x=>x.classList.toggle("on",x.dataset.p===p));
  };
  curAudio.ontimeupdate=upd;
  curAudio.onended=()=>{
    if(btn)btn.classList.remove("playing");
    if(card){card.classList.remove("active");phases.forEach(x=>x.classList.remove("on"));}
    curAudio=null;curBtn=null;
    if(seq){
      const i=ITEMS.findIndex(x=>x.id===seqCur);
      if(i>=0&&i+1<ITEMS.length){
        const nx=ITEMS[i+1];
        setTimeout(()=>playTrain(nx.id,document.getElementById("tb-"+nx.id),true),350);
      }else{seq=false;renderBar();}
    }else renderBar();
  };
  if(card)card.scrollIntoView({behavior:"smooth",block:"center"});
  curAudio.play();upd();renderBar();
}
function playSpeed(iid,speed,btn){
  const src=AUDIO[`${iid}-${voice}-${speed}`];if(!src)return;
  seq=false;seqPaused=false;seqCur=null;hardStop();renderBar();
  const card=document.getElementById("c-"+iid);if(card)card.classList.add("active");
  curAudio=new Audio(src);curBtn=btn;
  if(btn)btn.classList.add("playing");
  curAudio.onended=()=>{if(btn)btn.classList.remove("playing");
    if(card)card.classList.remove("active");curAudio=null;curBtn=null;};
  curAudio.play();
}
function seqToggle(){
  if(!seq){
    if(!ITEMS.length)return;
    seq=true;seqPaused=false;
    const first=ITEMS[0];
    playTrain(first.id,document.getElementById("tb-"+first.id),true);
  }else if(!seqPaused){
    if(curAudio){curAudio.pause();seqPaused=true;renderBar();}
  }else{
    seqPaused=false;
    if(curAudio){curAudio.play();renderBar();}
    else{
      const i=seqCur?ITEMS.findIndex(x=>x.id===seqCur):-1;
      const nx=ITEMS[(i+1)%ITEMS.length];seq=true;
      playTrain(nx.id,document.getElementById("tb-"+nx.id),true);
    }
  }
}
function seqNext(){
  const i=seqCur?ITEMS.findIndex(x=>x.id===seqCur):-1;
  const nx=ITEMS[(i+1)%ITEMS.length];seq=true;seqPaused=false;
  playTrain(nx.id,document.getElementById("tb-"+nx.id),true);
}
function renderBar(){
  const sb=$("#seqbar"),info=$("#barinfo");
  if(tab==="train"){
    sb.style.display="flex";
    if(seq){
      const i=Math.max(0,ITEMS.findIndex(x=>x.id===seqCur));
      $("#seqbtn").textContent=seqPaused?"▶ 再開":"⏸ 一時停止";
      info.textContent=`連続再生中 ${i+1}/${ITEMS.length}${seqPaused?"（一時停止）":""} · ${voice==="m"?"👨 ケイタ":"👩 ナナミ"}`;
    }else{
      $("#seqbtn").textContent="▶ 連続再生";
      info.textContent="点击各句 ▶ 跟読訓練，或 ▶ 連続再生 全 10 句通し";
    }
  }else{
    sb.style.display="none";
    if(tab!=="quiz")info.textContent="离线可用 · 全部音声内嵌";
  }
}

/* ---------- shared render helpers ---------- */
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
function wordsHTML(it){
  if(!(it.words||[]).length)return "";
  return `<div class="wlist blurable">`+it.words.map(w=>
    `<div class="wcard"><span class="ww"><ruby>${w.w}<rt>${w.read}</rt></ruby></span>
     <span class="wpos">${w.pos||""}</span><div class="wmean">${w.mean}</div></div>`).join("")+`</div>`;
}

/* ---------- tabs ---------- */
const TABS=[["train","🎧 跟読訓練"],["list","📝 例文一覧"],["quiz","🎯 クイズ"]];
let tab="train";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){
  tab=k;seq=false;seqPaused=false;seqCur=null;hardStop();
  renderNav();render();window.scrollTo(0,0);
}
function setVoice(v){
  if(v===voice)return;
  voice=v;lsSet("sent-voice",v);
  seq=false;seqPaused=false;seqCur=null;hardStop();
  render();
}
function toggleBlind(){
  blind=!blind;lsSet("sent-blind",blind);
  document.body.classList.toggle("blind",blind);
  render();
}

/* ---------- train ---------- */
function renderTrain(){
  let h=`<div class="card intro"><h2>2×2×2 跟読メソッド</h2>
    <p>每句自动播放 <b>慢速×2 → 跟读留白 → 中速×2 → 跟读留白 → 常速×2 → 跟读留白</b>。
    留白长度≈该档句长，正好够你开口跟读一遍。慢速 -40% / 中速 -18% / 常速 原速。</p>
    <div class="toolrow">
      <span class="vbtn ${voice==="f"?"on":""}" onclick="setVoice('f')">👩 ナナミ（女声）</span>
      <span class="vbtn ${voice==="m"?"on":""}" onclick="setVoice('m')">👨 ケイタ（男声）</span>
      <span class="vbtn ${blind?"on":""}" onclick="toggleBlind()">🙈 盲聴モード${blind?" ON":""}</span>
    </div>
    ${blind?`<div class="hint" style="margin-top:8px">盲聴中：文字被模糊，点卡片文字即可逐句揭示。</div>`:""}
  </div>`;
  ITEMS.forEach((it,i)=>{
    h+=`<div class="card scard" id="c-${it.id}">
      <div class="shead"><span class="snum">${String(i+1).padStart(2,"0")}</span>
        <span class="phases">
          <span class="ph" data-p="slow">🐢 慢×2</span>
          <span class="ph" data-p="med">🚶 中×2</span>
          <span class="ph" data-p="norm">🏃 常×2</span>
        </span></div>
      <div class="jp blurable">${it.jp}</div>
      ${it.cn?`<div class="cn blurable">${it.cn}</div>`:""}
      <div class="controls">
        <button class="tbtn" id="tb-${it.id}" onclick="playTrain('${it.id}',this)">▶ 跟読訓練</button>
        <button class="sbtn" onclick="playSpeed('${it.id}','slow',this)">🐢 慢</button>
        <button class="sbtn" onclick="playSpeed('${it.id}','med',this)">🚶 中</button>
        <button class="sbtn" onclick="playSpeed('${it.id}','norm',this)">🏃 常</button>
      </div>
      ${wordsHTML(it)}
      ${(it.nadeshiko||[]).map((sc,j)=>nadeHTML(sc,it.id,j)).join("")}
      ${it.note?`<div class="note">💡 ${it.note}</div>`:""}
    </div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- list ---------- */
function renderList(){
  let h=`<div class="card intro"><h2>例文一覧</h2>
    <p>全 ${ITEMS.length} 句。点击 ▶ 播放该句的 2×2×2 训练音轨（含跟读留白）。</p></div>`;
  ITEMS.forEach((it,i)=>{
    h+=`<div class="card" style="padding:12px 14px">
      <div style="display:flex;gap:10px;align-items:flex-start">
        <span class="snum" style="flex:none;padding-top:8px">${String(i+1).padStart(2,"0")}</span>
        <div style="flex:1">
          <div class="jp" style="font-size:15.5px">${it.jp}</div>
          ${it.cn?`<div class="cn">${it.cn}</div>`:""}
        </div>
        <button class="btn" onclick="play('${it.id}-${voice}-train',this)">▶</button>
      </div></div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function wrongBook(){return lsGet("sent-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("sent-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("sent-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 錯題重練 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">答对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">錯題本是空的——答錯的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 sentences.json 自动生成 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交錯 · 全量隨機<br><span style="font-size:12px;color:var(--sub)">跨题型交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空錯題本？')){localStorage.removeItem('sent-wrong');renderQuizTab();}">🗑️ 清空錯題本</button>`:""}
    </div>`;
    $("#barinfo").textContent="离线可用 · 全部音声内嵌";
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
    const aid=(voice==="m"&&q.aidM)?q.aidM:q.aid;
    body=AUDIO[aid]?`<div style="text-align:center;margin:6px 0 14px">
      <button class="btn" style="width:56px;height:56px;font-size:24px;margin:auto" onclick="play('${aid}',this)">🔊</button>
      <div class="hint">可反復点擊重听 · 当前${voice==="m"?"👨 ケイタ":"👩 ナナミ"}</div></div>`
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
  $("#barinfo").textContent="离线可用 · 答错自动进錯題本";
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
  const msg=pct===100?"🏆 完璧！耳朵已经记住这些句子了！":pct>=70?"👍 かなりいい！錯题趁热打铁":"📖 回跟読訓練再磨几遍耳朵";
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
  if(tab==="train")renderTrain();
  else if(tab==="list")renderList();
  else renderQuizTab();
  renderBar();
}
$("#main").addEventListener("click",e=>{
  if(!blind)return;
  const b=e.target.closest(".blurable");if(!b)return;
  const card=b.closest(".card");if(card)card.classList.toggle("revealed");
});
document.body.classList.toggle("blind",blind);
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, items = load_data()
    n = len(items)
    n_words = sum(len(it.get("words") or []) for it in items)
    n_nade = sum(len(it.get("nadeshiko") or []) for it in items)

    audio, train_meta = gen_audio(items)

    print("[2/4] nadeshiko: fetching scene audio + thumbs...")
    nade_audio, scenes = gen_nade_media(items)

    print("[3/4] generating quiz banks...")
    qs = build_questions(items, audio)
    banks_meta = [["listen", "🎧 聴解判别"], ["fill", "✍️ 語彙填空"],
                  ["recog", "📘 語彙認識"]]
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k in dict.fromkeys(q["bank"] for q in qs)}
    for k, label in banks_meta:
        print(f"      {label}: {counts.get(k,0)} 問")
    print(f"      合计: {sum(counts.values())} 問")

    display = copy.deepcopy(items)
    furi = 0
    if HAS_KAKASI:
        for it in display:
            it["jp"] = add_furigana(it["jp"])
            furi += 1
            for sc in it.get("nadeshiko") or []:
                sc["jp"] = add_furigana(sc["jp"])
                furi += 1
        print(f"      furigana: applied to {furi} sentences")

    tags = (f"<span>{n} 例文</span>"
            f"<span>慢/中/常 ×2 + 跟読留白</span>"
            f"<span>👩 Nanami + 👨 Keita</span>"
            f"<span>{n_words} 語彙カード</span>"
            f"<span>{n_nade} Nadeshiko 原声</span>")

    print("[4/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__NADE_AUDIO__", j(nade_audio))
            .replace("__SCENES__", j(scenes))
            .replace("__ITEMS__", j(display))
            .replace("__TRAIN_META__", j(train_meta))
            .replace("__BANKS__", j(banks_meta))
            .replace("__QS__", j(qs))
            .replace("__META__", j(meta)))
    OUT.write_text(html, encoding="utf-8")
    left = [m for m in ("__TAGS__", "__AUDIO__", "__NADE_AUDIO__", "__SCENES__",
                        "__ITEMS__", "__TRAIN_META__", "__BANKS__", "__QS__", "__META__")
            if m in html]
    if left:
        sys.exit(f"[!] 模板占位符未替换：{left}")
    print(f"      wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
