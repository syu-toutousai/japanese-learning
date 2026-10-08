#!/usr/bin/env python3
"""Build the self-contained 「変体仮名店招帖」courseware HTML.

用法：改 hentaigana.json、往 assets/ 丢新招牌图 → python3 build.py → index.html 自动更新。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了文本会自动重录；某条合成失败只跳过该条发音，不影响整体构建。
"""

import base64
import hashlib
import json
import random
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "hentaigana.json"
ASSETS = ROOT / "assets"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

MIN_MP3 = 300
BANKS = [
    ["sign", "🏮 看板クイズ"],
    ["letter", "🔤 字母クイズ"],
    ["fill", "✍️ 穴埋め"],
    ["judge", "🤔 常識クイズ"],
    ["listen", "🎧 聴解クイズ"],
]


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] hentaigana.json 不是合法 JSON：{e}")
    errors = []
    meta = data.get("meta") or {}
    for key in ("title", "titleCn", "jp", "subtitle", "voiceFemale", "seed"):
        if not meta.get(key) and key != "seed":
            errors.append(f"meta 缺少 {key}")
    if not isinstance(meta.get("seed"), int):
        errors.append("meta.seed 必须是整数")
    asset_names = {p.name for p in ASSETS.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg")}
    for section in ("letters", "signs", "senses", "timeline", "trivia", "audio", "quizzes"):
        if not isinstance(data.get(section), list) or not data[section]:
            errors.append(f"缺少非空列表 {section}")
    letters = data.get("letters") or []
    seen = set()
    for it in letters:
        for key in ("letter", "kana", "kind", "note", "img"):
            if not it.get(key):
                errors.append(f"字母「{it.get('letter','?')}」缺少 {key}")
        if it.get("img") and it["img"] not in asset_names:
            errors.append(f"字母「{it.get('letter')}」的图 {it['img']} 不在 assets/")
        if it.get("letter") in seen:
            errors.append(f"字母重复：{it.get('letter')}")
        seen.add(it.get("letter"))
    audio_ids = {a.get("id") for a in data.get("audio") or []}
    for a in data.get("audio") or []:
        if not a.get("id") or not a.get("text"):
            errors.append(f"audio 条目缺 id/text：{a}")
    for s in data.get("signs") or []:
        for key in ("id", "title", "img", "reading", "story"):
            if not s.get(key):
                errors.append(f"看板「{s.get('id','?')}」缺少 {key}")
        if s.get("img") and s["img"] not in asset_names:
            errors.append(f"看板「{s.get('id')}」的图 {s['img']} 不在 assets/")
        if s.get("audio") and s["audio"] not in audio_ids:
            errors.append(f"看板「{s.get('id')}」的 audio「{s['audio']}」不在 audio 列表")
        for d in s.get("detail") or []:
            for key in ("letter", "kana", "note", "img"):
                if not d.get(key):
                    errors.append(f"看板「{s.get('id')}」明细缺 {key}")
    for q in data.get("quizzes") or []:
        if q.get("type") not in ("choice", "judge"):
            errors.append(f"自定义题 type 必须是 choice/judge：{q.get('q','?')[:20]}")
        elif q.get("type") == "choice" and (
                not q.get("opts") or not isinstance(q.get("ans"), int)
                or not 0 <= q["ans"] < len(q["opts"])):
            errors.append(f"自定义选择题需要 opts 和范围内 ans：{q.get('q','?')[:20]}")
        elif q.get("type") == "judge" and not isinstance(q.get("ans"), bool):
            errors.append(f"自定义判断题 ans 必须是布尔：{q.get('q','?')[:20]}")
    if errors:
        print("[!] hentaigana.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return data


# ---------------------------------------------------------------- TTS audio

def cache_path(aid, text):
    return AUDIO_DIR / f"{aid}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}.mp3"


def gen_one(task):
    aid, text, voice, rate = task
    out = cache_path(aid, text)
    for _ in range(3):
        try:
            subprocess.run(
                ["edge-tts", "--voice", voice, f"--rate={rate}", "--text", text,
                 "--write-media", str(out)],
                check=True, capture_output=True, timeout=60)
            if out.exists() and out.stat().st_size > MIN_MP3:
                return aid, True
        except Exception:
            pass
    return aid, False


def gen_audio(data):
    AUDIO_DIR.mkdir(exist_ok=True)
    voice = data["meta"]["voiceFemale"]
    rate = "-6%"
    tasks = [(a["id"], a["text"], voice, rate) for a in data["audio"]]
    todo = [t for t in tasks if not cache_path(t[0], t[1]).exists()]
    print(f"[1/3] audio: {len(tasks)} clips, {len(tasks)-len(todo)} cached, {len(todo)} to synthesize...")
    if todo:
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
        failed = [aid for aid, ok in results.items() if not ok]
        if failed:
            print(f"[!] {len(failed)} 条发音合成失败（课件照常生成，只是这几处没有播放键）：{failed}")
    keep = {cache_path(aid, text).name for aid, text, _, _ in tasks}
    for p in AUDIO_DIR.glob("*.mp3"):
        if p.name not in keep:
            p.unlink()
    audio = {}
    for aid, text, _, _ in tasks:
        p = cache_path(aid, text)
        if p.exists() and p.stat().st_size > MIN_MP3:
            audio[aid] = "data:audio/mpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    return audio


# ---------------------------------------------------------------- assets

def load_assets():
    mimes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    img = {}
    for p in sorted(ASSETS.iterdir()):
        if p.suffix.lower() not in mimes:
            continue
        img[p.name] = f"data:{mimes[p.suffix.lower()]};base64," + base64.b64encode(p.read_bytes()).decode()
    return img


# ---------------------------------------------------------------- questions

def build_questions(data, audio, img):
    rng = random.Random(data["meta"]["seed"])
    letters = data["letters"]
    signs = data["signs"]
    kana_pool = sorted({l["kana"] for l in letters})
    sign_pool = [s["signText"] for s in signs if s.get("signText")]
    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for s in signs:
        own = s["reading"]
        cands = [x["reading"] for x in signs if x["reading"] != own]
        for extra in ("うどん", "すし", "ところてん"):
            if extra not in cands:
                cands.append(extra)
        rng.shuffle(cands)
        opts = [own] + cands[:3]
        breakdown = "、".join(f'{d["letter"]}＝{d["kana"]}' for d in s.get("detail") or [])
        add("sign", f'{s["id"]}:sign', type="choice",
            q=f'这块暖簾读什么？<br><span class="hint">猜不出就点开「しくみ」补课。</span>',
            qimg=s["img"], opts=opts, ans=0,
            exp=f'{s["title"]}＝{own}（{s["meaning"]}）' + (f'<br>{breakdown}' if breakdown else ""))

    for l in letters:
        own = l["kana"]
        cands = [k for k in kana_pool if k != own]
        rng.shuffle(cands)
        opts = [own] + cands[:3]
        add("letter", f'{l["letter"]}:letter', type="choice",
            q=f'字母「{l["letter"]}」对应哪个音？',
            qimg=l["img"], opts=opts, ans=0,
            exp=f'{l["letter"]}＝{own}（{l["kind"]}）<br>💡 {l["note"]}')

    for i, q in enumerate(data["quizzes"]):
        add(q.get("bank", "custom"), f'custom:{i}', type=q["type"], q=q["q"],
            opts=q.get("opts"), ans=q["ans"], exp=q.get("exp", ""))

    if audio.get("hentaigana"):
        add("listen", "concept:listen", type="listen", aid="hentaigana",
            q="🎧 听到的这个词，指的是什么？",
            opts=["平假名的旧字体群", "一种荞麦面", "书法展的名字", "江户时代的敬语"], ans=0,
            exp="変体仮名＝平假名里未被1900年字体统一选上的旧字体（异体假名）。")
    for s in signs:
        if s.get("audio") in audio and s.get("signText"):
            own = s["signText"]
            cands = [x for x in sign_pool if x != own]
            opts = [own] + cands[:3]
            add("listen", f'{s["id"]}:listen', type="listen", aid=s["audio"],
                q="🎧 听发音，选出对应的暖簾文字。", opts=opts, ans=0,
                exp=f'{own}＝{s["reading"]}（{s["meaning"]}）')
    return qs


# ---------------------------------------------------------------- HTML fragments

def link_audio(aid, label, audio):
    if aid not in audio:
        return ""
    return f'<button class="btn play" data-aid="{aid}">▶ {label}</button>'


def html_signs(data, img, audio):
    out = []
    for s in data["signs"]:
        detail = ""
        if s.get("detail"):
            cards = []
            for d in s["detail"]:
                cards.append(
                    f'<div class="lcard"><img data-img="{d["img"]}" alt="{d["letter"]}">'
                    f'<div class="lk"><b>{d["letter"]}</b><span class="kana">{d["kana"]}</span></div>'
                    f'<p>{d["note"]}</p></div>')
            detail = f'<div class="ldetail">{"".join(cards)}</div>'
        extra = ""
        if s.get("extraImg"):
            e = s["extraImg"]
            extra = (f'<figure class="extra"><img data-img="{e["src"]}" alt="">'
                     f'<figcaption>{e["caption"]}</figcaption></figure>')
        chips = "".join(f'<span class="chip">{c}</span>' for c in s.get("chips") or [])
        reading_btns = link_audio(s.get("audio"), s["reading"], audio)
        if s.get("readingAlt"):
            reading_btns += link_audio("namakisoba" if "なま" in s["readingAlt"] else "",
                                       s["readingAlt"], audio)
        sources = ""
        if s.get("sources"):
            srcs = "".join(f'<a class="src" href="{x["url"]}" target="_blank" rel="noopener">{x["label"]}</a>'
                           for x in s["sources"])
            sources = f'<div class="sources">参考：{srcs}</div>'
        out.append(f'''<article class="sigcard card">
  <div class="sigimg"><img data-img="{s["img"]}" alt="{s["title"]}"></div>
  <div class="sigbody">
    <h3>{s["emoji"]} {s["title"]}</h3>
    <div class="sigmeta">{chips}
      <span class="rd">{s["reading"]}</span>
      <span class="mean">＝ {s["meaning"]}</span>
      {reading_btns}
    </div>
    <div class="story">{s["story"]}</div>
    {detail}
    {extra}
    {sources}
  </div>
</article>''')
    return "\n".join(out)


def html_letters(data, img):
    cards = []
    for l in data["letters"]:
        cls = "std" if l["kind"].startswith("現行") else "var"
        cards.append(f'''<div class="lcard wide">
  <img data-img="{l["img"]}" alt="{l["letter"]}">
  <div class="lk"><b>{l["letter"]}</b><span class="kana">{l["kana"]}</span></div>
  <span class="kind {cls}">{l["kind"]}</span>
  <p>{l["note"]}</p>
</div>''')
    return "\n".join(cards)


def html_senses(data):
    out = []
    for s in data["senses"]:
        out.append(f'<div class="sense"><h4>{s["title"]}</h4><p>{s["text"]}</p></div>')
    return "\n".join(out)


def html_timeline(data):
    out = []
    for t in data["timeline"]:
        out.append(f'<div class="tl"><span class="era">{t["era"]}</span><p>{t["text"]}</p></div>')
    return "\n".join(out)


def html_trivia(data):
    out = []
    for t in data["trivia"]:
        out.append(f'<div class="triv"><h4>{t["emoji"]} {t["title"]}</h4><p>{t["text"]}</p></div>')
    return "\n".join(out)


# ---------------------------------------------------------------- template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ ・ 変体仮名店招帖</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;--gold:#b8860b;
--ai:#24356b;--ai2:#3a4f96;--paper:#f7f4ec}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);line-height:1.7}
header{background:linear-gradient(160deg,#1c2a52,#24356b 55%,#3a4f96);color:#fff;padding:26px 18px 70px;text-align:center}
header .noren{max-width:760px;margin:0 auto 18px;border-radius:14px;overflow:hidden;
box-shadow:0 18px 44px rgba(8,14,40,.45);border:1px solid rgba(255,255,255,.18)}
header .noren img{display:block;width:100%}
header h1{font-size:30px;letter-spacing:6px;font-family:"Hiragino Mincho ProN","Yu Mincho",serif}
header .jp{font-size:13px;opacity:.85;letter-spacing:3px;margin-top:4px}
header p.sub{margin-top:10px;font-size:14.5px;opacity:.95}
header .tags span{display:inline-block;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);
border-radius:99px;padding:3px 12px;font-size:12px;margin:12px 4px 0}
main{max-width:1000px;margin:-46px auto 0;padding:0 16px 60px}
.card{background:var(--card);border-radius:18px;box-shadow:0 6px 24px rgba(30,40,90,.10)}
nav.tabs{display:flex;gap:8px;flex-wrap:wrap;background:rgba(255,255,255,.92);border:1px solid var(--line);
border-radius:16px;padding:8px;margin-bottom:18px;position:sticky;top:8px;z-index:30;
-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
nav.tabs button{flex:1;min-width:130px;border:0;background:transparent;border-radius:12px;padding:11px 8px;
font-size:14.5px;font-weight:700;color:var(--sub);cursor:pointer;transition:.15s;font-family:inherit}
nav.tabs button.on{background:var(--ai);color:#fff;box-shadow:0 6px 16px rgba(36,53,107,.35)}
section.tab{display:none}
section.tab.on{display:block}
h2.sec{font-size:19px;margin:26px 4px 12px;letter-spacing:1px}
h2.sec small{font-size:12.5px;color:var(--sub);font-weight:400;margin-left:8px;letter-spacing:0}
.btn{border:0;border-radius:10px;background:var(--ai);color:#fff;padding:7px 14px;font-size:13.5px;
font-weight:700;cursor:pointer;font-family:inherit;transition:.15s}
.btn:hover{filter:brightness(1.12);transform:translateY(-1px)}
.btn.play{background:#fff;color:var(--ai);border:1.5px solid var(--ai)}
.btn.playing{background:var(--gold);border-color:var(--gold);color:#fff;animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.04)}}
.sigcard{overflow:hidden;margin-bottom:22px}
.sigimg{background:#182348}
.sigimg img{display:block;width:100%}
.sigbody{padding:20px 22px 18px}
.sigbody h3{font-size:18.5px;letter-spacing:1px}
.sigmeta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0 6px}
.chip{display:inline-block;background:var(--ai);color:#fff;border-radius:8px;padding:1px 10px;
font-size:15px;font-weight:700;font-family:"Hiragino Mincho ProN","Yu Mincho",serif}
.rd{font-size:20px;font-weight:800;color:var(--ai);font-family:"Hiragino Mincho ProN","Yu Mincho",serif}
.mean{color:var(--sub);font-size:14px}
.story{margin:10px 0 4px;font-size:14.5px}
.story b{color:var(--ai)}
.story br{line-height:2.1}
.ldetail{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0 4px}
.lcard{background:var(--paper);border:1px solid #e8e2d2;border-radius:14px;padding:10px;text-align:center}
.lcard img{width:88px;border-radius:10px;display:block;margin:0 auto 8px}
.lcard.wide img{width:72px}
.lcard .lk b{font-size:26px;font-family:"Hiragino Mincho ProN","Yu Mincho",serif;color:var(--ai);vertical-align:-2px}
.lcard .lk .kana{font-size:17px;font-weight:800;color:var(--gold);margin-left:8px}
.lcard p{font-size:12.5px;color:var(--sub);text-align:left;margin-top:6px;line-height:1.65}
.lcard .kind{display:inline-block;font-size:11px;border-radius:99px;padding:1px 9px;margin-top:4px;font-weight:700}
.kind.var{background:#eee7ff;color:#6b46c1}
.kind.std{background:var(--okbg);color:var(--ok)}
.extra{margin:14px 0}
.extra img{width:100%;max-width:560px;border-radius:12px;display:block;margin:0 auto}
.extra figcaption{text-align:center;font-size:12.5px;color:var(--sub);margin-top:8px}
.sources{margin-top:12px;font-size:12px;color:var(--sub);line-height:2}
.sources a{color:var(--acc);text-decoration:none;background:var(--acc2);border-radius:99px;padding:2px 10px;margin:0 4px}
.intro{font-size:15px;padding:22px 24px}
.intro .hook{font-size:17px;font-weight:800;color:var(--ai);font-family:"Hiragino Mincho ProN","Yu Mincho",serif;
background:var(--paper);border-left:5px solid var(--gold);border-radius:10px;padding:14px 16px;white-space:pre-line;line-height:2}
.intro p{margin:14px 0 0;white-space:pre-line}
.intro .note{margin-top:14px;background:var(--okbg);border-radius:10px;padding:10px 14px;font-size:13.5px;color:#155e3a}
.narbtns{margin-top:14px;display:flex;gap:8px;flex-wrap:wrap}
.sense{background:var(--card);border-radius:14px;padding:16px 18px;margin-bottom:12px;
box-shadow:0 4px 16px rgba(30,40,90,.07);border-left:5px solid var(--ai2)}
.sense h4{font-size:15.5px;margin-bottom:6px;color:var(--ai)}
.sense p{font-size:13.5px;color:var(--sub)}
.letters{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
.tlgrid{position:relative;margin-left:10px;border-left:3px solid #d9dcec;padding-left:20px}
.tl{position:relative;margin-bottom:16px}
.tl::before{content:"";position:absolute;left:-29px;top:7px;width:13px;height:13px;border-radius:50%;
background:var(--ai);border:3px solid #fff;box-shadow:0 0 0 2px #d9dcec}
.tl .era{display:inline-block;background:var(--ai);color:#fff;border-radius:8px;padding:1px 10px;
font-size:12.5px;font-weight:700;margin-bottom:4px}
.tl p{font-size:13.5px;color:var(--sub)}
.trivia{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.triv{background:var(--card);border-radius:14px;padding:16px 18px;box-shadow:0 4px 16px rgba(30,40,90,.07)}
.triv h4{font-size:15px;margin-bottom:6px}
.triv p{font-size:13.5px;color:var(--sub)}
.qwrap{padding:20px 22px}
.modes{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.modes .btn{background:#fff;color:var(--ai);border:1.5px solid var(--ai)}
.modes .btn.on{background:var(--ai);color:#fff}
.modes .btn.warn{color:var(--ng);border-color:var(--ng)}
.q{font-size:15.5px;font-weight:700;margin-bottom:12px}
.qimg{max-width:460px;width:100%;border-radius:12px;display:block;margin:0 0 14px;box-shadow:0 6px 18px rgba(20,30,80,.18)}
.opt{display:block;width:100%;text-align:left;background:#fff;border:1.5px solid var(--line);border-radius:12px;
padding:11px 14px;margin-bottom:9px;font-size:14.5px;cursor:pointer;transition:.15s;font-family:inherit}
.opt:hover{border-color:var(--acc);background:var(--acc2)}
.opt.ok{background:var(--okbg);border-color:var(--ok);color:var(--ok);font-weight:700}
.opt.ng{background:var(--ngbg);border-color:var(--ng);color:var(--ng)}
.opt:disabled{cursor:default}
.exp{background:var(--paper);border-radius:12px;padding:12px 14px;font-size:13.5px;margin:6px 0 12px}
.exp b{color:var(--ai)}
.bar{display:flex;align-items:center;gap:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:6px}
.barinfo{font-size:13.5px;color:var(--sub);flex:1}
.fin{text-align:center;padding:14px 4px 4px;font-size:16px;font-weight:800;color:var(--ai)}
.hint{font-size:12.5px;color:var(--sub);font-weight:400}
footer{text-align:center;font-size:12px;color:var(--sub);padding:22px;line-height:2}
footer code{background:#eceff7;border-radius:6px;padding:1px 7px}
@media (max-width:700px){nav.tabs button{min-width:unset;flex:1 1 40%;font-size:13px}}
</style>
</head>
<body>
<header>
  <div class="noren"><img data-img="sign_kisoba.jpg" alt="幾楚者の暖簾"></div>
  <h1>変体仮名店招帖</h1>
  <div class="jp">へんたいがな ・ てんしょうちょう</div>
  <p class="sub">__SUBTITLE__</p>
  <div class="tags"><span>きそば＝幾楚者</span><span>字母 __NLETTERS__ 枚</span><span>看板 __NSIGNS__ 块</span><span>クイズ __NQ__ 問</span></div>
</header>
<main>
  <nav class="tabs" id="tabs">
    <button data-tab="signs" class="on">🏮 看板帖</button>
    <button data-tab="shikumi">🗺️ しくみ</button>
    <button data-tab="monogatari">📜 ものがたり</button>
    <button data-tab="quiz">🎯 クイズ</button>
  </nav>

  <section class="tab on" id="tab-signs">
    <h2 class="sec">🏮 看板帖 <small>老铺暖簾逐个解码 · 点 ▶ 听读音</small></h2>
    __SIGNS__
  </section>

  <section class="tab" id="tab-shikumi">
    <h2 class="sec">🗺️ しくみ <small>変体仮名是什么、怎么用</small></h2>
    <div class="intro card">
      <div class="hook">__HOOK__</div>
      <p>__BODY__</p>
      <div class="note">💡 __NOTE__</div>
      <div class="narbtns">__INTRO_AUDIO__</div>
    </div>
    <h2 class="sec">🧭 使い分けの四原則 <small>当年怎么挑字形</small></h2>
    __SENSES__
    <h2 class="sec">🔤 字母表 <small>招牌上常见字体的「本体」——点图不响，但值得端详</small></h2>
    <div class="letters">__LETTERS__</div>
  </section>

  <section class="tab" id="tab-monogatari">
    <h2 class="sec">📜 ものがたり <small>从平安女手到电脑Unicode</small></h2>
    <div class="tlgrid">__TIMELINE__</div>
    <h2 class="sec">🎲 まめ知識 <small>拿去吓朋友一跳的小知识</small></h2>
    <div class="trivia">__TRIVIA__</div>
    <h2 class="sec">🎵 いろは <small>拍子木一响，江户登场</small></h2>
    <div class="card" style="padding:16px 20px">__IROHA_AUDIO__ <span class="hint">いろは歌的开头——旧时代的「ABC」。ゐ・ゑ・を这些旧假名也唱在这里面。</span></div>
  </section>

  <section class="tab" id="tab-quiz">
    <h2 class="sec">🎯 クイズ <small>错了会自动进错题本（localStorage）</small></h2>
    <div class="card qwrap">
      <div class="modes" id="modes"></div>
      <div id="qbox"></div>
      <div class="bar"><span class="barinfo" id="barinfo"></span>
        <button class="btn" id="next" disabled>つぎへ</button>
        <span class="barinfo" id="score" style="flex:0 0 auto"></span></div>
    </div>
  </section>
</main>
<footer>
  <div>変体仮名店招帖 · 个人学习课件（单文件离线可用）</div>
  <div>图片素材：実拍照片（Wikimedia Commons／tenki.jp／朝日新聞ことばマガジン／fv1.jp 等）＋真实変体仮名字形（Unicode／Wikimedia），出处与许可见各卡片及 README。</div>
  <div>数据随手改：<code>hentaigana.json</code> → <code>python3 build.py</code> → 重建本页</div>
</footer>
<script>
const AUDIO = __AUDIO_JS__;
const QUIZ = __QUIZ_JS__;
const IMG = __IMG_JS__;
const BANKS = __BANKS_JS__;
const $ = s => document.querySelector(s);

document.querySelectorAll('img[data-img]').forEach(im => { if (IMG[im.dataset.img]) im.src = IMG[im.dataset.img]; });

document.querySelectorAll('#tabs button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('section.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + b.dataset.tab));
}));

let cur = null;
function stopAudio() {
  document.querySelectorAll('audio').forEach(a => a.pause());
  document.querySelectorAll('.playing').forEach(x => x.classList.remove('playing'));
  cur = null;
}
document.querySelectorAll('[data-aid]').forEach(btn => btn.addEventListener('click', () => {
  const aid = btn.dataset.aid;
  if (cur && cur.aid === aid) { stopAudio(); return; }
  stopAudio();
  if (!AUDIO[aid]) return;
  const a = new Audio(AUDIO[aid]);
  cur = { aid, a };
  btn.classList.add('playing');
  a.addEventListener('ended', stopAudio);
  a.play();
}));

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
function wrongSet() {
  try { return new Set(JSON.parse(localStorage.getItem('hentaigana-wrong') || '[]')); }
  catch (e) { return new Set(); }
}
function saveWrong(s) { localStorage.setItem('hentaigana-wrong', JSON.stringify([...s])); }

let order = [], qi = 0, correct = 0, answered = false, mode = 'mix';

function poolSize(bank) {
  if (bank === 'mix') return QUIZ.length;
  if (bank === 'wrong') return wrongSet().size;
  return QUIZ.filter(q => q.bank === bank).length;
}
function counts(bank) { return QUIZ.filter(q => q.bank === bank).length; }

function renderModes() {
  const el = $('#modes');
  el.innerHTML = '';
  BANKS.forEach(([bank, label]) => {
    const b = document.createElement('button');
    b.className = 'btn' + (mode === bank ? ' on' : '');
    b.textContent = label + ' (' + counts(bank) + ')';
    b.addEventListener('click', () => start(bank));
    el.appendChild(b);
  });
  [['mix', '🌀 混合'], ['wrong', '📕 错题 (' + wrongSet().size + ')']].forEach(([bank, label]) => {
    const b = document.createElement('button');
    b.className = 'btn' + (mode === bank ? ' on' : '');
    b.textContent = label;
    b.addEventListener('click', () => start(bank));
    el.appendChild(b);
  });
  const clear = document.createElement('button');
  clear.className = 'btn warn';
  clear.textContent = '🗑 清空错题本';
  clear.addEventListener('click', () => { saveWrong(new Set()); renderModes(); });
  el.appendChild(clear);
}

function start(bank) {
  mode = bank;
  let idxs;
  if (bank === 'mix') idxs = QUIZ.map((_, i) => i);
  else if (bank === 'wrong') idxs = [...wrongSet()];
  else idxs = QUIZ.map((_, i) => i).filter(i => QUIZ[i].bank === bank);
  order = shuffle(idxs);
  qi = 0; correct = 0;
  renderModes();
  if (!order.length) {
    $('#qbox').innerHTML = '<div class="fin">这个池子还是空的——先去做几题，错题会自己攒进来。</div>';
    $('#barinfo').textContent = '';
    $('#next').disabled = true;
    return;
  }
  renderQ();
}

function renderQ() {
  answered = false;
  const q = QUIZ[order[qi]];
  const box = $('#qbox');
  let html = '';
  if (q.qimg && IMG[q.qimg]) html += '<img class="qimg" src="' + IMG[q.qimg] + '" alt="">';
  html += '<div class="q">' + q.q + '</div>';
  if (q.type === 'listen') {
    if (AUDIO[q.aid]) html += '<div style="margin-bottom:12px"><button class="btn play" data-aid="' + q.aid + '">▶ もう一度闻く</button></div>';
    else html += '<div class="hint" style="margin-bottom:12px">（音频未生成，请读拼音想）</div>';
  }
  box.innerHTML = html + '<div id="opts"></div><div id="exp"></div>';

  const optsEl = $('#opts');
  let opts;
  if (q.type === 'judge') {
    opts = [{ t: '⭕ 正しい（真的）', ok: q.ans === true }, { t: '❌ 間違い（假的）', ok: q.ans === false }];
  } else {
    opts = shuffle(q.opts.map((t, i) => ({ t, ok: i === q.ans })));
  }
  opts.forEach(o => {
    const b = document.createElement('button');
    b.className = 'opt';
    b.textContent = o.t;
    b.dataset.ok = o.ok ? '1' : '0';
    b.addEventListener('click', () => answer(b, o.ok, q));
    optsEl.appendChild(b);
  });
  $('#barinfo').textContent = '第 ' + (qi + 1) + ' / ' + order.length + ' 問';
  $('#score').textContent = '✅ ' + correct;
  $('#next').disabled = true;
  $('#next').textContent = qi + 1 === order.length ? '集計' : 'つぎへ';

  box.querySelectorAll('[data-aid]').forEach(btn => btn.addEventListener('click', e => {
    e.stopPropagation();
    const aid = btn.dataset.aid;
    if (cur && cur.aid === aid) { stopAudio(); return; }
    stopAudio();
    if (!AUDIO[aid]) return;
    const a = new Audio(AUDIO[aid]);
    cur = { aid, a };
    btn.classList.add('playing');
    a.addEventListener('ended', stopAudio);
    a.play();
  }));
}

function answer(btn, ok, q) {
  if (answered) return;
  answered = true;
  btn.parentNode.querySelectorAll('.opt').forEach(b => {
    b.disabled = true;
    const isRight = (q.type === 'judge')
      ? (b.textContent.startsWith('⭕') === (q.ans === true))
      : b.dataset.ok === '1';
    if (isRight) b.classList.add('ok');
    else if (b === btn) b.classList.add('ng');
  });
  if (ok) correct++;
  const i = order[qi];
  const ws = wrongSet();
  if (ok) ws.delete(i); else ws.add(i);
  saveWrong(ws);
  $('#exp').innerHTML = '<div class="exp">' + (ok ? '✅ 正解！' : '❌ 答错了，已记入错题本。') + (q.exp || '') + '</div>';
  $('#score').textContent = '✅ ' + correct;
  $('#next').disabled = false;
}

$('#next').addEventListener('click', () => {
  if (qi + 1 >= order.length) {
    const total = order.length;
    $('#qbox').innerHTML = '<div class="fin">' + (correct === total ? '🏆 全問正解！' : '结果：✅ ' + correct + ' / ' + total + (correct / total >= 0.8 ? ' ——老铺のれん、任你读。' : ' ——错题已进错题本，再来一轮。')) + '</div>';
    $('#barinfo').textContent = '';
    $('#next').disabled = true;
    $('#next').textContent = 'つぎへ';
    renderModes();
    return;
  }
  qi++;
  renderQ();
});

renderModes();
start('mix');
</script>
</body>
</html>
"""


def main():
    data = load_data()
    audio = gen_audio(data)
    img = load_assets()
    questions = build_questions(data, audio, img)
    print(f"[2/3] assets: {len(img)} images, quiz: {len(questions)} questions")
    counts = {b: sum(1 for q in questions if q["bank"] == b) for b, _ in BANKS}
    banks_js = {b: c for b, c in counts.items() if c}
    html = (TEMPLATE
            .replace("__TITLE__", data["meta"]["title"])
            .replace("__SUBTITLE__", data["meta"]["subtitle"])
            .replace("__NLETTERS__", str(len(data["letters"])))
            .replace("__NSIGNS__", str(len(data["signs"])))
            .replace("__NQ__", str(len(questions)))
            .replace("__HOOK__", data["intro"]["hook"])
            .replace("__BODY__", data["intro"]["body"])
            .replace("__NOTE__", data["intro"]["note"])
            .replace("__INTRO_AUDIO__", link_audio("nar1", "▶ 昔の仮名の話", audio)
                     + link_audio("nar2", "▶ 江戸の暖簾の話", audio))
            .replace("__SIGNS__", html_signs(data, img, audio))
            .replace("__SENSES__", html_senses(data))
            .replace("__LETTERS__", html_letters(data, img))
            .replace("__TIMELINE__", html_timeline(data))
            .replace("__TRIVIA__", html_trivia(data))
            .replace("__IROHA_AUDIO__", link_audio("iroha", "▶ いろは歌（开头）", audio))
            .replace("__AUDIO_JS__", j(audio))
            .replace("__QUIZ_JS__", j(questions))
            .replace("__IMG_JS__", j(img))
            .replace("__BANKS_JS__", j([[b, l] for b, l in BANKS])))
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"[3/3] wrote {OUT} ({size:.0f} KB)")
    print("      banks:", counts, "| audio:", len(audio), "| wrong-book key: hentaigana-wrong")


if __name__ == "__main__":
    main()
