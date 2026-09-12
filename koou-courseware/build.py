#!/usr/bin/env python3
"""Build the self-contained 「呼応・搭配」 courseware HTML.

数据源：koou.json（由 extract_bank.py 从 jlpt-n1-question-bank 抽取）。
用法：往 koou.json 里加/改数据 → 运行本脚本 → index.html 自动长出卡片、
发音、真题与题库。

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
DATA = ROOT / "koou.json"
AUDIO_DIR = ROOT / "audio"
NADE_CACHE = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260912
MIN_MP3 = 300
PREFIX = "koou"


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ────────────────────────────────────────────── furigana (ruby)
_KANJI = r"\u4e00-\u9fff\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")

_READING_OVERRIDES = [
    ("時間が経つ", "じかんがたつ"), ("年を取る", "としをとる"),
    ("人によって", "ひとによって"), ("多くの人が", "おおくのひとが"),
    ("今日は", "きょうは"), ("一日", "いちにち"), ("一日中", "いちにちじゅう"),
    ("一番", "いちばん"), ("一緒に", "いっしょに"), ("大人", "おとな"),
    ("子供", "こども"), ("上手", "じょうず"), ("下手", "へた"),
    ("今度", "こんど"), ("今年", "ことし"), ("昨日", "きのう"),
    ("明日", "あした"), ("今日", "きょう"), ("何時", "なんじ"),
    ("一人", "ひとり"), ("二人", "ふたり"), ("上手く", "うまく"),
    ("気持ち", "きもち"), ("出来る", "できる"), ("初めて", "はじめて"),
    ("お客様", "おきゃくさま"), ("社長", "しゃちょう"), ("部長", "ぶちょう"),
    ("課長", "かちょう"), ("先生", "せんせい"), ("学生", "がくせい"),
    ("本当に", "ほんとうに"), ("本当", "ほんとう"), ("大変", "たいへん"),
    ("大切", "たいせつ"), ("大事", "だいじ"), ("大丈夫", "だいじょうぶ"),
    ("主人", "しゅじん"), ("家内", "かない"), ("息子", "むすこ"),
    ("娘", "むすめ"), ("両親", "りょうしん"), ("兄弟", "きょうだい"),
    ("皆さん", "みなさん"), ("皆", "みんな"), ("自分", "じぶん"),
    ("場合", "ばあい"), ("場所", "ばしょ"), ("気分", "きぶん"),
    ("気", "き"), ("物", "もの"), ("事", "こと"), ("方", "ほう"),
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


def add_furigana(text):
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
    out, pos = [], 0
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
        sys.exit(f"[!] koou.json 不是合法 JSON：{e}")
    errors = []
    meta = data.get("meta", {})
    groups = data.get("groups", [])
    items = data.get("items", [])
    exams = data.get("exams", [])
    if not groups:
        errors.append("缺少 groups")
    if not items:
        errors.append("缺少 items")
    if not exams:
        errors.append("缺少 exams")
    gids = {g.get("id") for g in groups}
    seen = set()
    for it in items:
        iid = it.get("id", "")
        if not iid:
            errors.append(f"条目「{it.get('word')}」缺 id")
        elif iid in seen:
            errors.append(f"item id 重复：{iid}")
        seen.add(iid)
        if it.get("group") not in gids:
            errors.append(f"「{iid}」group「{it.get('group')}」不在 groups")
        for k in ("word", "read", "level", "meaning", "engine", "blueprint"):
            if not it.get(k):
                errors.append(f"「{iid}」缺 {k}")
    if errors:
        print("[!] koou.json 有问题：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, groups, items, exams


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
             "--write-media", str(path)], capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > MIN_MP3:
            return True
    return False


def fill_blank(stem, answer):
    for bl in ["（　）", "（　　　）", "(　)", "（ ）", "（    ）", "（  ）", "（   ）"]:
        if bl in stem:
            return stem.replace(bl, f"【{answer}】", 1)
    return stem


def exam_spoken(e):
    """Return the speakable sentence for a choice exam (blank filled)."""
    if e["type"] == "choice":
        return fill_blank(e["stem"], e["answer_text"])
    return None


def gen_audio(exams):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []
    for e in exams:
        txt = exam_spoken(e)
        if txt:
            tasks.append((f"ex-{e['id']}", txt))
    todo = [(lid, txt) for lid, txt in tasks
            if not cache_path(lid, txt).exists() or cache_path(lid, txt).stat().st_size <= MIN_MP3]
    print(f"[1/4] audio: {len(tasks)} choice sentences total, {len(tasks)-len(todo)} cached, "
          f"{len(todo)} to synthesize...")
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
    failed = [lid for lid, ok in results.items() if not ok]
    if failed:
        print(f"[!] {len(failed)} 条发音合成失败（课件照常生成）：{failed[:8]}")
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
def download_nade_audio(url, logical_id):
    NADE_CACHE.mkdir(exist_ok=True)
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    path = NADE_CACHE / f"nade-{h}.mp3"
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
        futs = {lid: ex.submit(download_nade_audio, url, lid) for lid, url in tasks}
        for lid, fut in futs.items():
            data = fut.result()
            if data:
                nade_audio[lid] = data
    print(f"  nade: {len(nade_audio)}/{len(tasks)} clips cached")
    return nade_audio


# ────────────────────────────────────────────── auto quizzes
def build_questions(items, exams, audio):
    rng = random.Random(SEED)
    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    # 🎯 真题 bank (exhaustive: every 問題5 choice + 問題6 composition)
    for e in exams:
        if e["type"] not in ("choice", "composition"):
            continue
        if not e.get("options") or not e.get("answer_index"):
            continue
        tag = e["section"]
        exp = (f'{tag}　{e["year"]}-{e["month"]:02d} Q{e["number"]}<br>'
               f'正解：{e["answer_index"]}　{e["answer_text"]}'
               + (f'<br>呼応・搭配：{e["pattern"]}' if e.get("pattern") else '')
               + f'<br><span style="color:var(--sub)">出处：{e["source"]}</span>')
        add("exam", e["id"], type="choice", q=e["stem"], opts=e["options"],
            ans=e["answer_index"] - 1, exp=exp,
            meta=dict(year=e["year"], month=e["month"], no=e["number"], section=tag))

    def distract(it, field, n=3):
        own = it[field]
        cand = [x[field] for x in items if x["id"] != it["id"] and x[field] != own]
        rng.shuffle(cand)
        return cand[:n]

    for it in items:
        iid = it["id"]
        # 📘 意味認識
        add("recog", f"{iid}:recog", type="choice",
            q=f"「{it['word']}」表示什么？",
            opts=[it["meaning"]] + distract(it, "meaning"), ans=0,
            exp=f'{it["word"]}（{it["read"]}）＝{it["meaning"]}<br>🔧 {it["engine"]}')
        # 🔧 呼応拆解
        add("engine", f"{iid}:engine", type="choice",
            q=f"「{it['word']}」的呼応结构是？",
            opts=[f'{it["trigger"]} ⇄ {it["response"]}'] + [f'{x["trigger"]} ⇄ {x["response"]}' for x in items if x["id"] != iid][:3],
            ans=0, exp=f'🔧 {it["engine"]}<br>📐 {it["blueprint"]}')
        # 🎧 聴解判别
        if it.get("examples"):
            ex = it["examples"][0]
            aid = "ex-" + ex["qid"]
            if aid in audio:
                opts = [it["word"]] + [x["word"] for x in items if x["id"] != iid][:3]
                rng.shuffle(opts)
                add("listen", f"{iid}:listen", type="listen", aid=aid,
                    q="🎧 听音频：句子里用了哪个呼応・搭配？", opts=opts,
                    ans=opts.index(it["word"]),
                    exp=f'原句：{ex["jp"]}<br>正解：{it["word"]}')
        # ⭕ 判断正誤
        add("judge", f"{iid}:judge", type="judge",
            q=f"「{it['word']}」：{it['engine']}", ans=True, exp=f'正解！{it["blueprint"]}')

    extra_judges = [
        ("「たとえ〜ても」的后项必须是肯定，不能接否定。", False,
         "誤り。たとえ〜ても后项可肯定可否定，核心是「让步」。"),
        ("「いっさい〜ない」「なんら〜ない」「決して〜ない」都属于全面否定呼応。", True,
         "正解。三者都要求后项是否定。"),
        ("「どうやら」呼应的是「〜ようだ／らしい」这类推量述语。", True,
         "正解。どうやら＝委婉推断。"),
        ("「あたかも」与「かのごとく」不能搭配使用。", False,
         "誤り。あたかも〜かのごとく 是固定搭配。"),
        ("「〜を機に」的「を」提示的是对象，「に」提示的是时点。", True,
         "正解。を（对象）＋機（节点）＋に（时点）。"),
        ("「〜に先立ち」表示「在…之后」。", False,
         "誤り。に先立ち＝在…之前（先行准备）。"),
        ("「〜ならではの」强调「只有…才有的」独特性。", True, "正解。"),
        ("「必ずしも〜ない」是全面否定。", False,
         "誤り。必ずしも〜ない＝部分否定（未必）。"),
        ("「さすが〜だけあって」表达「果然名不虚传」。", True, "正解。"),
        ("「〜はどうあれ」表示「无论…如何」，把方式排除在讨论外。", True, "正解。"),
    ]
    for i, (q, ans, exp) in enumerate(extra_judges):
        add("judge", f"extra-judge-{i}", type="judge", q=q, ans=ans, exp=exp)
    return qs


# ────────────────────────────────────────────── contrast data
CONTRAST = [
    {"c": "#2563a8", "title": "A 仮定・譲歩：三种「即使…也」",
     "items": [
         ["たとえ〜ても", "极端假定 → 结论不变", "例：たとえ雨が降っても行く"],
         ["どんなに〜ても", "程度无上限 → 结论不变", "例：どんなに反論しようと自由だ"],
         ["いくら〜ても", "反复/次数 → 结论不变", "例：いくら注意しても直らない"],
     ]},
    {"c": "#d33f49", "title": "B 全面否定：否定副词的强度",
     "items": [
         ["決して〜ない", "意志性强调（绝不）", "例：決して諦めない"],
         ["いっさい〜ない", "范围归零（完全不）", "例：添加物はいっさい使わない"],
         ["なんら〜ない", "书面强调（毫无）", "例：品質にはなんら問題はない"],
         ["必ずしも〜ない", "部分否定（未必）", "例：必ずしもそうではない"],
     ]},
    {"c": "#6a3de8", "title": "C 推量・様態：推断与比况",
     "items": [
         ["どうやら〜ようだ", "有证据的委婉推断", "例：どうやら怖いようだ"],
         ["はたして〜だろうか", "质疑式疑问", "例：はたして本当だろうか"],
         ["まるで〜ない", "全面强调（完全不）", "例：まるで興味がない"],
         ["あたかも〜かのごとく", "比况（仿佛）", "例：あたかも事実であるかのごとく"],
     ]},
    {"c": "#b8860b", "title": "D 強調・程度：限定与感叹",
     "items": [
         ["せめて〜だけでも", "最低愿望", "例：せめて気分だけでも"],
         ["なんと〜ことか", "感叹顶点", "例：なんと美しかったことか"],
         ["ただ／単に〜だけ", "限定", "例：単に指摘したにすぎない"],
         ["〜にもほどがある", "过度越界", "例：猫好きにもほどがある"],
     ]},
    {"c": "#188a52", "title": "E 文末モダリティ：推定的收尾",
     "items": [
         ["〜に違いない", "确信（一定）", "例：最後までやり通すに違いない"],
         ["〜はずだ", "逻辑推论（理应）", "例：空いてるはずだ"],
         ["〜に決まっている", "主观强断定", "例：無理だと言われるに決まっている"],
         ["〜おそれがある", "负面可能（恐怕）", "例：失わせてしまうおそれがある"],
     ]},
    {"c": "#e67e22", "title": "F 助詞系文型：助词决定搭配",
     "items": [
         ["〜を機に", "を（对象）＋機（节点）＋に（时点）", "以…为契机"],
         ["〜に先立ち", "に（时点）＋先立つ（先行）", "在…之前"],
         ["〜を受けて", "を（对象）＋受ける（承接）", "接受…之后"],
         ["〜をもって", "を（对象）＋持つ（凭借/为界）", "以…为界；用…"],
         ["〜とあって", "と（引用）＋あって（原因）", "因为（特殊状况）"],
         ["〜ならではの", "ならでは（限定主体）＋の", "只有…才有的"],
     ]},
]


# ────────────────────────────────────────────── html template
TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>呼応・搭配 完全体系</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#1f3a6e,#7b3fe4);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);border-radius:99px;
padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:900px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:6px;margin:-18px 0 16px;position:relative;z-index:2;flex-wrap:wrap}
nav button{flex:1;min-width:0;border:none;border-radius:12px;padding:12px 2px;font-size:12.5px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16px;line-height:2.05;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.intro{background:linear-gradient(135deg,#eef1ff,#f5f0ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#3b4fd4}
.intro p{font-size:14px;line-height:1.8}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:170px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#6a3de8}
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-note{font-size:12.5px;color:var(--sub);line-height:1.6;padding:8px 4px}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;padding:6px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#6a3de8)}
.mini .em{font-size:24px}
.mini .nm{font-weight:800;font-size:14.5px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.mini .ct{font-size:10.5px;color:#fff;background:var(--g,#6a3de8);border-radius:99px;padding:1px 7px;margin-left:4px}
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#6a3de8)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:2px 9px;font-size:12px}
.mini-btn{width:26px;height:26px;font-size:12px}
.engine-box{background:#fff8e6;border:1px solid #f0d060;border-radius:12px;padding:10px 13px;font-size:13.5px;line-height:1.7;margin:10px 0}
.engine-box b{color:#b8860b}
.blueprint-box{background:#eef6ff;border:1px solid #c0d8f0;border-radius:12px;padding:10px 13px;font-size:13.5px;line-height:1.7;margin:10px 0}
.blueprint-box b{color:#2563a8}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
.exam-card{background:#fbfcff;border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin:10px 0}
.exam-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--sub);margin-bottom:6px}
.badge{border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700}
.badge-q5{background:#e8f0fe;color:#2563a8}.badge-q6{background:#fdeede;color:#e67e22}
.badge-q7{background:#eee7f6;color:#6a3de8}
.badge-ok{background:var(--okbg);color:var(--ok)}.badge-pending{background:#fdf3e0;color:var(--gold)}
.exam-stem{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:15.5px;line-height:2}
.exam-stem ruby rt{font-size:.52em;color:var(--sub)}
.exam-opts{margin:7px 0 0;padding-left:4px;font-size:14px;line-height:1.7}
.exam-opts li{list-style:none;margin:3px 0}
.exam-opts li.correct{color:var(--ok);font-weight:700}
.exam-ans{font-size:13px;margin-top:6px;color:var(--ink)}
.exam-src{font-size:11px;color:var(--sub);margin-top:5px}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.filters button{border:1px solid var(--line);background:#fff;border-radius:99px;padding:5px 12px;font-size:12px;cursor:pointer;color:var(--sub)}
.filters button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.q{font-size:16px;line-height:1.85;margin-bottom:14px}
.q .jp{font-size:16px}
.opt{display:block;width:100%;text-align:left;padding:12px 14px;margin:8px 0;font-size:15px;
border-radius:12px;border:2px solid var(--line);background:#fff;cursor:pointer;line-height:1.55}
.opt:hover:not(:disabled){border-color:var(--acc)}
.opt.right{border-color:var(--ok);background:var(--okbg)}
.opt.wrong{border-color:var(--ng);background:var(--ngbg)}
.opt:disabled{cursor:default;opacity:.92}
.exp{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:14px;line-height:1.7}
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
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;margin-left:6px;vertical-align:middle}
.src-jlpt{background:#e8f0fe;color:#2563a8}
.src-nade{background:#ede7f6;color:#5e35b1}
.nade-card{background:#faf5ff;border:1px solid #e0d0f0;border-radius:12px;padding:12px;margin:10px 0}
.nade-card .nade-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.nade-card .nade-media{font-weight:700;color:#5e35b1;font-size:13px}
.nade-card .nade-ep{font-size:11.5px;color:var(--sub)}
.nade-card .nade-jp{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:15px;line-height:2}
.nade-card .nade-jp ruby rt{font-size:.52em;color:var(--sub)}
.nade-card .nade-en{font-size:12.5px;color:var(--sub);margin-top:3px;font-style:italic}
.nade-card .nade-row{display:flex;gap:10px;align-items:flex-start}
.nade-card .nade-thumb{width:80px;height:50px;border-radius:8px;object-fit:cover;flex:none}
.nade-card a{color:#5e35b1;font-size:11.5px;text-decoration:none}
.contrast-card{border-left:4px solid var(--c,#6a3de8);padding-left:14px;margin-bottom:18px}
.contrast-card h4{font-size:15px;color:var(--c);margin-bottom:6px}
</style>
</head>
<body>
<header><div class="wrap">
<h1>呼応・搭配 完全体系</h1>
<div class="kana">こおう —— JLPT N1 過去問（2010-07 ～ 2025-07）の呼応・助詞搭配を一望 ⚡</div>
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
const ITEMS=__ITEMS__;
const EXAMS=__EXAMS__;
const BANKS=__BANKS__;
const CONTRAST=__CONTRAST__;
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
  const chip=s.src==="nadeshiko"?`<span class="src src-nade">Nadeshiko</span>`:`<span class="src src-jlpt">真题</span>`;
  return `<div class="row">${b}<div><div class="jp">${s.jp}${chip}</div><div class="cn">${s.cn}</div></div></div>`;
}
function nadeHTML(sc,iid,idx){
  const nid=`${iid}-n${idx}`;
  const hasAudio=!!NADE_AUDIO[nid];
  const playBtn=hasAudio?`<button class="btn" style="width:30px;height:30px;font-size:13px" onclick="play('${nid}',this)">▶</button>`:"";
  return `<div class="nade-card">
    <div class="nade-hdr"><span class="src src-nade">Nadeshiko</span>${playBtn}
      <span class="nade-media">${sc.media}</span><span class="nade-ep">${sc.ep} @ ${sc.at}</span></div>
    <div class="nade-row">
      <img class="nade-thumb" src="${sc.thumb}" alt="" onerror="this.style.display='none'">
      <div><div class="nade-jp">${sc.jp}</div><div class="nade-en">${sc.en}</div>
      <a href="${sc.url}" target="_blank">nadeshiko.co ↗</a></div>
    </div></div>`;
}
function examHTML(e){
  const bd=e.type==="choice"?"badge-q5":e.type==="composition"?"badge-q6":"badge-q7";
  const aid="ex-"+e.id;
  const playBtn=AUDIO[aid]?`<button class="btn mini-btn" title="聴発音" onclick="play('${aid}',this)">▶</button>`:"";
  const vd=e.verified==="matched"?`<span class="badge badge-ok">✓ matched</span>`:`<span class="badge badge-pending">pending</span>`;
  const opts=(e.options||[]).map((o,i)=>{
    const ok=(i+1)===e.answer_index;
    return `<li class="${ok?'correct':''}">${i+1}. ${o}${ok?'　✔':''}</li>`;
  }).join("");
  return `<div class="exam-card">
    <div class="exam-head"><span class="badge ${bd}">${e.section}</span>
      <b>${e.year}-${String(e.month).padStart(2,'0')} Q${e.number}</b>${vd}${playBtn}
      ${e.pattern?`<span class="badge" style="background:#eef1ff;color:#4f6ef7">${e.pattern}</span>`:''}</div>
    <div class="exam-stem">${e.stem}</div>
    <ul class="exam-opts">${opts}</ul>
    <div class="exam-ans">正解：${e.answer_index}. <b>${e.answer_text}</b></div>
    <div class="exam-src">出处：${e.source}</div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["pillars","🏛️ 呼応の原理"],["map","🗺️ 体系図"],["detail","📖 詳解"],
  ["contrast","🔍 対比"],["exams","📝 真題総覧"],["quiz","🎯 クイズ"]];
let tab="map";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- pillars ---------- */
function renderPillars(){
  const nEx=EXAMS.filter(e=>e.type!=='passage').length;
  let h=`<div class="card intro"><h2>呼応（こおう）＝ 前項が後項を予告する</h2>
    <p>日本語の上級文法には、<b>前半の副詞・助詞・文型が、後半の述語の形をあらかじめ指定する</b>型がある。
    二者は必ずセットで現れ、片方だけでは文が決まらない。これが「呼応」であり、JLPT N1 問題5/問題6 の中心的考点である。</p>
    <div class="steps">
      <div><b>A 仮定・譲歩</b><br>たとえ〜ても／どんなに〜ても／いくら〜ても —— 前項を極限へ、後項は不変。</div>
      <div><b>B 全面否定</b><br>決して／いっさい／なんら／必ずしも〜ない —— 副詞が否定を予告。</div>
      <div><b>C 推量・様態</b><br>どうやら〜ようだ／あたかも〜かのごとく —— 副詞が推量を予告。</div>
      <div><b>D 強調・程度</b><br>せめて〜だけでも／なんと〜ことか —— 限定と感叹。</div>
      <div><b>E 文末モダリティ</b><br>〜に違いない／〜はずだ／〜おそれがある —— 述語で断定。</div>
      <div><b>F 助詞系文型</b><br>を機に／を受けて／をもって／ならではの —— 助词决定搭配。</div>
    </div></div>`;
  h+=`<div class="card"><h3 class="sec">収録規模</h3>
    <p style="font-size:14px;line-height:1.9">全 <b>${GROUPS.length}</b> 類型 · <b>${ITEMS.length}</b> 個の呼応・搭配パターン ·
    <b>${nEx}</b> 問の真題（問題5 文法選択 + 問題6 並べ替え）をパターン別に整理。
    さらに <b>${EXAMS.length}</b> 問（問題7 文章文法を含む）を「📝 真題総覧」タブに全量収録。
    各問に出典（年度・月・問題番号）と校対状態を明記。</p></div>`;
  GROUPS.forEach(g=>{
    const mem=ITEMS.filter(i=>i.group===g.id);
    if(!mem.length)return;
    h+=`<div class="card" style="border-left:5px solid ${g.color}">
      <h3 style="color:${g.color};margin-bottom:6px">${g.emoji} ${g.name}</h3>
      <p style="font-size:13.5px;line-height:1.75">${g.note}</p>
      <div class="hint" style="margin-top:6px">収録 ${mem.length} 点</div></div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- map ---------- */
function renderMap(){
  let h=`<div class="card intro"><h2>体系図：六類型の呼応・搭配</h2>
    <p>呼応の型は、前項（副詞・助詞）と後項（述語）の関係で六つに分けられる。
    カードをタップすると詳解へ。</p></div>`;
  GROUPS.forEach(g=>{
    const mem=ITEMS.filter(i=>i.group===g.id);
    if(!mem.length)return;
    h+=`<div class="grp"><div class="grp-h" style="background:${g.color}">
      <h3>${g.emoji} ${g.name}</h3><span>${mem.length} 点</span></div>
      <div class="grp-note">${g.note}</div>
      <div class="mini-wrap">${mem.map(n=>`
        <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
          <div class="em">${n.emoji}</div>
          <div class="nm">${n.word}<span class="ct">${n.exam_count}</span></div>
          <div class="im">${n.meaning}</div></button>`).join("")}</div></div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- detail ---------- */
function renderDetail(){
  let h=`<div class="card intro"><h2>詳解：パターン × 真題</h2>
    <p>各パターンの仕組みと、それが実際に出題された真題（全文・選択肢・正解・出典）を並べる。</p></div>`;
  GROUPS.forEach(g=>{
    const mem=ITEMS.filter(i=>i.group===g.id);
    if(!mem.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.emoji} ${g.name}</h3>`;
    mem.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta"><code>呼応 ${n.trigger} ⇄ ${n.response}</code>
          <code>真題 ${n.exam_count} 問</code></div>
        <div class="engine-box"><b>🔧 Engine</b>　${n.engine}</div>
        <div class="blueprint-box"><b>📐 Blueprint</b>　${n.blueprint}</div>
        <div class="meanbox">📌 <b>意思</b>　${n.meaning}</div>
        ${(n.examples||[]).length?`<h3 class="sec">真題例文</h3>`:""}
        ${(n.examples||[]).map(ex=>rowHTML("ex-"+ex.qid,ex)).join("")}
        ${(n.nadeshiko||[]).map((sc,i)=>nadeHTML(sc,iid,i)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
        ${(n._exams||[]).length?`<h3 class="sec">出題真題（${n._exams.length}）</h3>${n._exams.map(examHTML).join("")}`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- contrast ---------- */
function renderContrast(){
  let h=`<div class="card intro"><h2>対比：似ている呼応の力学差</h2>
    <p>同じ「〜ても」でも、前項の副詞が違えば力の入れ方が違う。グループ単位で並べて比較する。</p></div>`;
  CONTRAST.forEach(sec=>{
    h+=`<div class="card contrast-card" style="--c:${sec.c}"><h4>${sec.title}</h4>`;
    sec.items.forEach(([name,mech,core])=>{
      h+=`<div style="margin:8px 0;padding:8px 12px;background:#f8f9fc;border-radius:10px">
        <div style="font-weight:700;color:${sec.c}">${name}</div>
        <div style="font-size:13px;color:var(--sub);margin:3px 0">力学：${mech}</div>
        <div style="font-size:12.5px;color:var(--gold)">${core}</div></div>`;
    });
    h+=`</div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- exams ---------- */
let exFilter="all", exSession="all";
function renderExams(){
  const sessions=[...new Set(EXAMS.map(e=>`${e.year}-${String(e.month).padStart(2,'0')}`))];
  let h=`<div class="card intro"><h2>真題総覧（全 ${EXAMS.length} 問）</h2>
    <p>2010-07 ～ 2025-07 の JLPT N1 文法問題を全量収録（問題5 文法選択・問題6 並べ替え・問題7 文章文法）。
    各問に正解・出典・校対状態を付す。</p></div>`;
  h+=`<div class="filters">`;
  const fgroups=[["all","全部"],["hypothesis","A 仮定譲歩"],["negation","B 全面否定"],
    ["conjecture","C 推量様態"],["emphasis","D 強調程度"],["modality","E 文末モダリティ"],
    ["particle","F 助詞文型"],["other","未分類"]];
  fgroups.forEach(([k,l])=>h+=`<button class="${exFilter===k?'on':''}" onclick="setExFilter('${k}')">${l}</button>`);
  h+=`</div>`;
  h+=`<div class="filters"><select id="exsel" onchange="setExSession(this.value)" style="border:1px solid var(--line);border-radius:8px;padding:5px 10px;font-size:12px">
    <option value="all">全部场次</option>${sessions.map(s=>`<option value="${s}" ${exSession===s?'selected':''}>${s}</option>`).join("")}</select></div>`;
  let list=EXAMS.filter(e=>exFilter==="all"||e.group===exFilter);
  if(exSession!=="all") list=list.filter(e=>`${e.year}-${String(e.month).padStart(2,'0')}`===exSession);
  h+=`<div class="hint" style="margin-bottom:8px">显示 ${list.length} 问</div>`;
  h+=list.map(examHTML).join("");
  $("#main").innerHTML=h;
}
function setExFilter(k){exFilter=k;renderExams();}
function setExSession(s){exSession=s;renderExams();}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("__PREFIX__-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("__PREFIX__-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("__PREFIX__-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}
let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:460px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:460px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 錯題重練 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做対即移出錯題本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">錯題本是空的——答錯的題會自動收進來 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">選擇訓練關卡</div>
      <div class="hint" style="margin-bottom:18px">真題填空＝历年真题原题；其余由 koou.json 自动生成</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:460px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交錯 · 全量隨機<br><span style="font-size:12px;color:var(--sub)">跨類型交錯練習</span></button>
      ${wc?`<button class="opt" style="max-width:240px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空錯題本？')){localStorage.removeItem('__PREFIX__-wrong');renderQuizTab();}">🗑️ 清空錯題本</button>`:""}
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
  qi=0;correct=0;answered=false;renderQ();
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
    <div class="q">${q.q}</div>${body}${optHTML}<div id="fb"></div></div>`;
}
function showNext(v){const b=$("#next");b.style.display=v?"inline-block":"none";b.disabled=!v;}
function pick(btn){
  if(answered)return;answered=true;
  const q=curQ();const ok=btn.dataset.ok==="1";
  if(ok)correct++;else addWrong(q.ref);
  if(ok&&mode==="wrong")delWrong(q.ref);
  document.querySelectorAll(".opt").forEach(b=>{b.disabled=true;if(b.dataset.ok==="1")b.classList.add("right");});
  if(!ok)btn.classList.add("wrong");
  $("#fb").innerHTML=`<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！"} ${q.exp||""}</div>`;
  showNext(true);updateScore();window.scrollTo(0,document.body.scrollHeight);
}
function nextQ(){answered=false;qi++;if(qi>=order.length)finish();else renderQ();}
function finish(){
  showNext(false);
  const total=order.length,pct=Math.round(correct/total*100);
  const msg=pct===100?"🏆 完璧！呼応・搭配を完全掌握！":pct>=70?"👍 かなりいい！錯題趁熱打鐵":"📖 詳解タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">別的關卡選一選</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正確率 ${pct}%`;window.scrollTo(0,0);
}
function backToBanks(){mode=null;render();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="pillars")renderPillars();
  else if(tab==="map")renderMap();
  else if(tab==="detail")renderDetail();
  else if(tab==="contrast")renderContrast();
  else if(tab==="exams")renderExams();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, groups, items, exams = load_data()
    print(f"[1/4] audio: TTS for choice exams...")
    audio = gen_audio(exams)
    print(f"[2/4] nadeshiko: downloading CDN audio...")
    nade_audio = gen_nade_audio(items)
    print(f"[3/4] generating quiz banks...")
    qs = build_questions(items, exams, audio)
    banks_meta = [[k, l] for k, l in meta.get("quizBanks", [
        ["exam", "🎯 真题填空"], ["recog", "📘 意味認識"], ["engine", "🔧 呼応拆解"],
        ["listen", "🎧 聴解判别"], ["judge", "⭕ 判断正誤"]])]
    from collections import Counter
    counts = Counter(q["bank"] for q in qs)
    for k, l in banks_meta:
        print(f"      {l}: {counts.get(k, 0)} 問")
    print(f"      合計: {sum(counts.values())} 問")

    # attach exams to items (display copy) and build furigana display copy
    ex_by_id = {e["id"]: e for e in exams}
    display = copy.deepcopy(items)
    for it in display:
        it["_exams"] = [ex_by_id[eid] for eid in it.get("exam_ids", []) if eid in ex_by_id]
    if HAS_KAKASI:
        for it in display:
            for ex in it.get("examples", []):
                ex["jp"] = add_furigana(ex["jp"])
            for sc in it.get("nadeshiko", []):
                sc["jp"] = add_furigana(sc["jp"])
        for e in exams:
            e["stem"] = add_furigana(e["stem"])
        print(f"      furigana applied")

    n_ex = sum(len(it.get("examples", [])) for it in items)
    n_nade = sum(len(it.get("nadeshiko", [])) for it in items)
    tags = (f"<span>{len(items)} パターン</span><span>{len(groups)} 類型</span>"
            f"<span>{len(exams)} 問 真題</span><span>{n_ex} 例文 + {n_nade} 原声</span>")

    print("[4/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__NADE_AUDIO__", j(nade_audio))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(display))
            .replace("__EXAMS__", j(exams))
            .replace("__BANKS__", j(banks_meta))
            .replace("__CONTRAST__", j(CONTRAST))
            .replace("__QS__", j(qs))
            .replace("__PREFIX__", PREFIX))
    OUT.write_text(html, encoding="utf-8")
    print(f"      wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
