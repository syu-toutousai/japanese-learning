#!/usr/bin/env python3
"""Build the self-contained 「断定の「に」完全体系」 courseware HTML.

用法：往 ni.json 里加一条语法点 → 运行本脚本 → index.html 自动长出
卡片、发音和题库。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/，
改了句子会自动重录；某条合成失败只跳过该条发音，不影响整体构建。
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
DATA = ROOT / "ni.json"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260909
MIN_MP3 = 300


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


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
        ]
        # pick the right one based on group
        engine_map = {
            "suru": 0, "yoru": 1, "vector": 0, "relate": 2, "progress": 3
        }
        correct_engine = engine_map.get(it["group"], 0)
        add("engine", f"{iid}:engine", type="choice",
            q=f"「{it['word']}」中的「に」扮演什么角色？",
            opts=engine_opts, ans=correct_engine,
            exp=f'🔧 {it["engine"]}<br>💡 {it["blueprint"]}')

        # ✍️ 运用填空：例句挖空选回
        for i, ex in enumerate(it.get("examples") or []):
            # try to find the pattern in the sentence
            pattern = it["read"].replace("〜", "")
            if pattern and pattern in ex["jp"]:
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

        # ⭕ 判断正误
        add("judge", f"{iid}:judge-jp", type="judge",
            q=f"「{it['word']}」的 engine 是：{it['engine']}",
            ans=True,
            exp=f'正确！{it["blueprint"]}')

    # 额外的判断题
    extra_judges = [
        ('「〜にしては」表示「即使…也…」。', False,
         '错误。にしては = 「就…而言却…」。表示「即使…也…」的是にしても。'),
        ('「〜によって」可以表示「因…而异」。', True,
         '正确。によって三大义之一就是「因人而异」。'),
        ('「〜に先立って」比「〜に際して」更正式书面。', False,
         '错误。に際して更正式书面，常用于公告致辞。'),
        ('「〜に反して」常搭配期待、予想。', True,
         '正确。反して = 预期 vs 现实形成反差。'),
        ('「〜につれて」和「〜にともなって」完全相同。', False,
         '错误。にともなって更书面，更强调因果捆绑。'),
        ('「〜に対して」既有「对…」也有「与…相反」的含义。', True,
         '正确。两大义：①动作承受对象 ②对比/相反。'),
        ('「〜にあって」的「あって」来自古典动词「ある」。', True,
         '正确。あって = あり（存在）的te形。'),
        ('「〜にあたって」比「〜に際して」更强调客观描述。', False,
         '错误。にあたって更强调主观能动性。'),
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
<title>断定の「に」完全体系</title>
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
.jp{font-size:16.5px;line-height:1.7;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
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
</style>
</head>
<body>
<header><div class="wrap">
<h1>断定の「に」完全体系</h1>
<div class="kana">だんていの「に」 —— 从 N5 到 N1 的统一语法引擎 ⚡</div>
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
const GROUPS=__GROUPS__;
const ITEMS=__ITEMS__;
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
function rowHTML(sid,s){
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["map","🗺️ 体系図"],["detail","📖 詳解"],["contrast","🔍 対比"],["sentences","📝 例文"],["quiz","🎯 クイズ"]];
let tab="map";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- map (hub-spoke) ---------- */
function renderMap(){
  let h=`<div class="card intro"><h2>Hub-and-Spoke：一个引擎，五条辐线</h2>
  <p>70%以上的中高级日语接续语法，都是断定の「に」在做不同风格的力学支撑。
  <b>Hub（轴心）</b>就是断定の「に」，<b>Spokes（辐线）</b>是描述你对该现实的认知动作的动词。</p>
  <div class="steps">
    <div><b>Master Formula</b><br>[名词短语] + [（断定）に] + [语法化动词] + [可选助词]</div>
    <div><b>核心洞察</b><br>N5→N1 不是五座独立的山，而是同一棵wheel的不同spoke。</div>
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
      <div class="count">${members.length} 个语法点</div>
    </div>`;
  });
  h+=`</div></div></div>`;
  // group overview
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.name}</h3><span>${members.length} 点</span></div>
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
        <div class="meta"><code>读作 ${n.read}</code>
          ${AUDIO[`${iid}-e0`]?`<button class="btn mini-btn" title="听例句发音" onclick="play('${iid}-e0',this)">▶</button>`:""}
        </div>
        <div class="engine-box"><b>🔧 Engine</b>　${n.engine}</div>
        <div class="blueprint-box"><b>📐 Blueprint</b>　${n.blueprint}</div>
        <div class="meanbox">📌 <b>意思</b>　${n.meaning}</div>
        <h3 class="sec">例句</h3>
        ${(n.examples||[]).map((ex,i)=>rowHTML(`${iid}-e${i}`,ex)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- contrast ---------- */
function renderContrast(){
  let h=`<div class="card intro"><h2>Group级対比：五条Lineage的力学差异</h2>
  <p>同一个「に」，搭配不同的动词，认知力学完全不同。下面按group逐一对比。</p></div>`;
  const contrasts=[
    {c:"#e74c3c",title:"する系 vs 他系",items:[
      ["にする (N5)","锁定选项 → 敲定","核心：主观决定"],
      ["にしては (N3)","锁定事实 → 但出现意外","核心：事实基线 vs 预期反差"],
      ["にしても (N2)","完全承认 → 结论不变","核心：退让让步"],
    ]},
    {c:"#3498db",title:"よる系：信息源 vs 方法",items:[
      ["によると (N4)","追溯依赖 → 到信息源","核心：据…说（传闻来源）"],
      ["によって (N3)","锁定参数 → 声明为通用引擎","核心：因…而异 / 通过…手段"],
    ]},
    {c:"#2ecc71",title:"時空向量系：时间定位的微妙差异",items:[
      ["に際して (N2)","正式场合 → 临界点","核心：正值…之际（客观）"],
      ["にあたって (N2)","重大事件 → 正面迎上","核心：在…之际（主观能动）"],
      ["に先立って (N2)","事件前方 → 时间先行","核心：在…之前（先行准备）"],
      ["にあって (N1)","重压处境 → 存在其中","核心：身处…之中（沉重书面）"],
    ]},
    {c:"#9b59b6",title:"関連系：三种「关于」的微妙差异",items:[
      ["について (N4)","紧贴目标 → 不游移","核心：关于（最常用）"],
      ["に関して (N3)","追溯关系 → 辐射网络","核心：关于（更正式书面）"],
      ["に対して (N3)","正面对准 → 投射动作","核心：对… / 与…相反"],
    ]},
    {c:"#f39c12",title:"推移系：四种变化表达",items:[
      ["につれて (N3)","绑定尾流 → 渐进同步","核心：随着（渐进变化）"],
      ["に従って (N2)","遵循轨道 → 服从规则","核心：按照 / 随着（遵从）"],
      ["にともなって (N2)","捆绑同行 → 因果套餐","核心：伴随（因果捆绑）"],
      ["に反して (N2)","预期基线 → 方向相反","核心：与…相反（预期反差）"],
    ]},
  ];
  contrasts.forEach(sec=>{
    h+=`<div class="card contrast-card" style="--c:${sec.c}"><h4>${sec.title}</h4>`;
    sec.items.forEach(([name,mech,core])=>{
      h+=`<div style="margin:8px 0;padding:8px 12px;background:#f8f9fc;border-radius:10px">
        <div style="font-weight:700;color:${sec.c}">${name}</div>
        <div style="font-size:13px;color:var(--sub);margin:3px 0">力学：${mech}</div>
        <div style="font-size:12.5px;color:var(--gold)">${core}</div>
      </div>`;
    });
    h+=`</div>`;
  });
  // master formula card
  h+=`<div class="card" style="background:linear-gradient(135deg,#f5f0ff,#eef6ff);border:1px solid #d0c8f0">
    <h3 style="color:#5b3cc4;margin-bottom:8px">⚡ The Ultimate Epiphany</h3>
    <p style="font-size:14px;line-height:1.8">把 N5→N1 看成一座山是错的。它们是同一个 <b>Hub-and-Spoke Wheel</b> 的不同辐线。
    Hub 是断定の「に」，Spoke 是描述你认知动作的动词。
    70%以上的中高级接续语法，都是这个引擎在做力学支撑。</p>
  </div>`;
  $("#main").innerHTML=h;
}

/* ---------- sentences ---------- */
function renderSentences(){
  let h=`<div class="card intro"><h2>例文集 · 全部配有TTS发音</h2>
  <p>点击 ▶ 听发音。每个语法点的例句来自不同场景，帮助理解实际用法。</p></div>`;
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
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 ni.json 自动生成 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨语法点交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('ni-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
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
  const msg=pct===100?"🏆 完璧！断定の「に」已完全掌握！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 詳解タブで復習してから再挑戦";
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
  if(tab==="map")renderMap();
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

    audio = gen_audio(items)
    qs = build_questions(groups, items, audio)

    tags = (f"<span>{n_total} 语法点</span><span>{level_str}</span>"
            f"<span>{len(audio)} 音声</span><span>5 Group Hub-and-Spoke</span>")
    banks_meta = [[k, l] for k, l in meta.get("quizBanks", [
        ["recog", "📘 语法认识"], ["engine", "🔧 Engine拆解"],
        ["fill", "✍️ 运用填空"], ["listen", "🎧 聴解判别"],
        ["judge", "⭕ 判断正误"]
    ])]

    print(f"[2/4] generating quiz banks for {n_total} grammar points...")
    all_banks = set(q["bank"] for q in qs)
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k in all_banks}
    total_q = sum(counts.values())
    for k, l in banks_meta:
        print(f"      {l}: {counts.get(k, 0)} 問")
    print(f"      合計: {total_q} 問")

    print("[3/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(items))
            .replace("__BANKS__", j(banks_meta))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[4/4] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
