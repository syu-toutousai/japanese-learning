#!/usr/bin/env python3
"""Build the self-contained 「次第」 interactive courseware HTML."""

import base64
import json
from pathlib import Path

AUDIO_DIR = Path(__file__).parent / "audio"
if not AUDIO_DIR.exists():
    AUDIO_DIR = Path("/tmp/opencode/shidai/audio")
OUT = Path.home() / "scratch/shidai-courseware/index.html"

SENTS = {
    "198961275": {"jp": "次第", "cn": "次第（词条发音）"},
    "pOpz1zCZ8g": {"jp": "～次第（文法）", "cn": "文法条目发音"},
    "198961277": {"jp": "次第に", "cn": "次第に（副词）"},
    "50598": {"jp": "式の次第を掲示する。", "cn": "公布仪式的程序。"},
    "50599": {"jp": "事の次第はこうです。", "cn": "事情的经过是这样的。"},
    "50601": {"jp": "このような次第で、まことに申し訳ありません。", "cn": "由于这种情况，实在非常抱歉。"},
    "50605": {"jp": "なに事も人次第だ。", "cn": "什么事都在人／事在人为。"},
    "50606": {"jp": "これから先は君の腕次第だ。", "cn": "今后就全看你的本事了。"},
    "50607": {"jp": "成功するかどうかは、ふだんの心がけ次第だ。", "cn": "成功与否取决于平时的用心。"},
    "50600": {"jp": "次第によっては捨てておけない。", "cn": "视情况而定，不能弃之不顾。"},
    "50609": {"jp": "手紙が着き次第すぐに来てください。", "cn": "信一到，请马上就来。"},
    "50610": {"jp": "現品を受け取り次第、金を払います。", "cn": "一收到现货就付款。"},
    "kEXBlRzGIA": {"jp": "事情が分かり次第、ご報告します。", "cn": "一了解情况就向您汇报。"},
    "ok3uQ3Bk2C": {"jp": "新しい住所が決まり次第、ご連絡します。", "cn": "新地址一定下来就和您联系。"},
    "wVpl8RWcq8": {"jp": "準備ができ次第、ご案内致します。", "cn": "准备好了就带您过去。"},
    "50611": {"jp": "次第に遠ざかる。", "cn": "渐渐远去。"},
    "50613": {"jp": "次第に空が暗くなった。", "cn": "天空渐渐黑了下来。"},
    "50614": {"jp": "次第に興味がわいてくる。", "cn": "渐渐产生了兴趣。"},
}

PATTERNS = [
    {
        "title": "① 名詞「次第」：程序・经过・缘由",
        "point": "原样作名词用，表示事情的经过、缘由或正式的程序安排。",
        "forms": ["式の次第＝仪式程序", "事の次第＝事情经过", "（こういう）次第で＝由于这种情况"],
        "sents": ["50598", "50599", "50601"],
        "note": "书面/正式场合常见。「このような次第で〜」是致歉、说明缘由的固定开场。",
    },
    {
        "title": "② 名詞＋次第だ／次第で(は)／次第によっては：全凭…、取决于…",
        "point": "表示结果完全由前项决定，「要看…而定」。句尾常用「〜次第だ」收束。",
        "forms": ["名＋次第だ", "名＋次第で", "次第によっては＝视…情况不同"],
        "sents": ["50605", "50606", "50607", "50600"],
        "note": "惯用「金次第」＝一切靠金钱（金钱万能），多含批判语气。",
    },
    {
        "title": "③ 動ます形＋次第：一…就（马上）…  ★N2 重点",
        "point": "前项一旦实现，立刻做后项。后项多为说话人的意志动作或请求对方动作，商务邮件高频。",
        "forms": ["着き次第＝一到就", "決まり次第＝一定下来就", "分かり次第＝一弄清就"],
        "sents": ["50609", "50610", "kEXBlRzGIA", "ok3uQ3Bk2C", "wVpl8RWcq8"],
        "note": "只能接ます形！✗着いた次第 / ✗着く次第。近义「〜たらすぐ」但本句型更郑重。",
    },
    {
        "title": "④ 次第に（副词）：渐渐地",
        "point": "表示状态随时间慢慢变化，相当于「だんだん」。",
        "forms": ["次第に＋变化动词（なってくる／〜くなる等）"],
        "sents": ["50611", "50613", "50614"],
        "note": "注意与②③的「次第」读音相同但已是另一个词，书写时常直接写作「次第に」。",
    },
]

QUESTIONS = [
    {"type": "choice", "q": "手紙が（　　）すぐに来てください。", "opts": ["着き次第", "着く次第", "着いた次第"], "ans": 0,
     "exp": "動ます形＋次第：「着き次第」＝一到就。✗着く／着いた 都不能接次第。"},
    {"type": "choice", "q": "準備が（　　）、ご案内致します。", "opts": ["できる次第", "でき次第", "できた次第"], "ans": 1,
     "exp": "でき（ます形）＋次第＝一准备好就…。"},
    {"type": "choice", "q": "スケジュールが決まり（　　）、ご連絡いたします。", "opts": ["次第に", "次第で", "次第"], "ans": 2,
     "exp": "「決まり次第」＝一定下来就。这里不是副词「次第に」（渐渐地）。"},
    {"type": "choice", "q": "結果は自分の努力（　　）。", "opts": ["次第に", "次第だ", "次第でない"], "ans": 1,
     "exp": "名＋次第だ 句型：「取决于自己的努力」。"},
    {"type": "choice", "q": "商務メールで「一到那边就和您联系」最合适的说法？", "opts": ["そちらへ到着した次第、ご連絡します。", "そちらへ到着次第、ご連絡します。", "そちらへ到着する次第、ご連絡します。"], "ans": 1,
     "exp": "必须用ます形「到着り→到着し」＋次第。"},
    {"type": "choice", "q": "「式の次第を掲示する。」中「式の次第」的意思是？", "opts": ["仪式的缘由", "仪式的程序", "渐渐地仪式"], "ans": 1,
     "exp": "名词用法①：程序、议程安排。"},
    {"type": "choice", "q": "「なに事も人次第だ。」的意思是？", "opts": ["凡事听天由命", "事在人为，全看人", "人渐渐变了"], "ans": 1,
     "exp": "名词用法②：人次第だ＝取决于人。"},
    {"type": "choice", "q": "「次第によっては捨てておけない。」的意思是？", "opts": ["视情况而定，不能弃之不顾", "一扔掉就不行了", "任其枯萎"], "ans": 0,
     "exp": "次第によっては＝根据情况不同（有可能…）。"},
    {"type": "listen", "aid": "50613", "q": "听音频，选出你听到的句子。",
     "opts": ["次第に空が暗くなった。", "すぐに空が暗くなった。", "空が暗くなり次第。"], "ans": 0,
     "exp": "次第に＋形容词变化＝天空渐渐变暗。"},
    {"type": "listen", "aid": "wVpl8RWcq8", "q": "听音频，选出你听到的句子。",
     "opts": ["準備ができたら、ご案内致します。", "準備ができ次第、ご案内致します。", "準備のできた次第、ご案内致します。"], "ans": 1,
     "exp": "でき（ます形）＋次第、郑重的商务表达。"},
    {"type": "listen", "aid": "50605", "q": "听音频，选出你听到的句子。",
     "opts": ["なに事も人しだいだ。", "なに事も人次第だ。", "なに事も次第に人だ。"], "ans": 1,
     "exp": "人次第だ＝事在人为。"},
    {"type": "judge", "q": "帰った次第、連絡します。", "ans": False,
     "exp": "✗ 必须接ます形：→「帰り次第、連絡します。」"},
    {"type": "judge", "q": "天気次第で、中止になるかもしれません。", "ans": True,
     "exp": "✓ 名＋次第で＝看天气（如何）再决定。"},
    {"type": "judge", "q": "次第に日本語が上手になった。", "ans": True,
     "exp": "✓ 副词「次第に」＝渐渐地。"},
    {"type": "judge", "q": "この世は全て金次第だ。（这个世界全靠金钱。）", "ans": True,
     "exp": "✓ 惯用「金次第」＝一切取决于金钱。"},
]


QUESTIONS2 = [
    {"type": "choice", "q": "手紙が（　　）すぐに来てください。", "opts": ["着く次第", "着いた次第", "着き次第"], "ans": 2,
     "exp": "動ます形＋次第：「着き次第」＝一到就。✗着く／着いた 都不能接次第。"},
    {"type": "choice", "q": "商务邮件「资料一确认就发送给您」最规范的说法？", "opts": ["資料確認次第お送りします。", "資料を確認し次第お送りします。", "資料を確認した次第お送りします。"], "ans": 1,
     "exp": "サ変動詞用ます形「確認し＋次第」最规范（语干直结「確認次第」也可，但✗確認した）。"},
    {"type": "choice", "q": "詳細が分かり（　　）、ご連絡いたします。", "opts": ["次第", "次第に", "次第で"], "ans": 0,
     "exp": "分かり次第＝一弄清就。「次第に」是副词（渐渐），不能接在动词后。"},
    {"type": "choice", "q": "留学できるかどうかは、親の賛成（　　）。", "opts": ["次第で", "次第に", "次第だ"], "ans": 2,
     "exp": "断定结句用名＋次第だ＝“全凭父母同意”。次第で后面还须接结果。"},
    {"type": "choice", "q": "この植物は、使い方（　　）毒にも薬にもなる。", "opts": ["次第に", "次第で", "次第だ"], "ans": 1,
     "exp": "名＋次第で＝视用法而定，引出两种相反结果，条件中顿。"},
    {"type": "choice", "q": "「（　　）、本日の会議は中止とさせていただきます。」正式开场白填入：", "opts": ["このような次第で", "このような次第に", "このような次第は"], "ans": 0,
     "exp": "名词“原委”＋で表原因：「由于以上情况」固定表达，与“取决于”用法无关。"},
    {"type": "choice", "q": "「次第によっては、計画の見直しも必要だろう。」的理解正确的是？", "opts": ["必须补出具体条件否则是病句", "相当于場合によっては，泛指看情况可独立使用", "与次第で完全同义语气相同"], "ans": 1,
     "exp": "前面不接名词时＝それ次第によっては的固化短语，且假设语气更强、常带消极警示。"},
    {"type": "choice", "q": "「式の次第はパンフレットをご覧ください。」中「式の次第」的意思：", "opts": ["仪式的缘由", "渐渐的仪式", "仪式的程序安排"], "ans": 2,
     "exp": "名词原义：程序、议程。パンフレット里印的就是流程单。"},
    {"type": "judge", "q": "三句话中哪句是病句？<br>A. 帰り次第、電話します　B. 天気次第で、出発を遅らせる　C. 買った次第、使ってみる",
     "opts": ["A", "B", "C"], "ans": 2,
     "exp": "動ます形＋次第！✗買った→○買い次第。A/B 均正确成立。"},
    {"type": "choice", "q": "「彼は金次第なら何でもする人だ。」中「金次第」的语感：", "opts": ["中性描述理财能力强", "含批判语气：只要有钱什么都干", "表示擅长攒钱"], "ans": 1,
     "exp": "惯用「金次第」＝一切靠金钱决定，多含批判、讽刺语感。"},
]


def audio_map():
    m = {}
    for sid in SENTS:
        p = AUDIO_DIR / f"{sid}.mp3"
        b64 = base64.b64encode(p.read_bytes()).decode()
        m[sid] = f"data:audio/mpeg;base64,{b64}"
    return m


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>「次第」完全掌握</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:80px}
header{background:linear-gradient(135deg,#4f6ef7,#7a4ff7);color:#fff;padding:28px 20px 20px}
header h1{font-size:26px} header .kana{opacity:.92;font-size:15px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:860px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 18px;position:relative;z-index:2}
nav button{flex:1;border:none;border-radius:12px;padding:12px 4px;font-size:15px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.pat h2{font-size:17px;color:var(--acc)} .pat .pt{margin:8px 0;font-size:14.5px}
.forms{margin:8px 0}.forms code{display:inline-block;background:var(--acc2);color:var(--acc);
border-radius:8px;padding:3px 9px;font-size:13px;margin:2px 4px 2px 0}
.note{font-size:13px;color:var(--gold);margin-top:8px;border-top:1px dashed var(--line);padding-top:8px}
.jp{font-size:16.5px;line-height:1.65;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.q{font-size:17px;line-height:1.7;margin-bottom:14px}
.opt{display:block;width:100%;text-align:left;padding:12px 14px;margin:8px 0;font-size:15.5px;
border-radius:12px;border:2px solid var(--line);background:#fff;cursor:pointer;line-height:1.5}
.opt:hover:not(:disabled){border-color:var(--acc)}
.opt.right{border-color:var(--ok);background:var(--okbg)}
.opt.wrong{border-color:var(--ng);background:var(--ngbg)}
.opt:disabled{cursor:default;opacity:.92}
.exp{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:14px;line-height:1.6}
.exp.ok{background:var(--okbg);color:var(--ok)} .exp.ng{background:var(--ngbg);color:var(--ng)}
.judgebtns{display:flex;gap:12px}
.judgebtns .opt{flex:1;text-align:center;font-size:18px}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px}
.bar .wrap{display:flex;justify-content:space-between;align-items:center}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
</style>
</head>
<body>
<header><div class="wrap">
<h1>次第 ＜しだい＞ 完全掌握</h1>
<div class="kana">一个形态 · 四种用法 · N2 高频</div>
<div class="tags"><span>N2</span><span>文法</span><span>ビジネス頻出</span></div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">MOJi辞書 音声つき · 离线可用</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO = __AUDIO__;
const SENTS = __SENTS__;
const PATTERNS = __PATTERNS__;
const QUESTIONS = __QUESTIONS__;
const QUESTIONS2 = __QUESTIONS2__;
const QBANKS = {basic: QUESTIONS, adv: QUESTIONS2};

const $ = s => document.querySelector(s);
let curAudio = null;
function play(id, btn){
  if(curAudio) {curAudio.pause(); curAudio.currentTime=0;}
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('playing'));
  curAudio = new Audio(AUDIO[id]);
  if(btn){btn.classList.add('playing'); curAudio.onended=()=>btn.classList.remove('playing');}
  curAudio.play();
}
function rowHTML(id){
  const s = SENTS[id];
  return `<div class="row"><button class="btn" onclick="play('${id}',this)">▶</button>
  <div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}

/* ---------- tabs ---------- */
const TABS = [["study","用法"],["sent","例句库"],["quiz","クイズ"]];
let tab = "study";
function renderNav(){
  $("#nav").innerHTML = TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}

/* ---------- study ---------- */
function renderStudy(){
  let h = PATTERNS.map((p,i)=>`<div class="card pat"><h2>${p.title}</h2>
   <div class="pt">${p.point}</div>
   <div class="forms">${p.forms.map(f=>`<code>${f}</code>`).join("")}</div>
   ${p.sents.map(rowHTML).join("")}
   <div class="note">💡 ${p.note}</div></div>`).join("");
  h += `<div class="card pat"><h2>🎯 一图记忆</h2>
   <div class="jp" style="line-height:2">
   名词「经过」→ <b>こういう次第で</b>（因此）<br>
   名+次第だ → <b>人次第だ</b>（全看人）<br>
   ます形＋次第 → <b>着き次第</b>（一到就）★<br>
   副词 → <b>次第に</b>（渐渐）
   </div></div>`;
  $("#main").innerHTML = h;
}

/* ---------- sentences ---------- */
function renderSent(){
  $("#main").innerHTML = PATTERNS.map(p=>`
    <h3 class="sec">${p.title}</h3>
    <div class="card">${p.sents.map(rowHTML).join("")}</div>`).join("");
}

/* ---------- quiz ---------- */
let mode=null, order=[], qi=0, correct=0, answered=false;
const shuffle = a => a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function renderQuizTab(){
  if(!mode){showBankPicker();return;}
  startQuiz();
}
function showBankPicker(){
  showNext(false); $("#score").textContent="";
  $("#main").innerHTML = `<div class="card" style="text-align:center;padding:30px 16px">
    <div style="font-size:19px;font-weight:700;margin-bottom:6px">选择题库</div>
    <div class="hint" style="margin-bottom:18px">全部随机打乱 · 即时判分讲解</div>
    <button class="opt" style="margin:0 auto 10px;max-width:340px"
      onclick="pickBank('basic')">📘 基礎 · 15問　四种用法全覆盖</button>
    <button class="opt" style="margin:0 auto 10px;max-width:340px"
      onclick="pickBank('adv')">🔥 挑戦 · 10問　进阶易混辨析</button>
    <button class="opt" style="margin:0 auto;max-width:340px"
      onclick="pickBank('mix')">🎲 混合 · 25問　全量随机</button>
  </div>`;
}
function pickBank(m){ if(m==="mix"){QBANKS.mix=QUESTIONS.concat(QBANKS.adv);} else if(!QBANKS[m]) return; mode=m==="mix"?"mix":m; startQuiz(); }
function bankArr(){ return mode==="mix"? QBANKS.mix : QBANKS[mode]; }
function startQuiz(){
  order = shuffle(bankArr().map((_,i)=>i));
  qi=0; correct=0; answered=false; renderQuiz();
}
function renderQuiz(){
  const q = bankArr()[order[qi]];
  updateScore();
  let body="";
  if(q.type==="listen"){
    body = `<div style="text-align:center;margin:6px 0 14px">
      <button class="btn" style="width:56px;height:56px;font-size:24px;margin:auto"
       onclick="play('${q.aid}',this)">🔊</button>
      <div class="hint">可反复点击重听</div></div>`;
  }
  const opts = q.opts ? shuffle(q.opts.map((t,i)=>({t,ok:i===q.ans}))) : null;
  let optHTML="";
  if(opts){
    optHTML = opts.map(o=>`<button class="opt" data-ok="${o.ok?1:0}"
      onclick="pick(this)">${o.t}</button>`).join("");
  }else{
    const truthy = q.ans === true;
    optHTML = `<div class="judgebtns">
      <button class="opt" data-ok="${truthy?1:0}" onclick="pick(this)">⭕ 正しい</button>
      <button class="opt" data-ok="${truthy?0:1}" onclick="pick(this)">❌ 間違い</button></div>`;
  }
  showNext(false);
  $("#main").innerHTML = `<div class="card">
    <div class="hint">第 ${qi+1} 题 / 共 ${order.length} 题</div>
    <div class="q">${q.q}</div>${body}${optHTML}
    <div id="fb"></div></div>`;
}
function showNext(v){
  const b = $("#next");
  b.style.display = v ? "inline-block" : "none";
  b.disabled = !v;
}
function pick(btn){
  if(answered) return; answered=true;
  const q = bankArr()[order[qi]];
  const ok = btn.dataset.ok==="1";
  if(ok) correct++;
  document.querySelectorAll(".opt").forEach(b=>{
    b.disabled=true;
    if(b.dataset.ok==="1") b.classList.add("right");
  });
  if(!ok) btn.classList.add("wrong");
  $("#fb").innerHTML = `<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！"}${q.exp}</div>`;
  showNext(true);
  updateScore();
  window.scrollTo(0,document.body.scrollHeight);
}
function nextQ(){
  answered=false;
  qi++;
  if(qi>=order.length){finish();}
  else renderQuiz();
}
function finish(){
  showNext(false);
  const total = order.length;
  const pct = Math.round(correct/total*100);
  const msg = pct===100?"🏆 完璧！「次第」マスター！" : pct>=70?"👍 かなりいい！復習して満点を目指そう":"📖 用法タブでもう一度復習しよう";
  $("#main").innerHTML = `<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz()">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">別の題庫を選ぶ</button></div>`;
  $("#score").textContent=""; $("#barinfo").textContent=`正确率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){ mode=null; render(); }
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz") showNext(false);
  if(tab==="study")renderStudy(); else if(tab==="sent")renderSent(); else renderQuizTab();
}
renderNav(); render();
</script>
</body>
</html>
"""

html = (TEMPLATE
        .replace("__AUDIO__", json.dumps(audio_map()))
        .replace("__SENTS__", json.dumps(SENTS, ensure_ascii=False))
        .replace("__PATTERNS__", json.dumps(PATTERNS, ensure_ascii=False))
        .replace("__QUESTIONS2__", json.dumps(QUESTIONS2, ensure_ascii=False))
        .replace("__QUESTIONS__", json.dumps(QUESTIONS, ensure_ascii=False)))
OUT.write_text(html, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size//1024} KB)")
