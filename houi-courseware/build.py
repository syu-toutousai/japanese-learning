#!/usr/bin/env python3
"""Build the self-contained 「方角・方位 ・ 认知罗盘」(houi-courseware) HTML.

依据人脑空间认知规律编排方位词：罗盘绝对坐标（东南西北）＋ 自身坐标（左右上下）
＋ 方位派生词。数据写在 houi.json，重跑本脚本即重建卡片、罗盘图、发音与题库。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了句子会自动重录；某条合成失败只跳过该处播放键，不影响整体构建。
"""

import base64
import hashlib
import json
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "houi.json"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260828          # 固定随机种子：干扰项抽样可复现，重复构建 diff 干净
MIN_MP3 = 300            # 小于该字节数视为合成失败
BANK_META = [
    ["recog",  "📘 詞義認識"],
    ["fill",   "✍️ 対立填空"],
    ["listen", "🎧 聴解判別"],
    ["spatial", "🧭 空間判斷"],
    ["custom", "⭐ 自作題"],
]

# 罗盘上每个方位词应落在视觉罗盘里的哪个位置
COMPASS_ANGLES = {
    "north": 0, "northeast": 45, "east": 90, "southeast": 135,
    "south": 180, "southwest": 225, "west": 270, "northwest": 315,
    "all": 0,
}

KIND_LABEL = {"compass": "绝对坐标", "axis": "自身坐标", "derived": "派生词"}


def j(obj):
    """json for embedding inside <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] houi.json 不是合法 JSON：{e}")
    errors = []
    meta = data.get("meta") or {}
    groups = data.get("groups")
    items = data.get("items")
    special = data.get("special") or []
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
        label = iid or it.get("word", "?")
        if not iid:
            errors.append(f"条目「{label}」缺少 id")
        elif iid in seen:
            errors.append(f"条目 id 重复：{iid}")
        seen.add(iid)
        if iid and not all(c.isalnum() or c in "-_" for c in iid):
            errors.append(f"条目 id「{iid}」只能用字母数字-_")
        if it.get("group") not in gids:
            errors.append(f"「{label}」group「{it.get('group')}」不在 groups 里")
        for key in ("word", "read", "level", "meaning"):
            if not it.get(key):
                errors.append(f"「{label}」缺少 {key}")
        exs = it.get("examples") or []
        if not exs:
            errors.append(f"「{label}」至少需要一条 examples 例句")
        for i, ex in enumerate(exs):
            if not ex.get("jp"):
                errors.append(f"「{label}」第{i+1}条例句缺 jp")
        for n, q in enumerate(it.get("quizzes") or []):
            qt = q.get("type")
            if qt not in ("choice", "judge", "listen"):
                errors.append(f"「{label}」自作题#{n+1} type 必须是 choice/judge/listen")
            elif qt != "judge":
                if not q.get("opts") or not isinstance(q.get("ans"), int) \
                        or not 0 <= q["ans"] < len(q["opts"]):
                    errors.append(f"「{label}」自作题#{n+1} 需要 opts 和范围内的 ans")

    for s in special:
        if not isinstance(s, dict) or not s.get("id"):
            errors.append(f"special 条目需要 id")

    if errors:
        print("[!] houi.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, groups, items, special


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


def gen_audio(items, special, sents):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []
    for it in items:
        tasks.append((it["id"], it["read"]))
        for i, ex in enumerate(it.get("examples") or []):
            tasks.append((f"{it['id']}-e{i}", ex["jp"]))
    for sp in special:
        for i, ex in enumerate(sp.get("examples") or []):
            tasks.append((f"{sp['id']}-e{i}", ex["jp"]))

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


# ---------------------------------------------------------------- auto quizzes

def build_questions(groups, items, special, audio):
    rng = random.Random(SEED)
    dup_words = {}
    for it in items:
        dup_words[it["word"]] = dup_words.get(it["word"], 0) + 1

    def disp(it):
        return f'{it["word"]}（{it["read"]}）' if dup_words[it["word"]] > 1 else it["word"]

    def distractors(it, field):
        own = it[field]
        pool = [x[field] for x in items if x["id"] != it["id"] and x[field] != own]
        rng.shuffle(pool)
        out = []
        for cand in pool:
            if len(out) >= 3:
                break
            if cand not in out:
                out.append(cand)
        return out

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for it in items:
        iid = it["id"]
        note = it.get("anchor", "")

        # 📘 詞義認識
        add("recog", f"{iid}:recog", type="choice",
            q=f"方位词「{disp(it)}」表示什么？",
            opts=[it["meaning"]] + distractors(it, "meaning"), ans=0,
            exp=f'{it["word"]}（{it["read"]}）＝{it["meaning"]}'
                + (f"<br>🧠 {note}" if note else ""))

        # ✍️ 対立填空：把例句里的方位词挖掉选回去
        for i, ex in enumerate(it.get("examples") or []):
            blanked = ex["jp"].replace(it["word"], "（　）", 1)
            add("fill", f"{iid}:fill-{i}", type="choice",
                q=f'{blanked}<br><span class="hint">（　）里填回哪个方位词？</span>',
                opts=[disp(it)] + distractors(it, "word"), ans=0,
                exp=f'完整句子：{ex["jp"]}<br>{ex["cn"]}')
            break

        # 🎧 聴解判別
        exs = it.get("examples") or []
        if exs and f"{iid}-e0" in audio:
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 听音频：句子里说的是哪个方位词？",
                opts=[disp(it)] + distractors(it, "word"), ans=0,
                exp=f'原句：{exs[0]["jp"]}<br>{exs[0]["cn"]}'
                    + f'<br>{disp(it)}＝{it["meaning"]}')

        # 🧭 空間判斷（方位词专属脑内旋转/罗盘定位题）
        if it.get("dir") and it["dir"] in COMPASS_ANGLES and it["kind"] == "compass":
            deg = COMPASS_ANGLES[it["dir"]]
            add("spatial", f"{iid}:spatial", type="choice",
                q=f"🧭 在「上北・下南・左西・右东」的罗盘图上，{disp(it)} 大约在几点钟方向？",
                opts=[f"{deg} 点(正{['上','右上','右','右下','下','左下','左','左上'][deg//45]})",
                      f"{(deg+90)%360} 点钟方向", f"{(deg+45)%360} 点钟方向",
                      f"{(deg+180)%360} 点钟方向"], ans=0,
                exp=f'罗盘定位：东南西北按「上北下南左西右东」排布，{disp(it)} 在 {deg}°。\n'
                    f'（提示：真正记牢要能在心里旋转罗盘应对不同方位）')

        # ⭐ 自作題
        for n, cq in enumerate(it.get("quizzes") or []):
            q = dict(cq)
            aid = q.get("aid")
            if isinstance(aid, int):
                q["aid"] = f"{iid}-e{aid}"
            q.setdefault("exp", "")
            add("custom", f"{iid}:custom-{n}", **q)

    # special 卡片的自作题也进 custom / spatial 库
    for sp in special:
        for n, cq in enumerate(sp.get("quizzes") or []):
            q = dict(cq)
            q.setdefault("exp", "")
            add("spatial" if sp["kind"] == "mental-rotation" else "custom",
                f"{sp['id']}:sp-{n}", **q)

    return qs


# ---------------------------------------------------------------- html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>方角・方位 ・ 认知罗盘</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#0b4f8f,#1971c2);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 16px;position:relative;z-index:2}
nav button{flex:1;border:none;border-radius:12px;padding:12px 2px;font-size:14px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:1.7;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
/* intro + compass */
.intro{background:linear-gradient(135deg,#e7f1ff,#f0f6ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#1971c2}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#1971c2}
.compass-box{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;align-items:center}
@media (max-width:640px){.compass-box{grid-template-columns:1fr}}
.rose{position:relative;width:230px;height:230px;margin:0 auto}
.rose .ring{position:absolute;inset:0;border-radius:50%;border:3px solid #cfe0f5;
background:radial-gradient(circle,#eef4fd 55%,#dfeaf9 100%);
display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--sub)}
.rose .pt{position:absolute;width:54px;height:54px;border-radius:12px;background:#fff;
border:2px solid #bcd3f0;display:flex;flex-direction:column;align-items:center;justify-content:center;
box-shadow:0 3px 10px rgba(30,40,90,.14);cursor:pointer;transform:translate(-50%,-50%);transition:.15s}
.rose .pt:hover{transform:translate(-50%,-50%) scale(1.1);border-color:var(--acc)}
.rose .pt .em{font-size:15px}.rose .pt b{font-size:14px}
.rose .pt small{font-size:9.5px;color:var(--sub)}
.rose .pt.core{background:#1971c2;color:#fff;border-color:#1971c2;width:74px;height:74px}
.rose .pt.core small{color:#dcebff}
.rose .lab{position:absolute;transform:translate(-50%,-50%);color:var(--sub);
font-size:10px;font-weight:700;letter-spacing:.5px}
.rose .lab.N{color:#1971c2}
.coglist{display:flex;flex-direction:column;gap:9px}
.coglist b{color:#1971c2}
.coglist li{font-size:13px;line-height:1.7;margin-left:1em}
/* group & mini cards */
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-h p{font-size:12px;opacity:.95;font-weight:400;margin-top:3px;line-height:1.6}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#1971c2)}
.mini .em{font-size:26px}
.mini .nm{font-weight:800;font-size:16px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
/* special cards */
.special{border:2px dashed var(--gold);margin-top:20px;background:#fffdf5}
.special h3{font-size:15px;color:var(--gold);margin-bottom:8px}
.special .smean{background:#fff7e0;border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:8px 0}
/* detail cards */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#1971c2)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.chip.compass{background:#e7f1ff;color:#1971c2}
.chip.axis{background:#fff0e6;color:#e8590c}
.chip.derived{background:#e6fbf3;color:#0ca678}
.mini-btn{width:26px;height:26px;font-size:12px}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.anchor{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
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
</style>
</head>
<body>
<header><div class="wrap">
<h1>方角・方位 ・ 认知罗盘</h1>
<div class="kana">ほうがく・ほうい —— 東南西北・左右上下，和它们长出来的一串词 🧭</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">离线可用 · 点击🔊播放发音 · 大脑能记住方向，是因为它天生就是张地图</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const META=__META__;
const GROUPS=__GROUPS__;
const ITEMS=__ITEMS__;
const SPECIAL=__SPECIAL__;
const SENTS=__SENTS__;
const BANKS=__BANKS__;
const ANGLES=__ANGLES__;
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
function rowHTML(sid){
  const s=SENTS[sid];
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["list","🧭 罗盘"],["detail","📖 詳細"],["quiz","🎯 クイズ"]];
let tab="list";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- list: compass rose + cognitive intro ---------- */
const DIRNAME={north:"北",northeast:"北東",east:"東",southeast:"南東",south:"南",southwest:"南西",west:"西",northwest:"北西"};
const DIRREAD={north:"きた",northeast:"ほくとう",east:"ひがし",southeast:"なんとう",south:"みなみ",southwest:"なんせい",west:"にし",northwest:"ほくせい"};
function renderRose(){
  const pts=[
    {dir:"north",x:50,y:13},{dir:"northeast",x:76,y:24},
    {dir:"east",x:88,y:50},{dir:"southeast",x:76,y:76},
    {dir:"south",x:50,y:88},{dir:"southwest",x:24,y:76},
    {dir:"west",x:13,y:50},{dir:"northwest",x:24,y:24},
  ];
  let inner="";
  pts.forEach(p=>{
    const d=p.dir, name=p.dir.startsWith("north")?"北":p.dir.startsWith("south")?"南":p.dir.startsWith("east")?"東":p.dir.startsWith("west")?"西":p.dir;
    inner+=`<div class="pt" style="left:${p.x}%;top:${p.y}%" onclick="goDetail('${firstByDir(p.dir)}')">
      <b>${DIRNAME[d]}</b><small>${p.dir==="east"?"ひがし":p.dir==="west"?"にし":p.dir==="south"?"みなみ":p.dir==="north"?"きた":""}</small></div>`;
  });
  inner+=`<div class="pt core" style="left:50%;top:50%">🧭<small>方位</small></div>`;
  return `<div class="rose">${inner}
    <div class="lab N" style="left:50%;top:4%">N</div>
    <div class="lab" style="left:94%;top:50%">E</div>
    <div class="lab" style="left:50%;top:96%">S</div>
    <div class="lab" style="left:6%;top:50%">W</div>
  </div>`;
}
function firstByDir(dir){
  const el=ITEMS.find(it=>it.dir===dir)||ITEMS[0];
  return el?el.id:"";
}
function renderList(){
  let h=`<div class="card intro"><h2>你的大脑，天生就是一张地图 🧭</h2>
  <p>方位词不是死记的「词」，而是<code class="inline">空间坐标</code>。这套课件按人脑空间认知规律编排：先用
  <b>视觉罗盘</b>装下东南西北（绝对坐标），再靠<b>身体</b>锚定左右上下（自身坐标），最后用
  <b>对立结伴</b>与<b>组块</b>把派生词一学一串。</p>
  <div class="compass-box">
    ${renderRose()}
    <ul class="coglist">
      <li><b>双重编码</b>：每个方位同时给「字形＋读音＋图上位置」，左右脑各管一路。</li>
      <li><b>绝对 vs 自身</b>：东南西北随地球不随身体；左右上下随身体。先分清坐标系，就容易不搞混。</li>
      <li><b>对立结伴</b>：东西・南北・左右・上下——记一个反推一个，一个顶俩。</li>
      <li><b>心理旋转</b>：别被「向かって右/左」坑到，见倒数第二张卡。</li>
    </ul>
  </div></div>`;
  h+=GROUPS.map(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return "";
    return `<div class="grp"><div class="grp-h" style="background:${g.color}"><div><h3>${g.name}</h3>${g.note?`<p>${g.note}</p>`:""}</div><span>${members.length} 词</span></div>
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="em">${n.emoji||"📌"}</div>
        <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
        <div class="im">${shortMean(n.meaning)}</div></button>`).join("")}</div></div>`;
  }).join("");
  h+=`<div class="card special"><h3>🧠 两张「会转弯」的认知卡</h3>
  ${SPECIAL.map(sp=>`<div style="margin-top:12px">
    <div style="font-weight:800;font-size:15px">${sp.title}</div>
    <div class="smean">${sp.meaning}</div>
    <div class="anchor">🧠 ${sp.anchor}</div>
    ${(sp.examples||[]).map((_,i)=>rowHTML(`${sp.id}-e${i}`)).join("")}
  </div>`).join("")}
  </div>`;
  $("#main").innerHTML=h;
}
function shortMean(m){return m.split(/[：:；;，,（(]/)[0];}

/* ---------- detail ---------- */
function kindTxt(n){return KIND[n.kind]||KIND.derived;}
const KIND={compass:"绝对坐标",axis:"自身坐标",derived:"派生词"};
function renderDetail(){
  let h="";
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.name}</h3>`;
    members.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji||"📌"} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta"><span class="chip ${n.kind}">${kindTxt(n)}</span>
          <code>读作 ${n.read}</code>
          ${AUDIO[iid]?`<button class="btn mini-btn" title="听读音" onclick="play('${iid}',this)">▶</button>`:""}
        </div>
        <div class="meanbox">📌 <b>意思</b>　${n.meaning}</div>
        ${(n.examples||[]).map((_,i)=>rowHTML(`${iid}-e${i}`)).join("")}
        ${n.anchor?`<div class="anchor">🧠 记忆锚　${n.anchor}</div>`:""}
      </div>`;
    });
  });
  h+=`<div class="card special"><h3>🧠 认知卡</h3>
  ${SPECIAL.map(sp=>`<div style="margin-top:12px" id="n-${sp.id}">
    <div style="font-weight:800;font-size:15px">${sp.title}</div>
    <div class="smean">${sp.meaning}</div>
    <div class="anchor">🧠 ${sp.anchor}</div>
    ${(sp.examples||[]).map((_,i)=>rowHTML(`${sp.id}-e${i}`)).join("")}
  </div>`).join("")}
  </div>`;
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("houi-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("houi-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("houi-wrong",w);}
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
      <div class="hint" style="margin-bottom:18px">题目由 houi.json 自动生成 · 含脑内旋转的空间题 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨方位交错练习，方位间不互相遮蔽</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('houi-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
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
  const msg=pct===100?"🏆 完璧！方位全认全了！":pct>=70?"👍 かなりいい！错题趁热打铁":"🧭 回罗盘复习再用身体多感受，再战一轮";
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
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, groups, items, special = load_data()
    n_compass = sum(1 for i in items if i["kind"] == "compass")
    n_axis = sum(1 for i in items if i["kind"] == "axis")
    n_derived = sum(1 for i in items if i["kind"] == "derived")

    # 平铺例句表
    sents = {}
    for it in items:
        for i, ex in enumerate(it.get("examples") or []):
            sents[f"{it['id']}-e{i}"] = {"jp": ex["jp"], "cn": ex.get("cn", "")}
    for sp in special:
        for i, ex in enumerate(sp.get("examples") or []):
            sents[f"{sp['id']}-e{i}"] = {"jp": ex["jp"], "cn": ex.get("cn", "")}

    audio = gen_audio(items, special, sents)
    qs = build_questions(groups, items, special, audio)
    angles = {d: a for d, a in COMPASS_ANGLES.items()}

    tags = (f"<span>{len(items)} 方位词</span><span>罗盘 {n_compass}・自身 {n_axis}・派生 {n_derived}</span>"
            f"<span>{len(audio)} 音声</span><span>认知规律</span>")
    banks_meta = [[k, l] for k, l in BANK_META]

    print("[2/4] generating quiz banks...")
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, l in BANK_META:
        print(f"      {l}: {counts[k]} 問")

    print("[3/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__META__", j(meta))
            .replace("__AUDIO__", j(audio))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(items))
            .replace("__SPECIAL__", j(special))
            .replace("__SENTS__", j(sents))
            .replace("__BANKS__", j(banks_meta))
            .replace("__ANGLES__", j(angles))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[4/4] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
