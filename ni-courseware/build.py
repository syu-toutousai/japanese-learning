#!/usr/bin/env python3
"""Build the self-contained 「断定の「に」完全体系」 courseware HTML.

用法：往 ni.json 里加一条语法点 → 运行本脚本 → index.html 自动长出
卡片、发音和题库。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了句子会自动重录；某条合成失败只跳过该条发音，不影响整体构建。

Nadeshiko 音频：从 CDN 下载并嵌入 base64，实现离线播放。
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
DATA = ROOT / "ni.json"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260909
MIN_MP3 = 300


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ────────────────────────────────────────────── furigana (ruby)

_KANJI = r"\u4e00-\u9fff\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")

# phrase overrides for context-sensitive readings pykakasi gets wrong
_READING_OVERRIDES = [
    ("時間が経つ", "じかんがたつ"),
    ("年を取る", "としをとる"),
    ("人によって", "ひとによって"),
    ("多くの人が", "おおくのひとが"),
    ("最も", "もっとも"),
    ("六時に", "ろくじに"),
    ("厳しく", "きびしく"),
    ("損を", "そんを"),
    ("小さい", "ちいさい"),
    ("今日は", "きょうは"),
    ("書き綴った", "かきつづった"),
    ("絵を描く", "えをかく"),
    ("描いた", "かいた"),
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
    """Convert nadeshiko-style 漢字(かな) to ruby, then add readings to
    remaining kanji via pykakasi. Falls back to plain text if unavailable."""
    if not HAS_KAKASI:
        return text
    if not _KANJI_RE.search(text):
        return text

    # collect all override-phrase / inline-annotation matches
    matches = []
    for phrase, reading in _READING_OVERRIDES:
        for m in re.finditer(re.escape(phrase), text):
            matches.append((m.start(), m.end(), "override", phrase, reading))
    for m in _INLINE.finditer(text):
        matches.append((m.start(), m.end(), "inline", m.group(1), m.group(2)))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    # drop overlapping (keep earliest start, longest on tie)
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


# ────────────────────────────────────────────── load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] ni.json 不是合法 JSON：{e}")
    errors = []
    meta = data.get("meta", {})
    groups = data.get("groups", [])
    items = data.get("items", [])

    if not isinstance(groups, list) or not groups:
        errors.append("缺少非空的 groups 列表")
    if not isinstance(items, list) or not items:
        errors.append("缺少非空的 items 列表")

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
        label = iid or it.get("word", "?")
        if not iid:
            errors.append(f"条目「{label}」缺少 id")
        elif iid in seen:
            errors.append(f"条目 id 重复：{iid}")
        seen.add(iid)
        if it.get("group") not in gids:
            errors.append(f"「{label}」group「{it.get('group')}」不在 groups 里")
        for key in ("word", "read", "level", "meaning", "engine", "blueprint"):
            if not it.get(key):
                errors.append(f"「{label}」缺少 {key}")
        exs = it.get("examples") or []
        if not exs:
            errors.append(f"「{label}」至少需要一条 examples 例句")
        for i, ex in enumerate(exs):
            if not ex.get("jp"):
                errors.append(f"「{label}」第{i+1}条例句缺 jp")

    if errors:
        print("[!] ni.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, groups, items


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
    return False


def gen_audio(items):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []
    for it in items:
        for i, ex in enumerate(it.get("examples") or []):
            tasks.append((f"{it['id']}-e{i}", ex["jp"]))

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


# ────────────────────────────────────────────── nadeshiko audio

NADE_CACHE = ROOT / "nade_audio"

def download_nade_audio(url, logical_id):
    """Download a Nadeshiko CDN mp3 and return base64 data URI."""
    NADE_CACHE.mkdir(exist_ok=True)
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    path = NADE_CACHE / f"{logical_id}-{h}.mp3"
    if path.exists() and path.stat().st_size > 500:
        return "data:audio/mpeg;base64," + base64.b64encode(path.read_bytes()).decode()
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) > 500:
                path.write_bytes(data)
                return "data:audio/mpeg;base64," + base64.b64encode(data).decode()
        except Exception:
            pass
    return None


def gen_nade_audio(items):
    tasks = []
    for it in items:
        for i, sc in enumerate(it.get("nadeshiko") or []):
            if sc.get("audio"):
                tasks.append((f"{it['id']}-n{i}", sc["audio"]))

    print(f"  nade: {len(tasks)} clips to download...")
    nade_audio = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = {}
        for lid, url in tasks:
            results[lid] = ex.submit(download_nade_audio, url, lid)
        for lid, fut in results.items():
            data = fut.result()
            if data:
                nade_audio[lid] = data
    downloaded = len(nade_audio)
    print(f"  nade: {downloaded}/{len(tasks)} clips cached")
    return nade_audio


# ────────────────────────────────────────────── auto quizzes

def build_questions(groups, items, audio):
    rng = random.Random(SEED)

    def distractors(it, field, n=3):
        own = it[field]
        candidates = [x[field] for x in items if x["id"] != it["id"] and x[field] != own]
        rng.shuffle(candidates)
        return candidates[:n]

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for it in items:
        iid = it["id"]

        # 📘 语法认识：这个语法什么意思
        add("recog", f"{iid}:recog", type="choice",
            q=f"「{it['word']}」表示什么？",
            opts=[it["meaning"]] + distractors(it, "meaning"), ans=0,
            exp=f'{it["word"]}（{it["read"]}）＝{it["meaning"]}<br>'
                f'🔧 Engine: {it["engine"]}<br>💡 {it["blueprint"]}')

        # 🔧 Engine拆解：识别に的角色
        engine_opts = [
            "既定事実を確定（锁定事实）",
            "情報源を断定（断定信息源）",
            "対象をピン留め（锁定目标）",
            "変化のパラメータを固定（固定变化参数）",
            "語幹を連用修飾（将词干变成连用修饰语）",
            "現実の座標に固定（钉入现实坐标）",
            "事態を述語に架橋（把整个事态变成状语）",
            "時間軸上の里程標を指定（锚定时间节点）",
        ]
        # pick the right one based on group
        engine_map = {
            "suru": 0, "yoru": 1, "vector": 7, "relate": 2, "progress": 3,
            "renyou": 4, "case": 5, "state": 6,
        }
        correct_engine = engine_map.get(it["group"], 0)
        add("engine", f"{iid}:engine", type="choice",
            q=f"「{it['word']}」中的「に」扮演什么角色？",
            opts=engine_opts, ans=correct_engine,
            exp=f'🔧 {it["engine"]}<br>💡 {it["blueprint"]}')

        # ✍️ 运用填空：例句挖空选回（裸の格助詞「に」はどこにでも出るので除外）
        for i, ex in enumerate(it.get("examples") or []):
            # try to find the pattern in the sentence
            pattern = it["read"].replace("〜", "")
            if it["group"] != "case" and pattern and pattern in ex["jp"]:
                blanked = ex["jp"].replace(pattern, "（　）", 1)
                distractor_words = [x["word"] for x in items
                                    if x["id"] != it["id"]]
                rng.shuffle(distractor_words)
                opts = [it["word"]] + distractor_words[:3]
                add("fill", f"{iid}:fill-{i}", type="choice",
                    q=f'{blanked}<br><span class="hint">（　）里填回哪个语法？</span>',
                    opts=opts, ans=0,
                    exp=f'完整句子：{ex["jp"]}<br>{ex["cn"]}')
                break

        # 🎧 聴解判别：听例句判断用了哪个语法
        exs = it.get("examples") or []
        if exs and f"{iid}-e0" in audio:
            distractor_words = [x["word"] for x in items if x["id"] != it["id"]]
            rng.shuffle(distractor_words)
            opts = [it["word"]] + distractor_words[:3]
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 听音频：句子里用了哪个语法？",
                opts=opts, ans=0,
                exp=f'原句：{exs[0]["jp"]}<br>{exs[0]["cn"]}')

        # ⭕ 判断正誤
        add("judge", f"{iid}:judge-jp", type="judge",
            q=f"「{it['word']}」の engine は：{it['engine']}",
            ans=True,
            exp=f'正解！{it["blueprint"]}')

    # 额外的判断题
    extra_judges = [
        ('「〜にしては」は「即使…也…」の意味。', False,
         '間違い。にしては＝「就…而言却…」。表示「即使…也…」的是にしても。'),
        ('「〜によって」は「因…而異」の意味がある。', True,
         '正解。によって三大义之一就是「因人而异」。'),
        ('「〜に先立って」は「〜に際して」より硬い。', False,
         '間違い。に際して更正式书面，常用于公告致辞。'),
        ('「〜に反して」は期待・予想をよく使う。', True,
         '正解。反して＝预期 vs 现实形成反差。'),
        ('「〜につれて」と「〜にともなって」は完全に同じ。', False,
         '間違い。にともなって更书面，更强调因果捆绑。'),
        ('「〜に対して」は「対…」と「…と反対」の両方の意味がある。', True,
         '正解。两大义：①动作承受对象 ②对比/相反。'),
        ('「〜にあって」の「あって」は古典の「ある」から来ている。', True,
         '正解。あって＝あり（存在）のて形。'),
        ('「〜にあたって」は「〜に際して」より主観的。', True,
         '正解。にあたって强调主观能动性。'),
    ]
    for i, (q, ans, exp) in enumerate(extra_judges):
        add("judge", f"extra-judge-{i}", type="judge", q=q, ans=ans, exp=exp)

    return qs


# ────────────────────────────────────────────── html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>「に」完全体系 · 三本柱</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#2c3e8f,#6a3de8);color:#fff;padding:26px 20px 20px}
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
.intro{background:linear-gradient(135deg,#eee8ff,#f5f0ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#5b3cc4}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#6a3de8}
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#6a3de8)}
.mini .em{font-size:26px}
.mini .nm{font-weight:800;font-size:15px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#6a3de8)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.mini-btn{width:26px;height:26px;font-size:12px}
.engine-box{background:#fff8e6;border:1px solid #f0d060;border-radius:12px;padding:10px 13px;font-size:13.5px;line-height:1.7;margin:10px 0}
.engine-box b{color:#b8860b}
.blueprint-box{background:#eef6ff;border:1px solid #c0d8f0;border-radius:12px;padding:10px 13px;font-size:13.5px;line-height:1.7;margin:10px 0}
.blueprint-box b{color:#2563a8}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* map hub-spoke */
.hub-map{text-align:center;padding:20px 10px}
.hub-center{display:inline-block;background:linear-gradient(135deg,#2c3e8f,#6a3de8);color:#fff;
border-radius:50%;width:100px;height:100px;line-height:100px;font-size:36px;font-weight:900;
box-shadow:0 4px 20px rgba(106,61,232,.35);margin-bottom:16px}
.spoke-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:8px}
.spoke{border-radius:14px;padding:14px 12px;text-align:center;cursor:pointer;transition:.15s;
border:2px solid transparent;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.spoke:hover{transform:translateY(-3px);border-color:var(--c)}
.spoke .em{font-size:28px}
.spoke .nm{font-weight:800;font-size:14px;margin:6px 0 4px;color:var(--c)}
.spoke .desc{font-size:12px;color:var(--sub);line-height:1.55}
.spoke .count{font-size:11px;color:var(--c);font-weight:700;margin-top:6px}
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
code.inline{background:#eceff7;border-radius:6px;padding:1px 7px;font-size:.92em}
/* source chips */
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;margin-left:6px;vertical-align:middle}
.src-moji{background:#e8f5e9;color:#2e7d32}
.src-nade{background:#ede7f6;color:#5e35b1}
/* nadeshiko scene */
.nade-card{background:#faf5ff;border:1px solid #e0d0f0;border-radius:12px;padding:12px;margin:10px 0}
.nade-card .nade-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px}
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
/* three pillars */
.pillar-card{border-left:5px solid var(--c,#6a3de8)}
.pillar-head{display:flex;align-items:center;gap:12px;border-radius:12px;padding:12px 16px;color:#fff;margin-bottom:12px}
.pillar-em{font-size:30px}
.pillar-name{font-size:16.5px;font-weight:800}
.pillar-sub{font-size:12px;opacity:.9}
.pillar-syntax{background:#f2f4fa;border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-size:13.5px;line-height:1.7;margin:8px 0}
.pillar-syntax b{color:var(--c)}
.pillar-mech{font-size:14px;line-height:1.75;margin:6px 0}
.pillar-ex{font-size:13px;line-height:2;margin:8px 0;color:var(--sub)}
.pillar-grp{border-left:3px solid;border-radius:10px;background:#fafbfe;padding:10px 12px;margin:10px 0}
.pillar-grp-h{font-weight:700;font-size:14px;margin-bottom:2px}
.pillar-grp-h span{font-size:11.5px;color:var(--sub);font-weight:400}
/* etymology */
.ety-timeline{position:relative;padding:10px 0 10px 28px;margin:12px 0}
.ety-timeline::before{content:'';position:absolute;left:12px;top:0;bottom:0;width:3px;
background:linear-gradient(180deg,#2c3e8f,#6a3de8,#b8860b);border-radius:2px}
.ety-era{position:relative;margin-bottom:14px}
.ety-era::before{content:'';position:absolute;left:-22px;top:6px;width:12px;height:12px;
background:var(--acc);border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.15)}
.ety-era .era-date{font-size:11.5px;color:var(--acc);font-weight:700}
.ety-era .era-form{font-size:16px;font-weight:800;color:var(--ink);margin:2px 0}
.ety-era .era-note{font-size:12.5px;color:var(--sub);line-height:1.55}
.ety-formula{background:linear-gradient(135deg,#fff8e6,#fff3d0);border:1px solid #f0d060;
border-radius:12px;padding:12px 16px;font-size:16px;font-weight:800;color:#b8860b;
text-align:center;margin:10px 0;letter-spacing:1px}
.ety-section{margin:14px 0}
.ety-section h4{font-size:14.5px;color:var(--acc);margin-bottom:6px}
.ety-section p{font-size:13.5px;line-height:1.75}
.ety-inflection{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;margin:8px 0}
.ety-inflection th{background:var(--acc);color:#fff;padding:8px 10px;text-align:left;font-weight:700}
.ety-inflection th:first-child{border-radius:10px 0 0 0}
.ety-inflection th:last-child{border-radius:0 10px 0 0}
.ety-inflection td{padding:8px 10px;border-bottom:1px solid var(--line)}
.ety-inflection tr:last-child td:first-child{border-radius:0 0 0 10px}
.ety-inflection tr:last-child td:last-child{border-radius:0 0 10px 0}
.ety-inflection .form-col{font-weight:700;color:var(--acc)}
.ety-inflection .kana-col{font-size:15px;font-weight:800;color:var(--ink)}
.ety-path{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:10px 0}
.ety-path-card{background:#f8f9fc;border-radius:12px;padding:12px;border:1px solid var(--line)}
.ety-path-card .path-from{font-size:12px;color:var(--sub);margin-bottom:4px}
.ety-path-card .path-via{font-size:11px;color:var(--gold);font-weight:700;margin-bottom:4px}
.ety-path-card .path-to{font-size:14px;font-weight:800;color:var(--acc);margin-bottom:6px}
.ety-path-card .path-desc{font-size:12.5px;color:var(--sub);line-height:1.55}
.ety-diffusion{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}
.ety-diff-item{background:#f8f9fc;border-radius:10px;padding:10px;text-align:center;border:1px solid var(--line)}
.ety-diff-item .diff-sense{font-weight:700;color:var(--ink);font-size:13px;margin-bottom:4px}
.ety-diff-item .diff-example{font-size:14px;color:var(--acc);margin-bottom:2px}
.ety-diff-item .diff-desc{font-size:11.5px;color:var(--sub)}
.ety-diag{margin:10px 0}
.ety-diag-row{display:flex;gap:8px;align-items:flex-start;margin:6px 0;padding:8px 10px;
background:#f8f9fc;border-radius:10px;border-left:3px solid var(--acc)}
.ety-diag-row .diag-test{font-size:13px;color:var(--ink);flex:1}
.ety-diag-row .diag-result{font-size:12px;color:var(--acc);font-weight:700;white-space:nowrap}
.ety-diag-row .diag-example{font-size:12px;color:var(--sub);font-style:italic}
</style>
</head>
<body>
<header><div class="wrap">
<h1>「に」完全体系 · 三本柱</h1>
<div class="kana">だんてい・かくじょし・じょうたいせつぞく —— にの全用法を貫く統一建築図 ⚡</div>
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
const GROUPS=__GROUPS__;
const LINES=__LINES__;
const ITEMS=__ITEMS__;
const BANKS=__BANKS__;
const ETYMOLOGY=__ETYMOLOGY__;
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
  const chip=s.src==="nadeshiko"?`<span class="src src-nade">Nadeshiko</span>`:`<span class="src src-moji">MOJi</span>`;
  return `<div class="row">${b}<div><div class="jp">${s.jp}${chip}</div><div class="cn">${s.cn}</div></div></div>`;
}
function nadeHTML(sc,iid,idx){
  const nid=`${iid}-n${idx}`;
  const hasAudio=!!NADE_AUDIO[nid];
  const playBtn=hasAudio?`<button class="btn" style="width:30px;height:30px;font-size:13px" onclick="play('${nid}',this)">▶</button>`:"";
  return `<div class="nade-card">
    <div class="nade-hdr"><span class="src src-nade">Nadeshiko</span>
      ${playBtn}
      <span class="nade-media">${sc.media}</span><span class="nade-ep">${sc.ep} @ ${sc.at}</span></div>
    <div class="nade-row">
      <img class="nade-thumb" src="${sc.thumb}" alt="" onerror="this.style.display='none'">
      <div>
        <div class="nade-jp">${sc.jp}</div>
        <div class="nade-en">${sc.en}</div>
        <div class="nade-cn">${sc.cn}</div>
        <a href="${sc.url}" target="_blank">nadeshiko.co ↗</a>
      </div>
    </div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["pillars","🏛️ 三本柱"],["map","🗺️ 体系図"],["history","📜 歴史"],["detail","📖 詳解"],["contrast","🔍 対比"],["sentences","📝 例文"],["quiz","🎯 クイズ"]];
let tab="map";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- etymology / history ---------- */
function renderHistory(){
  const E=ETYMOLOGY;
  let h=`<div class="card intro"><h2>${E.title}</h2>
  <p style="font-size:13px;color:var(--sub);margin-bottom:4px">${E.subtitle}</p>
  <p>${E.intro}</p></div>`;

  // Timeline
  h+=`<div class="card"><h3 style="margin-bottom:8px">📜 時間軸</h3><div class="ety-timeline">`;
  E.timeline.forEach(t=>{
    h+=`<div class="ety-era">
      <div class="era-date">${t.era}</div>
      <div class="era-form">${t.form}</div>
      <div class="era-note">${t.note}</div>
    </div>`;
  });
  h+=`</div></div>`;

  // Sections
  E.sections.forEach(sec=>{
    h+=`<div class="card"><h3 style="margin-bottom:8px">${sec.title}</h3>`;
    if(sec.content) h+=`<p>${sec.content}</p>`;
    if(sec.formula) h+=`<div class="ety-formula">${sec.formula}</div>`;

    // Inflection table
    if(sec.inflection){
      h+=`<table class="ety-inflection"><thead><tr>
        <th>活用形</th><th>形</th><th>接続</th><th>例文</th></tr></thead><tbody>`;
      sec.inflection.forEach(r=>{
        h+=`<tr><td class="form-col">${r.form}</td><td class="kana-col">${r.kana}</td>
          <td>${r.usage}</td><td style="font-family:serif">${r.example}</td></tr>`;
      });
      h+=`</tbody></table>`;
    }

    // Paths
    if(sec.paths){
      h+=`<div class="ety-path">`;
      sec.paths.forEach(p=>{
        h+=`<div class="ety-path-card">
          <div class="path-from">${p.from}</div>
          <div class="path-via">→ ${p.via} →</div>
          <div class="path-to">${p.to}</div>
          <div class="path-desc">${p.desc}</div>
        </div>`;
      });
      h+=`</div>`;
    }

    // Diffusion
    if(sec.diffusion){
      h+=`<div class="ety-diffusion">`;
      sec.diffusion.forEach(d=>{
        h+=`<div class="ety-diff-item">
          <div class="diff-sense">${d.sense}</div>
          <div class="diff-example">${d.example}</div>
          <div class="diff-desc">${d.desc}</div>
        </div>`;
      });
      h+=`</div>`;
    }

    // Diagnostic
    if(sec.diagnostic){
      h+=`<div class="ety-diag">`;
      sec.diagnostic.forEach(d=>{
        h+=`<div class="ety-diag-row">
          <div class="diag-test">${d.test}</div>
          <div class="diag-result">${d.result}</div>
        </div>
        <div style="font-size:12px;color:var(--sub);margin:-2px 0 6px 18px;font-style:italic">例：${d.example}</div>`;
      });
      h+=`</div>`;
    }

    h+=`</div>`;
  });

  // Final insight
  h+=`<div class="card" style="background:linear-gradient(135deg,#f5f0ff,#fff8e6);border:1px solid #d0c8f0">
    <h3 style="color:#5b3cc4;margin-bottom:8px">⚡ The Ultimate Insight</h3>
    <p style="font-size:14px;line-height:1.8">現代日語中的「に」不是一個單純的格助詞——它是一條從古典斷定助動詞「なり」分化出來的河流。
    にあり → なり → に，這條河流的三段旅程，解釋了為什麼「に」能同時擔任：
    <b>格助詞</b>（所在・方向・対象・時間・結果），
    <b>複合助詞的核心</b>（について・によって・に対して…），
    以及<b>な形容詞的詞尾</b>（静かな ← 静かなる）。
    70%以上的中高級接続語法，都是這個引擎在做力學支撐。</p>
  </div>`;

  $("#main").innerHTML=h;
}

/* ---------- three pillars ---------- */
function renderPillars(){
  let h=`<div class="card intro"><h2>「に」の三本柱 · Grand Unified Theory</h2>
    <p>テキスト中のどんな「に」も、この三本の柱のどれかにきれいに収まる。
    断定（第一）・格助詞（第二）・状態接続（第三）——「に」の全用法を貫く建築図。</p>
    <div class="steps">
      <div><b>Line 1 断定</b><br>名詞/形動語幹＋に＋なる・する —— 状態・身分を断言する。</div>
      <div><b>Line 2 格助詞</b><br>名詞＋に＋用言 —— 現実の座標にピン留めする GPS。</div>
      <div><b>Line 3 状態接続</b><br>節/条件名詞＋に＋用言 —— 事態を丸ごと様態副詞に変える。</div>
    </div></div>`;
  LINES.forEach(ln=>{
    const gs=GROUPS.filter(g=>g.line===ln.id);
    h+=`<div class="card pillar-card" style="--c:${ln.color}">
      <div class="pillar-head" style="background:${ln.color}">
        <div class="pillar-em">${ln.emoji}</div>
        <div><div class="pillar-name">${ln.name}</div><div class="pillar-sub">${ln.sub}</div></div>
      </div>
      <div class="pillar-syntax"><b>Syntax</b>　${ln.syntax}</div>
      <div class="pillar-mech">${ln.mechanic}</div>
      <div class="pillar-ex">${ln.examples.map(e=>`<code class="inline">${e}</code>`).join("　")}</div>
      ${gs.map(g=>{
        const members=ITEMS.filter(it=>it.group===g.id);
        return `<div class="pillar-grp" style="border-color:${g.color}">
          <div class="pillar-grp-h" style="color:${g.color}">${g.emoji} ${g.name} <span>${members.length} 点</span></div>
          <div class="mini-wrap">${members.map(n=>`
            <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
              <div class="em">${n.emoji}</div>
              <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
              <div class="im">${n.meaning.split('；')[0].split('…')[0]}</div></button>`).join("")}</div>
        </div>`;
      }).join("")}
    </div>`;
  });
  h+=`<div class="card" style="background:linear-gradient(135deg,#f5f0ff,#fff8e6);border:1px solid #d0c8f0">
    <h3 style="color:#5b3cc4;margin-bottom:8px">⚡ The Ultimate Takeaway</h3>
    <p style="font-size:14px;line-height:1.8">古文でも現代小説でも J-POP の歌詞でも、出会ったどんな「に」も、この三本の柱のどれかにきれいに収まる。
    <b>断定</b>は「何であるか」を言い、<b>格助詞</b>は「どこ・いつ・誰に」を指し、<b>状態接続</b>は「どんな状態で」を敷く。——これが「に」の Grand Unified Theory である。</p>
  </div>`;
  $("#main").innerHTML=h;
}

/* ---------- map (hub-spoke) ---------- */
function renderMap(){
let h=`<div class="card intro"><h2>Hub-and-Spoke：一個引擎，八條輻線</h2>
  <p>70%以上の中学級日語接續語法，都是「に」在做不同風格的力學支撐。
  <b>Hub（軸心）</b>就是「に」，<b>Spokes（輻線）</b>是描述你對該現實的認知動作的動詞。
  全體は三本柱（🏛️ 三本柱タブ）に収まる——断定・格助詞・状態接続。</p>
  <div class="steps">
    <div><b>Master Formula</b><br>[名詞短語] + [（斷定）に] + [語法化動詞] + [可選助詞]</div>
    <div><b>核心洞察</b><br>N5→N1 不是八座獨立的山，而是同一棵wheel的不同spoke。</div>
  </div></div>`;
  h+=`<div class="card"><div class="hub-map">
    <div class="hub-center">に</div>
    <div class="spoke-grid">`;
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    h+=`<div class="spoke" style="--c:${g.color};border-color:${g.color}20" onclick="goDetail('${members[0]?.id||''}')">
      <div class="em">${g.emoji}</div>
      <div class="nm">${g.name.split('·')[0].trim()}</div>
      <div class="desc">${g.note}</div>
      <div class="count">${members.length} 個語法點</div>
    </div>`;
  });
  h+=`</div></div></div>`;
  // group overview
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.name}</h3><span>${members.length} 點</span></div>
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="em">${n.emoji}</div>
        <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
        <div class="im">${n.meaning.split('；')[0].split('…')[0]}</div></button>`).join("")}</div></div>`;
  });
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
        <h2>${n.emoji} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta"><code>読作 ${n.read}</code>
          ${AUDIO[`${iid}-e0`]?`<button class="btn mini-btn" title="聴例句発音" onclick="play('${iid}-e0',this)">▶</button>`:""}
        </div>
        <div class="engine-box"><b>🔧 Engine</b>　${n.engine}</div>
        <div class="blueprint-box"><b>📐 Blueprint</b>　${n.blueprint}</div>
        <div class="meanbox">📌 <b>意思</b>　${n.meaning}</div>
        <h3 class="sec">例句</h3>
        ${(n.examples||[]).map((ex,i)=>rowHTML(`${iid}-e${i}`,ex)).join("")}
        ${(n.nadeshiko||[]).map((sc,i)=>nadeHTML(sc,iid,i)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- contrast ---------- */
function renderContrast(){
  let h=`<div class="card intro"><h2>Group級対比：八條Lineage的力學差異</h2>
  <p>同一個「に」，搭配不同的動詞或名詞，認知力學完全不同。下面按group逐一对比。
  全体は三本柱（🏛️ 三本柱タブ）に収まる。</p></div>`;
  const contrasts=[
    {c:"#e74c3c",title:"する系 vs 他系",items:[
      ["にする (N5)","鎖定選項 → 敲定","核心：主觀決定"],
      ["にしては (N3)","鎖定事實 → 但出現意外","核心：事實基線 vs 預期反差"],
      ["にしても (N2)","完全承認 → 結論不變","核心：退讓讓步"],
    ]},
    {c:"#3498db",title:"よる系：信息源 vs 方法",items:[
      ["によると (N4)","追溯依賴 → 到信息源","核心：據…說（傳聞來源）"],
      ["によって (N3)","鎖定參數 → 聲明為通用引擎","核心：因…而異 / 通過…手段"],
    ]},
    {c:"#2ecc71",title:"時空向量系：時間定位的微妙差異",items:[
      ["に際して (N2)","正式場合 → 臨界點","核心：正值…之際（客觀）"],
      ["にあたって (N2)","重大事件 → 正面迎上","核心：在…之際（主觀能動）"],
      ["に先立って (N2)","事件前方 → 時間先行","核心：在…之前（先行準備）"],
      ["にあって (N1)","重壓處境 → 存在其中","核心：身處…之中（沉重書面）"],
    ]},
    {c:"#9b59b6",title:"関連系：三種「關於」的微妙差異",items:[
      ["について (N4)","緊貼目標 → 不遊移","核心：關於（最常用）"],
      ["に関して (N3)","追溯關係 → 輻射網絡","核心：關於（更正式書面）"],
      ["に対して (N3)","正面對準 → 投射動作","核心：對… / 與…相反"],
    ]},
    {c:"#f39c12",title:"推移系：四種變化表達",items:[
      ["につれて (N3)","綁定尾流 → 漸進同步","核心：隨著（漸進變化）"],
      ["に従って (N2)","遵循軌道 → 服從規則","核心：按照 / 隨著（遵從）"],
      ["にともなって (N2)","捆綁同行 → 因果套餐","核心：伴隨（因果捆綁）"],
      ["に反して (N2)","預期基線 → 方向相反","核心：與…相反（預期反差）"],
    ]},
    {c:"#16a085",title:"連用系：裸「に」的直系遺產（連用修飾 vs 連体修飾）",items:[
      ["語幹＋に (N5)","不加動詞 → 直接連用修飾","核心：名詞/形動語幹＋に＝連用修飾語（静かに・実際に），修饰用言"],
      ["語幹＋な ← 同根（連体）","なる→な → 連体修飾","核心：静かな・確かだ系——同一个なり，連体形修飾体言"],
      [" vs 〜にする (N5)","同源但派生 → 接動詞","核心：にする＝「に＋する」變成了語法動詞绑定的衍生"],
    ]},
    {c:"#b8860b",title:"状態接続系：事態を丸ごと副詞に変える架橋（第三本柱）",items:[
      ["ずに (N3)","未然形＋ず → 打消の状態","核心：不…就…（＝ないで）"],
      ["ながらに (N2)","名詞＋ながら → 状態の継続","核心：〜の状態のままで（涙ながらに）"],
      ["ままに／がまま (N2/N1)","状態を保持 → 成り行きに委ねる","核心：按照…／任凭…（意のまま・望むがまま）"],
      ["ゆえに (N2)","原因を前置 → 理由の副詞","核心：因为…（＝だから）"],
      ["ことに (N2)","感情評価を前置 → 文全体を覆う","核心：令人…的是（嬉しいことに）"],
      ["うちに (N3)","期間の窓 → 状態が変わる前","核心：趁着…（若いうちに）"],
      ["わりに (N2)","予想ベースとの対比 → 釣り合わない","核心：虽然…却…（値段のわりに）"],
      ["かわりに (N3)","代替 → 動作の前提に置く","核心：代替…／作为交换…"],
      ["ために (N3)","目的・原因を前置","核心：为了…／因为…"],
    ]},
  ];
  contrasts.forEach(sec=>{
    h+=`<div class="card contrast-card" style="--c:${sec.c}"><h4>${sec.title}</h4>`;
    sec.items.forEach(([name,mech,core])=>{
      h+=`<div style="margin:8px 0;padding:8px 12px;background:#f8f9fc;border-radius:10px">
        <div style="font-weight:700;color:${sec.c}">${name}</div>
        <div style="font-size:13px;color:var(--sub);margin:3px 0">力學：${mech}</div>
        <div style="font-size:12.5px;color:var(--gold)">${core}</div>
      </div>`;
    });
    h+=`</div>`;
  });
  // master formula card
  h+=`<div class="card" style="background:linear-gradient(135deg,#f5f0ff,#eef6ff);border:1px solid #d0c8f0">
    <h3 style="color:#5b3cc4;margin-bottom:8px">⚡ The Ultimate Epiphany</h3>
    <p style="font-size:14px;line-height:1.8">把 N5→N1 看成一座山是錯的。它們是同一個 <b>Hub-and-Spoke Wheel</b> 的不同輻線。
    Hub 是斷定の「に」，Spoke 是描述你認知動作的動詞。
    70%以上的中高級接續語法，都是這個引擎在做力學支撐。</p>
  </div>`;
  $("#main").innerHTML=h;
}

/* ---------- sentences ---------- */
function renderSentences(){
  let h=`<div class="card intro"><h2>例文集 · MOJi + Nadeshiko 雙源</h2>
  <p>點擊 ▶ 聴TTS発音。<span class="src src-moji">MOJi</span> = 教科書例句，
  <span class="src src-nade">Nadeshiko</span> = 真實動漫/日劇台詞（▶ 播放原聲）。</p></div>`;
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.name}</h3></div>`;
    members.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card" style="margin-top:10px;padding:12px 14px">
        <div style="font-weight:700;color:${g.color};margin-bottom:6px">${n.emoji} ${n.word} <small style="color:var(--sub)">${n.level}</small></div>`;
      (n.examples||[]).forEach((ex,i)=>{
        h+=rowHTML(`${iid}-e${i}`,ex);
      });
      (n.nadeshiko||[]).forEach((sc,i)=>{
        h+=nadeHTML(sc,iid,i);
      });
      h+=`</div>`;
    });
    h+=`</div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("ni-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("ni-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("ni-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 錯題重練 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做対即移出錯題本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">錯題本是空的——答錯的題會自動收進來 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">選擇訓練關卡</div>
      <div class="hint" style="margin-bottom:18px">題目由 ni.json 自動生成 · 全部隨機打亂</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交錯 · 全量隨機<br><span style="font-size:12px;color:var(--sub)">跨語法點交錯練習，記憶更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空錯題本？')){localStorage.removeItem('ni-wrong');renderQuizTab();}">🗑️ 清空錯題本</button>`:""}
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
      <div class="hint">可反復點擊重聽</div></div>`
      :`<div class="hint" style="text-align:center;margin-bottom:10px">（這條發音還沒生成）</div>`;
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
  const msg=pct===100?"🏆 完璧！断定の「に」已完全掌握！":pct>=70?"👍 かなりいい！錯題趁熱打鐵":"📖 詳解タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">別的關卡選一選</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正確率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){mode=null;render();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="pillars")renderPillars();
  else if(tab==="map")renderMap();
  else if(tab==="history")renderHistory();
  else if(tab==="detail")renderDetail();
  else if(tab==="contrast")renderContrast();
  else if(tab==="sentences")renderSentences();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, groups, items = load_data()
    n_total = len(items)
    level_counts = {}
    for it in items:
        lv = it["level"]
        level_counts[lv] = level_counts.get(lv, 0) + 1
    level_str = "・".join(f"{k}×{v}" for k, v in sorted(level_counts.items()))

    print(f"[1/4] audio: TTS for {n_total} grammar points...")
    audio = gen_audio(items)

    print(f"[2/4] nadeshiko: downloading CDN audio...")
    nade_audio = gen_nade_audio(items)

    print(f"[3/4] generating quiz banks for {n_total} grammar points...")
    qs = build_questions(groups, items, audio)
    banks_meta = [[k, l] for k, l in meta.get("quizBanks", [
        ["recog", "📘 语法认识"], ["engine", "🔧 Engine拆解"],
        ["fill", "✍️ 运用填空"], ["listen", "🎧 聴解判别"],
        ["judge", "⭕ 判断正误"]
    ])]

    all_banks = set(q["bank"] for q in qs)
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k in all_banks}
    total_q = sum(counts.values())
    for k, l in banks_meta:
        print(f"      {l}: {counts.get(k, 0)} 问")
    print(f"      合计: {total_q} 问")

    n_nade = sum(len(it.get("nadeshiko", [])) for it in items)
    n_ex = sum(len(it.get("examples", [])) for it in items)
    tags = (f"<span>{n_total} 语法点</span><span>{level_str}</span>"
            f"<span>{n_ex} 例句 + {n_nade} 原声</span><span>MOJi + Nadeshiko</span>")

    raw = json.loads(DATA.read_text(encoding="utf-8"))
    etymology = raw.get("etymology", {})
    lines = raw.get("lines", [])

    # display copy with furigana ruby (TTS audio keeps plain text)
    display = copy.deepcopy(items)
    if HAS_KAKASI:
        for it in display:
            for ex in it.get("examples", []):
                ex["jp"] = add_furigana(ex["jp"])
            for sc in it.get("nadeshiko", []):
                sc["jp"] = add_furigana(sc["jp"])
        print(f"      furigana: applied to {sum(len(it.get('examples', [])) + len(it.get('nadeshiko', [])) for it in display)} sentences")

    print("[4/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__NADE_AUDIO__", j(nade_audio))
            .replace("__GROUPS__", j(groups))
            .replace("__LINES__", j(lines))
            .replace("__ITEMS__", j(display))
            .replace("__BANKS__", j(banks_meta))
            .replace("__QS__", j(qs))
            .replace("__ETYMOLOGY__", j(etymology)))
    OUT.write_text(html, encoding="utf-8")
    print(f"      wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
