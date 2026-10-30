#!/usr/bin/env python3
"""Build the self-contained 「日本語 記号と文字の全景」courseware HTML.

用法：往 kigou.json 里随手加一条记号 → 运行本脚本 → index.html 自动长出
卡片、读音、例文发音和题库。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了句子会自动重录；某条合成失败只跳过该条发音，不影响整体构建。
"""

import base64
import copy
import hashlib
import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "kigou.json"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20261030
MIN_MP3 = 300
BANK_META = [
    ["read",    "🔤 読み"],
    ["meaning", "📌 意味"],
    ["input",   "⌨️ 入力"],
    ["origin",  "🏛️ 由来"],
    ["fill",    "✍️ 用例填空"],
    ["listen",  "🎧 聴解"],
    ["custom",  "⭐ 自作題"],
]


def j(obj):
    """json for embedding inside <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] kigou.json 不是合法 JSON：{e}")
    errors = []
    groups = data.get("groups")
    items = data.get("items")
    guide = data.get("inputGuide") or {}
    if not isinstance(groups, list) or not groups:
        errors.append("缺少非空的 groups 列表")
        groups = []
    if not isinstance(items, list):
        errors.append("缺少 items 列表")
        items = []

    gids = set()
    for g in groups:
        gid = g.get("id", "")
        if not gid:
            errors.append(f"group 缺少 id：{g}")
        elif gid in gids:
            errors.append(f"group id 重复：{gid}")
        gids.add(gid)
        for key in ("name", "color"):
            if not g.get(key):
                errors.append(f"group「{gid}」缺少 {key}")

    seen = set()
    for it in items:
        iid = it.get("id", "")
        label = iid or it.get("symbol", "?")
        if not iid:
            errors.append(f"条目「{label}」缺少 id")
        elif iid in seen:
            errors.append(f"条目 id 重复：{iid}")
        seen.add(iid)
        if iid and not all(c.isalnum() or c in "-_" for c in iid):
            errors.append(f"条目 id「{iid}」只能用字母数字-_（要嵌进 HTML 属性）")
        if it.get("group") not in gids:
            errors.append(f"「{label}」group「{it.get('group')}」不在 groups 里")
        for key in ("symbol", "word", "read", "level", "meaning", "origin", "usage"):
            if not it.get(key):
                errors.append(f"「{label}」缺少 {key}")
        exs = it.get("examples") or []
        if not exs:
            errors.append(f"「{label}」至少需要一条 examples 例句")
        for i, ex in enumerate(exs):
            if not ex.get("jp"):
                errors.append(f"「{label}」第{i+1}条例句缺 jp")
            if not ex.get("cn"):
                errors.append(f"「{label}」第{i+1}条例句缺 cn")

    if errors:
        print("[!] kigou.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return groups, items, guide


# ---------------------------------------------------------------- furigana (ruby)

try:
    import pykakasi
    _kks = pykakasi.kakasi()
    HAS_KAKASI = True
except ImportError:
    HAS_KAKASI = False

_KANJI = r"\u4e00-\u9fff\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")

# phrase overrides for context-sensitive readings pykakasi gets wrong
_READING_OVERRIDES = [
    ("今日", "きょう"),
    ("昨日", "きのう"),
    ("一人", "ひとり"),
    ("二人", "ふたり"),
    ("大人", "おとな"),
    ("上手", "じょうず"),
    ("下手", "へた"),
    ("一日中", "いちにちじゅう"),
    ("一日", "いちにち"),
    ("正月", "しょうがつ"),
    ("今年", "ことし"),
    ("去年", "きょねん"),
    ("今度", "こんど"),
    ("本当", "ほんとう"),
    ("一生", "いっしょう"),
    ("一杯", "いっぱい"),
    ("十分", "じゅうぶん"),
    ("分かる", "わかる"),
    ("行って", "いって"),
    ("一日中", "いちにちじゅう"),
]


def _furi(text):
    """Wrap kanji in <ruby>…<rt>reading</rt></ruby> using pykakasi."""
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
    """Convert 漢字(かな) to ruby, then add readings to remaining kanji via
    pykakasi. Only affects the display copy."""
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


# ---------------------------------------------------------------- tts

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
    return False


def gen_audio(items):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []                       # (logical_id, text)
    for it in items:
        tasks.append((it["id"], it["read"]))
        for i, ex in enumerate(it.get("examples") or []):
            tasks.append((f"{it['id']}-e{i}", ex.get("speech") or ex["jp"]))

    todo = [(lid, txt) for lid, txt in tasks
            if not cache_path(lid, txt).exists()]
    print(f"[1/4] audio: {len(tasks)} clips total, {len(tasks)-len(todo)} cached, "
          f"{len(todo)} to synthesize...")
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
    failed = [lid for lid, ok in results.items() if not ok]
    if failed:
        print(f"[!] {len(failed)} 条发音合成失败（课件照常生成，只是这几处没有播放键）：")
        for lid in failed:
            print("   -", lid)

    # 清理不再被引用的旧缓存（改句子后遗留的 mp3）
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


# ---------------------------------------------------------------- auto quizzes

def build_questions(groups, items, audio):
    rng = random.Random(SEED)

    def disp(it):
        return it["symbol"]

    def distractors(it, field, n=3):
        own = it[field]
        same = [x[field] for x in items
                if x["group"] == it["group"] and x["id"] != it["id"] and x[field] != own]
        rest = [x[field] for x in items
                if x["group"] != it["group"] and x[field] != own]
        rng.shuffle(same)
        rng.shuffle(rest)
        out = []
        for cand in same + rest:
            if len(out) >= n:
                break
            if cand not in out and cand != own:
                out.append(cand)
        return out

    def unique(it, field):
        return sum(1 for x in items if x[field] == it[field]) == 1

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for it in items:
        iid = it["id"]
        sym = disp(it)
        note = it.get("note", "")

        # 🔤 読み
        if unique(it, "read"):
            add("read", f"{iid}:read", type="choice",
                q=f'「{it["word"]}」の読みは？',
                opts=[it["read"]] + distractors(it, "read"), ans=0,
                exp=f'{sym}＝{it["read"]}　{it["meaning"]}'
                    + (f'<br>💡 {note}' if note else ""))

        # 📌 意味
        add("meaning", f"{iid}:meaning", type="choice",
            q=f'「{sym}」（{it["read"]}）が表すのは？',
            opts=[it["meaning"]] + distractors(it, "meaning"), ans=0,
            exp=f'{sym}（{it["read"]}）：{it["meaning"]}<br>🏛️ {it["origin"]}'
                + (f'<br>💡 {note}' if note else ""))

        # ⌨️ 入力
        if it.get("input") and unique(it, "input"):
            add("input", f"{iid}:input", type="choice",
                q=f'「{sym}」（{it["read"]}）を出すときの入力は？',
                opts=[it["input"]] + distractors(it, "input"), ans=0,
                exp=f'{sym} ← 「{it["input"]}」と入力して変換'
                    + (f'<br>💡 {note}' if note else ""))

        # 🏛️ 由来
        add("origin", f"{iid}:origin", type="choice",
            q=f'「{sym}」（{it["read"]}）の成り立ちは？',
            opts=[it["origin"]] + distractors(it, "origin"), ans=0,
            exp=f'{sym}：{it["origin"]}<br>📌 {it["meaning"]}')

        # ✍️ 用例填空（一句一问）
        for i, ex in enumerate(it.get("examples") or []):
            if sym and sym in ex["jp"]:
                blanked = ex["jp"].replace(sym, "（　）", 1)
                add("fill", f"{iid}:fill-{i}", type="choice",
                    q=f'{blanked}<br><span class="hint">（　）に入る記号は？</span>',
                    opts=[sym] + distractors(it, "symbol"), ans=0,
                    exp=f'完整句子：{ex["jp"]}<br>{ex["cn"]}')
                break

        # 🎧 聴解（读音 → 记号）
        if iid in audio and unique(it, "read"):
            add("listen", f"{iid}:listen", type="listen", aid=iid,
                q="🎧 听读音：对应哪个记号？",
                opts=[sym] + distractors(it, "symbol"), ans=0,
                exp=f'读音：{it["read"]}　记号：{sym}<br>{it["meaning"]}')

        # ⭐ 自作题原样收录
        for n, cq in enumerate(it.get("quizzes") or []):
            q = dict(cq)
            aid = q.get("aid")
            if isinstance(aid, int):
                q["aid"] = f"{iid}-e{aid}"
            q.setdefault("exp", "")
            add("custom", f"{iid}:custom-{n}", **q)

    return qs


# ---------------------------------------------------------------- html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>日本語 記号と文字の全景 ・ 読む・使う・入力する</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#1f2937,#92400e);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:900px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 16px;position:relative;z-index:2}
nav button{flex:1;border:none;border-radius:12px;padding:12px 2px;font-size:14px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:1.7;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
/* intro */
.intro{background:linear-gradient(135deg,#fdf6e3,#fffaf0)}
.intro h2{font-size:17px;margin-bottom:8px;color:#92400e}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#92400e}
/* group & mini cards */
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-note{font-size:12.5px;color:var(--sub);line-height:1.65;margin:8px 2px 0}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#92400e)}
.mini .sym{font-size:32px;line-height:1.3;font-family:"Hiragino Mincho ProN","Yu Mincho","MS Mincho","Noto Serif CJK JP",serif;color:var(--g,#92400e)}
.mini .nm{font-weight:800;font-size:15px;margin:4px 0 1px}
.mini .rd{font-size:11.5px;color:var(--sub)}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55;margin-top:2px}
/* detail cards */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#92400e)}
.noun h2{font-size:19px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.noun h2 .jl{margin-left:auto;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.bigsym{font-family:"Hiragino Mincho ProN","Yu Mincho","MS Mincho","Noto Serif CJK JP",serif;
font-size:38px;line-height:1;color:var(--g,#92400e)}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.mini-btn{width:26px;height:26px;font-size:12px}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.origin{background:#fffaf0;border-left:4px solid var(--gold);border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.7;margin:2px 0 8px}
.usebox{background:#f0f9ff;border-left:4px solid #0369a1;border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.7;margin:0 0 10px}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* input guide */
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:8px 0 16px}
th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}
th{color:var(--sub);font-size:12px;font-weight:700;background:#f8f9fc}
td.sym{font-size:28px;line-height:1.7;white-space:nowrap;font-family:"Hiragino Mincho ProN","Yu Mincho","MS Mincho","Noto Serif CJK JP",serif}
td.examples{font-family:"Hiragino Mincho ProN","Yu Mincho","MS Mincho","Noto Serif CJK JP",serif;font-size:17px;letter-spacing:.02em;line-height:2}
td code,code.inline{background:#f2f4fa;border:1px solid var(--line);border-radius:6px;padding:1px 6px;font-size:12px}
.imebox{background:#f0f9ff;border-left:4px solid #0369a1;border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.75;margin:10px 0}
.imebox b{color:#0369a1}
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
.judgebtns{display:flex;gap:12px}
.judgebtns .opt{flex:1;text-align:center;font-size:18px}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px;z-index:5}
.bar .wrap{display:flex;justify-content:space-between;align-items:center}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
</style>
</head>
<body>
<header><div class="wrap">
<h1>日本語 記号と文字の全景</h1>
<div class="kana">きごうともじのぜんけい ／ 読む・使う・入力する —— ことばの化石と暗号 🔣</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">离线可用 · 点击🔊播放读音</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const GROUPS=__GROUPS__;
const ITEMS=__ITEMS__;
const GUIDE=__GUIDE__;
const BANKS=__BANKS__;
let QS=__QS__;
const $=s=>document.querySelector(s);
let curAudio=null,curBtn=null;
function play(id,btn){
  const src=AUDIO[id];if(!src)return;
  if(curAudio){curAudio.pause();curAudio.currentTime=0;}
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('playing'));
  curAudio=new Audio(src);curBtn=btn||null;
  if(curBtn){curBtn.classList.add('playing');curAudio.onended=()=>curBtn.classList.remove('playing');}
  curAudio.play();
}
function item(id){return ITEMS.find(x=>x.id===id);}
function rowHTML(iid,i){
  const n=item(iid), ex=n.examples[i], sid=`${iid}-e${i}`;
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${ex.jp}</div><div class="cn">${ex.cn}</div></div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["list","🗺️ 体系"],["detail","📖 図鑑"],["input","⌨️ 入力"],["quiz","🎯 クイズ"]];
let tab="list";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- list ---------- */
function shortMean(m){return m.split(/[：:；;，,（(]/)[0];}
function renderList(){
  let h=`<div class="card intro"><h2>记号是语言的化石层 🔣</h2>
  <p>假名、国字、地图记号、组文字、叠字号、输入法——它们不是零散的冷知识，而是日语在
  <b>书写空间、印刷成本、行政分类、外来语压力</b>下长出来的器官。这里按 6 个系统收 47 个记号：
  每个记号给「读音・意味・由来・用法・输入」，并配读音与例句发音、7 层题库。</p>
  <div class="steps">
    <div><b>STEP 1 读</b><br>先看「体系」建立地图，再进「図鑑」逐枚读。</div>
    <div><b>STEP 2 用</b><br>「入力」查 IME 打法，把符号真正打进文档。</div>
    <div><b>STEP 3 验</b><br>「クイズ」用读音・意味・由来・用例四路检验。</div>
  </div></div>`;
  h+=GROUPS.map(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return "";
    return `<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.name}</h3><span>${members.length} 件</span></div>
    ${g.note?`<div class="grp-note">${g.note}</div>`:""}
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="sym">${n.symbol}</div>
        ${n.word!==n.symbol?`<div class="nm">${n.word}</div>`:""}
        <div class="rd">${n.read}</div>
        <div class="im">${shortMean(n.meaning)}</div></button>`).join("")}</div></div>`;
  }).join("");
  $("#main").innerHTML=h;
}

/* ---------- detail ---------- */
function renderDetail(){
  let h="";
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.name}</h3>`;
    members.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2><span class="bigsym">${n.symbol}</span>${n.word!==n.symbol?` ${n.word}`:""}<span class="jl">${n.level}</span></h2>
        <div class="meta"><span class="chip" style="background:${g.color}22;color:${g.color}">${g.name}</span>
          <code>读作 ${n.read}</code>
          ${n.input?`<code>输入 ${n.input}</code>`:""}
          ${AUDIO[iid]?`<button class="btn mini-btn" title="听读音" onclick="play('${iid}',this)">▶</button>`:""}
        </div>
        <div class="meanbox">📌 <b>意味</b>　${n.meaning}</div>
        <div class="origin">🏛️ <b>由来</b>　${n.origin}</div>
        <div class="usebox">🛠️ <b>用法</b>　${n.usage}</div>
        ${(n.examples||[]).map((_,i)=>rowHTML(iid,i)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- input guide ---------- */
function renderInput(){
  const exact=ITEMS.filter(n=>n.input);
  let h=`<div class="card"><h2 style="font-size:19px;margin-bottom:8px">⌨️ 日本語入力で記号を出す</h2>
    <div class="imebox">${GUIDE.intro||""}</div>
    <h3 class="sec">① 統称入力（大分類から変換）</h3>
    <table><thead><tr><th>入力</th><th>統称</th><th>変換候補の例</th></tr></thead><tbody>`;
  (GUIDE.bulk||[]).forEach(b=>{
    h+=`<tr><td><code>${b.key}</code></td><td>${b.label}</td><td class="examples">${b.examples}</td></tr>`;
  });
  h+=`</tbody></table><h3 class="sec">② 個別入力（読みから直接）</h3>
    <table><thead><tr><th>記号</th><th>読み</th><th>入力</th><th>意味</th></tr></thead><tbody>`;
  exact.forEach(n=>{
    h+=`<tr><td class="sym">${n.symbol}</td><td>${n.read}</td><td><code>${n.input}</code></td><td>${shortMean(n.meaning)}</td></tr>`;
  });
  h+=`</tbody></table>`;
  if(GUIDE.note) h+=`<div class="hint">💡 ${GUIDE.note}</div>`;
  h+=`</div>`;
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("kigou-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("kigou-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("kigou-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 kigou.json 自动生成 · 记号越多题库越大 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨系统交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('kigou-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
    </div>`;
    return;
  }
  startQuiz(mode);
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
      <div class="hint">可反复点击重听</div></div>`
      :`<div class="hint" style="text-align:center;margin-bottom:10px">（这条发音还没生成）</div>`;
  }
  let optHTML="";
  if(q.opts){
    optHTML=shuffle(q.opts.map((t,i)=>({t,ok:i===q.ans})))
      .map(o=>`<button class="opt" data-ok="${o.ok?1:0}" onclick="pick(this)">${o.t}</button>`).join("");
  }else{
    const truthy=q.ans===true;
    optHTML=`<div class="judgebtns">
      <button class="opt" data-ok="${truthy?1:0}" onclick="pick(this)">⭕ 正しい</button>
      <button class="opt" data-ok="${truthy?0:1}" onclick="pick(this)">❌ 間違い</button></div>`;
  }
  showNext(false);
  const label=mode==="mix"?"混合":mode==="wrong"?"错题本":(BANKS.find(([k])=>k===mode)||["",""])[1];
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
  const msg=pct===100?"🏆 完璧！記号の森を制覇した！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 図鑑タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">别的关卡选一选</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正确率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){mode=null;render();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="list")renderList();
  else if(tab==="detail")renderDetail();
  else if(tab==="input")renderInput();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    groups, items, guide = load_data()

    # display 副本：例句全部 pykakasi 振假名（只作用展示，不动素文/出题/音频）
    display = copy.deepcopy(items)
    for it in display:
        for ex in it.get("examples") or []:
            ex["jp"] = add_furigana(ex["jp"])

    audio = gen_audio(items)
    print(f"[1/4] audio: {len(audio)} clips ready")

    qs = build_questions(groups, items, audio)

    tags = (f"<span>{len(items)} の記号</span>"
            f"<span>{len(groups)} 系統</span>"
            f"<span>{len(audio)} 音声</span>"
            f"<span>7 层题库</span>"
            f"<span>IME 入力付き</span>")
    banks_meta = [[k, l] for k, l in BANK_META]

    print("[2/4] generating quiz banks...")
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, l in BANK_META:
        print(f"      {l}: {counts[k]} 問")

    print("[3/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(display))
            .replace("__GUIDE__", j(guide))
            .replace("__BANKS__", j(banks_meta))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[4/4] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
