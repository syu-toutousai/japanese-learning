#!/usr/bin/env python3
"""Build the self-contained 「言葉・新詞 記憶の種」(kotoba-courseware) HTML.

一个新词收集器：把一个刚碰到的词，按人脑学习语言的认知规律铺一条
「初见 → 扎根(语义网络) → 辨析 → 提取 → 间隔复习」的学习路径。

数据写 kotoba.json。每次遇到新词，用
    moji <単語> --once
查释义与例句，抄成一个 word 条目（core/senses/contrast/anchor/quizzes），
重跑本脚本即自动生成该词的主卡、词义星图、题库与间隔复习计划。

音频：
  - MOJi 词条与例句音频（audio-moji/ 内）直接内嵌，保证原声；
  - 自写例句/场景由 edge-tts（ja-JP-Nanami / 男性 Keita）合成，
    按文本哈希缓存到 audio/。
"""

import base64
import hashlib
import json
import random
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "kotoba.json"
AUDIO_DIR = ROOT / "audio"
MOJI_DIR = ROOT / "audio-moji"
SCENES_DIR = ROOT / "scenes"
OUT = ROOT / "index.html"

RATE = "-4%"
SEED = 20260829          # 固定随机种子：干扰项抽样可复现
MIN_MP3 = 300            # 小于该字节数视为合成失败
BANK_META = [
    ["recog",    "📘 詞義認識"],
    ["generate", "✍️ 産出填空"],
    ["listen",   "🎧 聴解判別"],
    ["discrim",  "🧩 辨析判別"],
    ["custom",   "⭐ 自作題"],
]

# MOJi 原声：logical audio id → audio-moji/ 里的文件名
MOJI = {
    "fuzei:w":   "風情_1989100769_w_f003.mp3",
    "fuzei:s1":  "風情_86977_e1_f003.mp3",
    "fuzei:s1b": "風情_86979_e6_f003.mp3",
    "fuzei:s1c": "風情_86978_e4_f003.mp3",
    "fuzei:s2":  "風情_86980_e0_f003.mp3",
    "fuzei:s3":  "風情_86981_e2_f003.mp3",
    "fuzei:s4":  "風情_86975_e3_f003.mp3",
    "fuzei:s4b": "風情_86976_e5_f003.mp3",
    "aku:w":     "開く_198938426_w_f003.mp3",
    "aku:s1":    "開く_28898_e0_f003.mp3",
    "aku:s2":    "開く_28899_e3_f003.mp3",
    "aku:s3":    "開く_28900_e1_f003.mp3",
    "aku:s4":    "開く_28901_e4_f003.mp3",
}


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_data():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] kotoba.json 不是合法 JSON：{e}")
    errors = []
    meta = data.get("meta") or {}
    words = data.get("words") if isinstance(data.get("words"), list) else []
    if not words:
        errors.append("缺少非空的 words 列表")
    seen = set()
    for w in words:
        wid = w.get("id", "")
        label = wid or w.get("word", "?")
        if not wid:
            errors.append(f"词条「{label}」缺少 id")
        elif wid in seen:
            errors.append(f"词条 id 重复：{wid}")
        seen.add(wid)
        if wid and not all(c.isalnum() or c in "-_" for c in wid):
            errors.append(f"词条 id「{wid}」只能用字母数字-_")
        for key in ("word", "read", "level", "firstSeen"):
            if not w.get(key):
                errors.append(f"「{label}」缺少 {key}")
        if not w.get("core") or not w["core"].get("def"):
            errors.append(f"「{label}」缺少 core.def（核心意象）")
        sens = w.get("senses") or []
        if not sens:
            errors.append(f"「{label}」至少需要一个义项 senses")
        for i, s in enumerate(sens):
            if not s.get("def"):
                errors.append(f"「{label}」义项#{i+1} 缺少 def")
            for k, ex in enumerate(s.get("examples") or []):
                if not ex.get("jp"):
                    errors.append(f"「{label}」义项#{i+1} 例句#{k+1} 缺 jp")
        for n, q in enumerate(w.get("quizzes") or []):
            qt = q.get("type")
            bk = q.get("bank", "custom")
            if bk not in [b[0] for b in BANK_META]:
                errors.append(f"「{label}」自作题#{n+1} bank「{bk}」不认识")
            if qt not in ("choice", "judge", "listen", "type"):
                errors.append(f"「{label}」自作题#{n+1} type 必须是 choice/judge/listen/type")
            elif qt in ("choice", "listen"):
                if not q.get("opts") or not isinstance(q.get("ans"), int) \
                        or not 0 <= q["ans"] < len(q["opts"]):
                    errors.append(f"「{label}」自作题#{n+1} 需要 opts 和范围内的 ans")
            elif qt == "type" and not q.get("ansTxt"):
                errors.append(f"「{label}」自作题#{n+1} 需要 ansTxt（可接受的答案列表）")
        for k, cl in enumerate(w.get("nadeshiko") or []):
            if not cl.get("sid"):
                errors.append(f"「{label}」nadeshiko 片段#{k+1} 缺 sid")
            if not cl.get("jp") or not cl.get("en") or not cl.get("cn"):
                errors.append(f"「{label}」nadeshiko 片段#{k+1} 缺 jp/en/cn")
            sense_n = cl.get("sense")
            if not isinstance(sense_n, int) or not any(s.get("n") == sense_n for s in w.get("senses") or []):
                errors.append(f"「{label}」nadeshiko 片段#{k+1} sense 必须指向某个义项 n")
    if errors:
        print("[!] kotoba.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return meta, words


# ---------------------------------------------------------------- tts

def cache_path(logical_id, text):
    h = hashlib.sha1(text.encode()).hexdigest()[:10]
    return AUDIO_DIR / f"{logical_id}-{h}.mp3"


def gen_one(task):
    logical_id, text, voice = task
    path = cache_path(logical_id, text)
    if path.exists() and path.stat().st_size > MIN_MP3:
        return True
    for _ in range(3):
        r = subprocess.run(
            ["edge-tts", "--voice", voice, f"--rate={RATE}", "--text", text,
             "--write-media", str(path)],
            capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > MIN_MP3:
            return True
    return False


def gen_audio(meta, words):
    AUDIO_DIR.mkdir(exist_ok=True)
    MOJI_DIR.mkdir(exist_ok=True)
    vf = meta.get("voiceFemale", "ja-JP-NanamiNeural")
    vm = meta.get("voiceMale", "ja-JP-KeitaNeural")

    single = []   # (logical_id, text, voice)
    moji_ok = {}
    for wid, fname in MOJI.items():
        p = MOJI_DIR / fname
        moji_ok[wid] = p.exists() and p.stat().st_size > MIN_MP3

    tasks = []
    for w in words:
        wid = w["id"]
        tasks.append((f"{wid}:w", w["read"], vf))  # 单词读音（若 audio-moji 有则用原声）
        enc = w.get("encounter") or {}
        ctx = (enc.get("context") or "").split("／")
        if len(ctx) >= 1 and ctx[0]:
            tasks.append((f"{wid}:enc0", ctx[0], vm))
        if len(ctx) >= 2 and ctx[1]:
            tasks.append((f"{wid}:enc1", ctx[1], vm))
        if enc.get("line"):
            tasks.append((f"{wid}:enc2", enc["line"], vf))
        for i, s in enumerate(w.get("senses") or []):
            for k, ex in enumerate(s.get("examples") or []):
                tasks.append((f"{wid}:s{i+1}" + ("abc"[k] if k else ""), ex["jp"], vf))
        for i, c in enumerate(w.get("contrast") or []):
            if c.get("ex"):
                tasks.append((f"{wid}:c{i}", c["ex"], vf))

    # MOJi 原声直接算数；其余才交给 edge-tts
    moji_ready = {wid for wid, ok in moji_ok.items() if ok}
    todo = [(lid, txt, v) for lid, txt, v in tasks if lid not in moji_ready]
    print(f"[1/4] audio: {len(tasks)} clips, {len(tasks)-len(todo)} via MOJi, "
          f"{len(todo)} to synthesize...")
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
    failed = [lid for lid, ok in results.items() if not ok]
    if failed:
        print(f"[!] {len(failed)} 条发音合成失败（课件照常生成，只是这几处没有播放键）：")
        for lid in failed:
            print("   -", lid)

    keep = {cache_path(lid, txt).name for lid, txt, _ in tasks}
    removed = 0
    for p in AUDIO_DIR.glob("*.mp3"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    if removed:
        print(f"      cleaned {removed} stale mp3(s)")

    audio = {}
    for lid, txt, _ in tasks:
        src = None
        if lid in moji_ready:
            src = MOJI_DIR / MOJI[lid]
        else:
            p = cache_path(lid, txt)
            if p.exists() and p.stat().st_size > MIN_MP3:
                src = p
        if src:
            audio[lid] = "data:audio/mpeg;base64," + base64.b64encode(src.read_bytes()).decode()
    return audio, len(tasks) - len(audio)


# ---------------------------------------------------------------- SENTS

def build_sents(words):
    sents = {}
    for w in words:
        wid = w["id"]
        enc = w.get("encounter") or {}
        ctx = (enc.get("context") or "").split("／")
        enc_cn = [x.strip() for x in (enc.get("cn") or "").split("／")]
        if len(ctx) >= 1 and ctx[0]:
            sents[f"{wid}:enc0"] = {"jp": ctx[0], "cn": enc_cn[0] if enc_cn else ""}
        if len(ctx) >= 2 and ctx[1]:
            seg = enc_cn[1].split("——")[0] if len(enc_cn) > 1 else ""
            sents[f"{wid}:enc1"] = {"jp": ctx[1], "cn": seg if seg.endswith("。") else seg + "。"}
        if enc.get("line"):
            sents[f"{wid}:enc2"] = {"jp": enc["line"], "cn": "“你也太不解风情了。”"}
        for i, s in enumerate(w.get("senses") or []):
            for k, ex in enumerate(s.get("examples") or []):
                sents[f"{wid}:s{i+1}" + ("abc"[k] if k else "")] = {"jp": ex["jp"], "cn": ex.get("cn", "")}
        for i, c in enumerate(w.get("contrast") or []):
            if c.get("ex"):
                sents[f"{wid}:c{i}"] = {"jp": c["ex"], "cn": c.get("cn", "")}
    return sents


# ---------------------------------------------------------------- nadeshiko real-film clips

def build_clips(words):
    SCENES_DIR.mkdir(exist_ok=True)
    clips = {}
    for w in words:
        wid = w["id"]
        for i, cl in enumerate(w.get("nadeshiko") or []):
            lid = f"{wid}:nade{i}"
            sid = cl["sid"]
            mp3 = SCENES_DIR / f"{sid}.mp3"
            webp = SCENES_DIR / f"{sid}.webp"
            if not mp3.exists() or not webp.exists():
                print(f"   [!] 片段 {sid} 缺素材，需在 scenes/ 放 {sid}.mp3 和 {sid}.webp")
                continue
            clips[lid] = {
                "sid": sid, "sense": cl.get("sense"), "media": cl.get("media"),
                "ep": cl.get("ep"), "at": cl.get("at"), "jp": cl.get("jp"),
                "en": cl.get("en"), "cn": cl.get("cn"),
                "mp3": "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode(),
                "img": "data:image/webp;base64," + base64.b64encode(webp.read_bytes()).decode(),
            }
    return clips


# ---------- kana <-> romaji (用于生成填空的通用答案池) ----------
_SEI = (list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん")
        + list("がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ")
        + list("ゃゅょぁぃぅぇぉっ"))
_ROM = ("a i u e o ka ki ku ke ko sa si su se so ta ti tu te to na ni nu ne no ha hi hu he ho"
        " ma mi mu me mo ya yu yo ra ri ru re ro wa wo n"
        " ga gi gu ge go za zi zu ze zo da di du de do ba bi bu be bo pa pi pu pe po"
        " ya yu yo a i u e o tu").split()
KANA2ROM = dict(zip(_SEI, _ROM))


def kana2romaji(s):
    out, i = [], 0
    while i < len(s):
        hit = False
        for ln in (2, 1):
            if s[i:i+ln] in KANA2ROM:
                out.append(KANA2ROM[s[i:i+ln]])
                i += ln
                hit = True
                break
        if not hit:
            out.append(s[i]); i += 1
    return "".join(out)


def reading_pool(w):
    """读音通用可接受池：假名/片假名/罗马音 全部收进（含 Hepburn 变体）。"""
    pool = [w.get("read", "")]
    if w.get("altRead"):
        pool.append(w["altRead"].split("（")[0])
    pool += [r for r in pool if r]
    roms = [kana2romaji(r) for r in pool]
    # 训令式 → 常用拼法变体：hu→fu、zi→ji、si→shi、ti→chi、tu→tsu、zya→ja…
    ALTS = {"hu": "fu", "zi": "ji", "si": "shi", "ti": "chi", "tu": "tsu",
            "hi": "fi", "zya": "ja", "zyu": "ju", "zyo": "jo",
            "sya": "sha", "syu": "shu", "syo": "sho"}
    variants = []
    for r in roms:
        variants.append(r)
        for a, b in ALTS.items():
            if a in r:
                variants.append(r.replace(a, b))
    out = []
    for r in pool + variants:
        if r and r not in out:
            out.append(r)
    return out


# ---------------------------------------------------------------- auto quizzes

def build_questions(words, audio):
    rng = random.Random(SEED)
    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for w in words:
        wid = w["id"]
        sens = w.get("senses") or []
        ex0 = (sens[0]["examples"] or [{}])[0] if sens else {}

        # 📘 自動：核心意象 / 词义理解
        add("recog", f"{wid}:auto-meaning", type="choice",
            q=f"「{w['word']}（{w['read']}）」最基本的含义，最接近哪一项？",
            opts=[w["core"]["def"],
                  "做事的方式、做派（＝やり方）",
                  "食物的味道（＝ふうみ）",
                  "怀念远方的情绪（＝なつかしさ）"],
            ans=0,
            exp=f'{w["word"]}＝{w["core"]["def"]}<br>🧠 {w.get("anchor","")}')

        # ✍️ 自動：生成填空（type 输入读音 / 汉字）
        if ex0.get("jp"):
            blanked = ex0["jp"].replace(w["word"], "（　　　）", 1)
            first_def = (sens[0].get("def") or "").split("。")[0] if sens else ""
            add("generate", f"{wid}:auto-kana", type="type",
                q=f'「{blanked}」<br><span class="hint">（　　　）—— 请输入读音假名</span>',
                ansTxt=reading_pool(w),
                exp=f'完整句子：{ex0["jp"]}<br>{ex0.get("cn","")}<br>'
                    f'{w["word"]} 读作<b>{w["read"]}</b>。')
            add("generate", f"{wid}:auto-kanji", type="type",
                q=f'假名：{w["read"]} → 请输入对应的<b>汉字写法</b>。',
                ansTxt=[w["word"]],
                exp=f'{w["read"]} 的汉字写法：<b>{w["word"]}</b>。')

        # 🎧 自動：听解（优先用该词 MOJi 原声例句）
        for i, s in enumerate(sens):
            for k, ex in enumerate(s.get("examples") or []):
                aid = f"{wid}:s{i+1}" + ("abc"[k] if k else "")
                if aid in audio:
                    add("listen", f"{wid}:auto-listen-{i+1}{k}", type="listen", aid=aid,
                        q="🎧 听音频，选出你听到的句子。",
                        opts=[ex["jp"],
                              ex["jp"][::-1][:len(ex["jp"])],
                              ex["jp"][:len(ex["jp"])//2]+"..."],
                        ans=0,
                        exp=f'原句：{ex["jp"]}<br>{ex.get("cn","")}')
                    break
            break
            break

        # ⭐ 自作題
        for n, cq in enumerate(w.get("quizzes") or []):
            q = dict(cq)
            q.setdefault("exp", "")
            q["ref"] = f"{wid}:cu-{n}"
            qs.append(q)

    return qs





# ---------------------------------------------------------------- spacing (per word)

def build_schedule(w):
    """每词一条间隔复习计划；不同词配不同的回想提示。"""
    rd = w.get("read", "")
    wd = w.get("word", "")
    tasks = [
        (0, "初習", f"初见五件套：通读列表卡 → 点开发音({rd}) → 回放初遇场景×2 → 默念核心意象 → 过一遍语义网络。"),
        (1, "隔日闪回", f"听到「{rd}」，脑子里弹出什么画面？做一轮【📘 詞義認識】。"),
        (3, "搭配加固", "不看卡默写搭配、造句一句。做一轮【🎧 聴解判別】。"),
        (7, "产出练习", f"用「{wd}」自己造两句完整的日语句子写下来。做一轮【🧩 辨析判別】。"),
        (14, "教给别人", f"把「{wd}」的意象与近义区分讲给谁听（或对着空气大声讲一遍）。做一轮【✍️ 産出填空】。"),
        (30, "锚定收尾", "混合交错全刷一轮 ＋ 清空错题本——这个新词正式入账。"),
    ]
    return [[d, lab, t] for d, lab, t in tasks]

# ---------------------------------------------------------------- html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>言葉・新詞 記憶の種</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b;--warm:#e8590c;--teal:#0ca678}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#0f7a5f,#12a37f);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 16px;position:relative;z-index:2;flex-wrap:wrap}
nav button{flex:1;min-width:96px;border:none;border-radius:12px;padding:12px 2px;font-size:14px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
nav .wordtab{background:var(--card);border-radius:12px;padding:8px 14px;text-align:center;
cursor:pointer;font-weight:700;color:var(--sub);box-shadow:0 2px 10px rgba(30,40,90,.08);
font-size:15px;display:flex;flex-direction:column;line-height:1.2}
nav .wordtab small{font-size:11px;font-weight:600;color:var(--sub);opacity:.85}
nav .wordtab.on{background:linear-gradient(135deg,#0f7a5f,#12a37f);color:#fff}
nav .wordtab.on small{color:#d8f5ea}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:1.7;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px;line-height:1.75}
.read{font-size:14px;color:var(--sub)}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
.btn.big{width:46px;height:46px;font-size:20px}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.pill{display:inline-block;border-radius:99px;background:#f1f3f8;color:var(--ink);
padding:3px 12px;font-size:13px;font-weight:700;margin:2px 6px 2px 0}
.pill.gold{background:#fff4d6;color:var(--gold)}
.pill.teal{background:#e6fbf3;color:var(--teal)}
.pill.warm{background:#fff0e6;color:var(--warm)}
/* hero */
.hero{background:linear-gradient(135deg,#f0fbf7,#fff);border:2px solid #b8ecd9}
.hero .bigword{font-size:46px;font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-weight:700;
letter-spacing:4px;color:#0f7a5f}
.hero .mega{margin:6px 0}
.hero .meet{margin-top:12px;font-size:12.5px;color:var(--sub);
border-top:1px dashed var(--line);padding-top:10px}
/* scene */
.scene{background:linear-gradient(135deg,#fff7e6,#fff)}
.scene .place{font-size:13px;color:var(--gold);font-weight:700;margin-bottom:8px}
.scene .ctx{font-size:15px}
.scene .bigline{font-size:24px;margin:10px 0 4px;color:#7a4f00;letter-spacing:1px}
.scene .take{background:#fff4d6;border-radius:12px;padding:10px 13px;font-size:13.5px;
line-height:1.8;color:#6b4a00;margin-top:10px}
/* core & sense */
.corebox{background:linear-gradient(135deg,#eef1ff,#f6f2ff);border-left:5px solid var(--acc)}
.corebox h4{font-size:16.5px;margin-bottom:8px;color:#33418f}
.kv{font-size:14.5px;line-height:1.9}
.split{background:#fff;border-radius:12px;padding:10px 13px;margin-top:10px;font-size:14px;line-height:1.8}
.en{font-size:12.5px;color:var(--sub);font-style:italic;margin-top:8px;line-height:1.7}
.img{font-size:13.5px;color:#7a4ff7;margin-top:8px;line-height:1.7}
h3.sec{font-size:15px;color:var(--sub);margin:18px 0 8px;font-weight:700}
.star{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}
.star .corecell{grid-column:1/-1;background:linear-gradient(135deg,#33418f,#7a4ff7);color:#fff;
border-radius:14px;padding:14px 16px;font-size:14.5px;line-height:1.75;text-align:center}
.star .corecell small{display:block;opacity:.9;font-size:12px;margin-top:4px}
.star .cell{background:#f8faff;border:2px solid #dfe6fb;border-radius:14px;padding:12px;
cursor:pointer;text-align:center;transition:.15s}
.star .cell:hover{border-color:var(--acc);transform:translateY(-2px)}
.star .cell .em{font-size:22px}
.star .cell b{font-size:14.5px;display:block;margin:6px 0 3px}
.star .cell small{font-size:11.5px;color:var(--sub);line-height:1.5}
.sensecard{border-left:5px solid var(--teal)}
.sensecard h2{font-size:18px}
.sensecard h2 .num{color:var(--sub);font-weight:600;font-size:13px;margin-right:6px}
.senselabel{font-size:14px;color:var(--teal);font-weight:700;margin:4px 0 8px}
.defbox{background:#eef9f4;border-radius:12px;padding:10px 13px;font-size:14.5px;line-height:1.8;margin:6px 0}
.colloc{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.colloc span{background:#e6fbf3;color:#0b7285;border-radius:99px;padding:4px 12px;font-size:12.5px;font-weight:600}
.etymo{background:#eef1ff;border-left:5px solid #7a4ff7}
.etymo .key{background:#fff;border-radius:10px;padding:9px 12px;margin-top:10px;font-size:13.5px;line-height:1.7;color:#3b2f88}
.anchor{font-size:14px;color:var(--gold);background:#fffdf5;border-radius:12px;
padding:12px 14px;line-height:1.85;border:2px dashed var(--gold)}
/* contrast */
.contrast{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.ccard{background:#fff;border-radius:14px;padding:13px 14px;border:2px solid var(--line);box-shadow:0 1px 6px rgba(30,40,90,.06)}
.ccard.warming{border-color:var(--ng);background:#fff5f5}
.ccard h4{font-size:15px;color:#0b7285;margin-bottom:4px}
.ccard h4 .g{color:var(--sub);font-size:12px;font-weight:600}
.ccard p{font-size:13.5px;line-height:1.75;color:var(--ink)}
.ccard .rw{margin-top:8px}
/* nadeshiko real-film clips */
.clip{display:flex;gap:14px;align-items:flex-start;background:#fff;border:2px solid var(--line);
border-radius:14px;padding:12px;box-shadow:0 1px 6px rgba(30,40,90,.06)}
.clip+.clip{margin-top:12px}
.clip .shot{flex:none;width:128px;height:72px;border-radius:10px;object-fit:cover;border:1px solid var(--line);
background:#eef0f5}
.clip .cjp{font-size:16px;line-height:1.65;font-weight:600}
.clip .cen{font-size:13px;color:var(--sub);font-style:italic;margin-top:4px;line-height:1.6}
.clip .ccn{font-size:13px;color:var(--sub);margin-top:4px;line-height:1.7}
.clip .clabel{margin-top:6px}
.clip .clabel span{display:inline-block;border-radius:99px;padding:3px 10px;font-size:11.5px;font-weight:700;
margin:2px 5px 2px 0}
.clip .clabel .mep{background:#eef1ff;color:#33418f}
.clip .clabel .sense{background:#e6fbf3;color:#0b7285}
.clip .clabel .tim{background:#fff4d6;color:#7a4f00}
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
.judgebtns{display:flex;gap:12px;flex-wrap:wrap}
.judgebtns .opt{flex:1;min-width:120px;text-align:center;font-size:18px}
.typeq input{width:100%;max-width:320px;padding:12px 14px;font-size:17px;border:2px solid var(--line);
border-radius:12px;margin:6px 0;text-align:center}
.typeq input:focus{outline:none;border-color:var(--acc)}
.typeq .chk{display:inline-block;background:var(--acc);color:#fff;border:none;border-radius:10px;
padding:11px 24px;font-size:15px;cursor:pointer}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px;z-index:5}
.bar .wrap{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
code.inline{background:#eceff7;border-radius:6px;padding:1px 7px;font-size:.92em}
/* spacing */
.tl{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.tl .day{flex:1;min-width:100px;background:#fff;border:2px solid var(--line);border-radius:14px;
padding:10px;text-align:center;position:relative}
.tl .day.done{border-color:var(--ok);background:var(--okbg)}
.tl .day.now{border-color:var(--warm);box-shadow:0 0 0 3px #ffe3d1}
.tl .day.day-lock{border-color:#eef0f5;color:#b6bccd}
.tl .day b{font-size:15px}
.tl .day .off{font-size:11px;color:var(--sub)}
.tl .day button{border:none;background:var(--ok);color:#fff;border-radius:8px;padding:5px 10px;
font-size:12px;margin-top:8px;cursor:pointer}
.tl .day.day-lock button{display:none}
.due{background:#fff7e6;border:2px solid var(--gold)}
.due .bigoff{font-size:18px;color:var(--warm);font-weight:800}
.due h4{margin:6px 0}
.due .taskc{line-height:1.8;font-size:14px}
</style>
</head>
<body>
<header><div class="wrap">
<h1>言葉・新詞 記憶の種</h1>
<div class="kana">新碰到的词，走一条人脑记得住的路——初见 👉 语义网络 👉 辨析 👉 提取 👉 间隔复习</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">离线可用 · 点🔊听发音 · 新词先「遇见」再「长根」，靠提取与间隔才能长住</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const META=__META__;
const WORDS=__WORDS__;
const SENTS=__SENTS__;
const CLIPS=__CLIPS__;
const BANKS=__BANKS__;
const SCHED=__SCHED__;
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
  const s=SENTS[sid]; if(!s)return "";
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}
function esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function pill(text,cls){return `<span class="pill ${cls||''}">${text}</span>`;}
let W=WORDS[0];

/* ---------- tabs ---------- */
const TABS=[["enc","🧊 初见"],["nade","🎬 台词·画面"],["web","🌐 语义网络"],["cmp","🧂 辨析场"],["quiz","🎯 提取"],["spc","🔁 间隔"]];
let tab="enc";
function renderNav(){
  let sel=WORDS.map(w=>`<button class="${w.id===W.id?'on':''}" onclick="selectWord('${w.id}')">${esc(w.word)}</button>`).join("");
  $("#nav").innerHTML=`
    <div style="flex:0 0 100%;display:flex;gap:8px;flex-wrap:wrap;background:transparent;box-shadow:none">
      ${WORDS.map(w=>`<span class="wordtab ${w.id===W.id?'on':''}" onclick="selectWord('${w.id}')">${esc(w.word)}<small>${esc(w.read)}</small></span>`).join("")}
    </div>
    ${TABS.map(([k,l])=>`<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("")}`;
}
function selectWord(id){
  W=WORDS.find(w=>w.id===id)||WORDS[0];
  mode=null;pool=[];order=[];qi=0;correct=0;answered=false;
  renderNav();render();window.scrollTo(0,0);
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}

/* ---------- 初见 ---------- */
function renderEnc(){
  const e=W.encounter||{}, c=W.core||{};
  const readB=AUDIO[W.id+':w']?`<button class="btn big" onclick="play('${W.id+':w'}',this)">🔊</button>`:"";
  let h=`<div class="card hero">
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div class="bigword">${W.emoji} ${esc(W.word)}</div>
      <div>
        <div class="mega">${pill(esc(W.read)+' '+esc(W.accent))}${pill(esc(W.level),'gold')}${W.altRead?pill('别读：'+esc(W.altRead),'warm'):''}</div>
        ${readB}
      </div>
    </div>
    <div class="meet">🆕 初次遇见 · ${esc(W.firstSeen)} ｜ 你正在追的素材里，听到这句话</div>
  </div>`;
  h+=`<div class="card scene">
    <div class="place">初遇场景 —— ${esc(e.scene||"")}</div>
    ${rowHTML(W.id+':enc0')}${rowHTML(W.id+':enc1')}
    <div class="bigline">「${esc(e.line||"")}」</div>
    ${AUDIO[W.id+':enc2']?`<button class="btn" onclick="play('${W.id+':enc2'}',this)">▶ 回放原句</button>`:""}
    <div class="cn" style="margin-top:8px">${e.cn||""}</div>
    <div class="take">🧠 ${e.take||""}</div>
  </div>`;
  h+=`<div class="card corebox">
    <h4>核心意象 —— ${esc(c.title||"")}</h4>
    <div class="kv">${c.def||""}</div>
    <div class="split">🔤 ${c.split||""}</div>
    <div class="img">🎨 ${c.image||""}</div>
    ${c.en?`<div class="en">${esc('i.e. ')+esc(c.en)}</div>`:""}
  </div>`;
  $("#main").innerHTML=h;
}

/* ---------- 语义网络 ---------- */
function renderWeb(){
  let h=`<h3 class="sec">先把多义记成一棵「同根树」：所有意思都从核心意象长出来</h3>
  <div class="star">
    <div class="corecell">${esc(W.word)}＝${W.core?esc(W.core.def):""}<small>核心意象：一切义项的根</small></div>
    ${(W.senses||[]).map(s=>`<div class="cell" onclick="goSense(${s.n})">
      <div class="em">${s.emoji||"📌"}</div><b>${s.n}. ${esc(s.label)}</b>
      <small>${esc(shortD(s.def))}</small></div>`).join("")}
  </div>`;
  (W.senses||[]).forEach(s=>{
    h+=`<div class="card sensecard" id="sense-${s.n}" style="scroll-margin-top:70px">
      <h2><span class="num">第${s.n}义</span> ${s.emoji||"📌"} ${esc(s.label)}</h2>
      <div class="senselabel">${esc(s.emoji)} ${esc(s.label)} ｜ 搭配</div>
      <div class="defbox">📌 <b>意思</b>　${s.def||""}</div>
      ${s.colloc?`<div class="colloc">${s.colloc.split(/\s+/).filter(x=>x).map(x=>`<span>${esc(x)}</span>`).join("")}</div>`:""}
      ${(s.examples||[]).map((_,i)=>rowHTML(`${W.id}:s${s.n}`+("abc"[i]||""))).join("")}
    </div>`;
  });
  const et=W.etymology||{};
  h+=`<div class="card" style="background:#eef1ff;border-left:5px solid #7a4ff7">
    <h3 class="sec" style="margin-top:0">📜 ${esc(et.title||"语源")}</h3>
    <div class="kv" style="line-height:1.85">${et.text||""}</div>
    <div class="key">🧠 ${et.key||""}</div>
  </div>`;
  h+=`<div class="anchor">🧠 记忆锚　${W.anchor||""}</div>`;
  $("#main").innerHTML=h;
}
function shortD(d){return (d||"").replace(/<[^>]+>/g,"").split(/[，。]/)[0];}
function goSense(n){goTab('web');setTimeout(()=>{const el=document.getElementById('sense-'+n);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- 🎬 台词·画面（Nadeshiko 真实番剧原声） ---------- */
function renderNade(){
  const prefix=W.id+":nade";
  const keys=Object.keys(CLIPS).filter(k=>k.startsWith(prefix)).sort();
  if(!keys.length){$("#main").innerHTML=`<div class="card"><div class="hint">还没有纳进台词片段——往 kotoba.json 的 nadeshiko 里填，把音频和画面放进 scenes/。</div></div>`;return;}
  let h=`<h3 class="sec">🎬 这句话出现在真实番剧哪一集，配的是哪一帧画面 —— 原声+原画，一次性钉进脑子里</h3>
  <div class="hint">片段来自 Nadeshiko 语料库（有版权仅作学习）。点 ▶ 听原声，反复听——真实语速、真实语气。</div>`;
  keys.forEach(k=>{
    const c=CLIPS[k];
    const sense=(W.senses||[]).find(s=>s.n===c.sense)||{};
    h+=`<div class="clip">
      <img class="shot" src="${c.img}" alt="scene">
      <div style="flex:1;min-width:0">
        <div class="cjp">${c.jp}</div>
        <div class="cen">${esc(c.en)}</div>
        <div class="ccn">${c.cn}</div>
        <div class="clabel">
          <span class="mep">📺 ${esc(c.media)}</span>
          <span class="tim">EP${c.ep} · ${esc(c.at||"")}</span>
          <span class="sense" onclick="goSense(${c.sense})">义项 ${c.sense} · ${esc(sense.label||"")}</span>
        </div>
        <button class="btn" style="margin-top:8px" onclick="play('${k}',this)">🔊 原声</button>
      </div>
    </div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- 辨析场 ---------- */
function renderCmp(){
  let h=`<h3 class="sec">近义词只靠「比较」才分得清 —— 一组一对照，记忆成对长</h3>
  <div class="contrast">`;
  (W.contrast||[]).forEach((c,i)=>{
    const warm=c.d.indexOf("対義")>=0;
    h+=`<div class="ccard ${warm?'warming':''}">
      <h4>${esc(c.word)}${warm?' <span class="g">· 対義</span>':' <span class="g"></span>'}</h4>
      <p>${esc(c.d)}</p>
      ${c.ex?`<div class="rw">${rowHTML(`${W.id}:c${i}`)}</div>`:""}
    </div>`;
  });
  h+=`</div>
  <div class="card corebox" style="margin-top:14px">
    <h4>一句话切分的刀</h4>
    <div class="kv">${W.javaKnife||""}</div>
  </div>
  <div class="anchor" style="margin-top:14px">🧠 记忆锚　${W.anchor||""}</div>`;
  $("#main").innerHTML=h;
}

/* ---------- 提取（クイズ） ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function lsKey(s){return `koto-${W.id}-${s}`;}
function wrongBook(){return lsGet(lsKey("wrong"),{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet(lsKey("wrong"),w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet(lsKey("wrong"),w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
const wqs=()=>QS.filter(q=>q.ref.startsWith(W.id+":"));
function countBank(key){return wqs().filter(q=>q.bank===key).length;}
function renderQuiz(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('${k}')">${esc(label)} · ${countBank(k)}問<br><span style="font-size:12px;color:var(--sub)">${bankTip[k]||""}</span></button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:420px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div style="max-width:420px;margin:6px auto 10px;text-align:center"><span class="hint">错题本是空的——答错的题自动收进来 📕</span></div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:20px;font-weight:700;margin-bottom:6px">🎯 提取练习 —— 记得住，才算学会</div>
      <div class="hint" style="margin-bottom:18px">按「认识 → 生成 → 听解 → 辨析 → 综合」五关层层加深 · 全随机打乱 · 错题自动入账</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨关卡交错练习，最强记忆留存</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem(lsKey('wrong'));renderQuiz();}">🗑️ 清空错题本</button>`:""}
    </div>`;
    return;
  }
  startQuiz(mode);
}
const bankTip={recog:"认出词义与读音，先混个脸熟",generate:"自己把词「生成」出来，记忆最牢固",listen:"耳朵和字绑在一起",discrim:"近义辨析，一次分清",custom:"场景・文化・语感的综合题"};
function startQuiz(m){
  mode=m;
  const qs=wqs();
  if(m==="mix")pool=qs.slice();
  else if(m==="wrong"){const w=wrongBook();pool=qs.filter(q=>w[q.ref]);}
  else pool=qs.filter(q=>q.bank===m);
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
  if(q.type==="type"){
    body=body+`<div class="typeq">
      <input id="typin" type="text" autocomplete="off" placeholder="输入答案（假名/汉字均可）">
      <button class="chk" onclick="checkType()">✓ 提交</button>
      <div class="hint">可以多试几次 · 回车同样可以提交</div></div>`;
    setTimeout(()=>{const i=document.getElementById("typin");if(i){i.focus();i.onkeydown=e=>{if(e.key==="Enter")checkType();};}},60);
  }else if(q.opts){
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
const kanaHira=s=>s.replace(/[\u30a1-\u30f6]/g,c=>String.fromCharCode(c.charCodeAt(0)-0x60));
const normZ=s=>s.replace(/[\uff21-\uff5a\uff01-\uff5e\uff10-\uff19]/g,c=>String.fromCharCode(c.charCodeAt(0)-0xfee0));
const norm=s=>kanaHira(normZ((s||"").toLowerCase().replace(/[\sー－]/g,"")));
function checkType(){
  if(answered)return;answered=true;
  const q=curQ();const i=document.getElementById("typin");
  const ok=q.ansTxt.map(norm).includes(norm(i&&i.value));
  if(ok)correct++;else addWrong(q.ref);
  if(ok&&mode==="wrong")delWrong(q.ref);
  $("#fb").innerHTML=`<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！正解：<b>"+esc(q.ansTxt.join(" / "))+"</b>"}<br>${q.exp||""}</div>`;
  showNext(true);updateScore();
  window.scrollTo(0,document.body.scrollHeight);
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
  const msg=pct===100?"🏆 完璧！这个词已经长住脑里了！":pct>=70?"👍 かなりいい！错题趁热打铁":"🔁 回『语义网络』再扎根一轮，错题会自动收进错题本";
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

/* ---------- 间隔复习 ---------- */
function dstr(d){return d.toISOString().slice(0,10);}
function addDays(d,n){const x=new Date(d);x.setDate(x.getDate()+n);return x;}
function anchorDate(){
  let a=lsGet(lsKey("first"),null);
  if(!a){a=W.firstSeen||dstr(new Date());if(a<dstr(new Date()))a=dstr(new Date());lsSet(lsKey("first"),a);}
  return a;
}
function schedMap(){return lsGet(lsKey("sched"),{});}
function todayOff(){
  const a=new Date(anchorDate());const now=new Date();
  return Math.max(0,Math.round((now-a)/864e5));
}
function renderSpc(){
  const off=todayOff();const sm=schedMap();
  const a=anchorDate();
  const sch=SCHED[W.id]||SCHED[WORDS[0].id];
  let tl=sch.map(([d,label,task])=>{
    const done=!!sm[d];
    const cls=done?"done":(off>=d?"now":"day-lock");
    return `<div class="day ${cls}">
      <b>${done?"✓":label}</b><div class="off">+${d} 天 · ${addDays(new Date(a),d).toISOString().slice(0,10)}</div>
      <button onclick="markDay(${d},this)">${done?"重做":(off>=d?"完成 ✓":"未到期")}</button></div>`;
  }).join("");
  const cur=sch.filter(([d])=>off>=d);
  const due=cur[cur.length-1];
  const hasDue=due&&!sm[due[0]];
  let h=`<h3 class="sec">间隔复习 —— 反遗忘曲线，不靠拼命先靠定时</h3>
  <div class="card scene">
    <div class="place">🕰 锚点：初见日 ${a} ｜ 距今 ${off} 天</div>
    <div class="tl">${tl}</div>
    <div class="hint">复习节奏：当天 → +1 → +3 → +7 → +14 → +30 天。次数不多，贵在“到点就到”。</div>
  </div>`;
  if(hasDue){
    h+=`<div class="card due">
      <div class="bigoff">📌 今天轮到：+${due[0]} 天（${due[1]}）</div>
      <h4>这次做三件事</h4>
      <div class="taskc">${due[2]}<br><br>完成后回来点上面的「完成 ✓」，进度存本机。</div>
    </div>`;
  }else{
    h+=`<div class="card due"><div style="text-align:center;font-size:16px;font-weight:700">🎉 今日任务已清 — 到下一个 +${(sch.find(([d])=>off<d)||["∞"])[0]} 天再来</div></div>`;
  }
  h+=`<div class="card" style="text-align:center">
    <div class="hint" style="text-align:center">间隔复习的底牌是「提取」：到点先凭记忆回想，想不出再看卡。</div>
    <button class="opt" style="max-width:280px;margin:12px auto 0;font-size:13px;padding:8px" onclick="if(confirm('重置复习锚点到今天？')){localStorage.removeItem(lsKey('first'));lsSet(lsKey('sched'),{});renderSpc();}">🔄 重置复习计划</button>
  </div>`;
  $("#barinfo").textContent="间隔复习：定时提取，胜过集中死记";
  $("#main").innerHTML=h;
}
function markDay(d,btn){
  const sm=schedMap();sm[d]=!sm[d];lsSet(lsKey("sched"),sm);
  renderSpc();
}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="enc")renderEnc();
  else if(tab==="nade")renderNade();
  else if(tab==="web")renderWeb();
  else if(tab==="cmp")renderCmp();
  else if(tab==="quiz")renderQuiz();
  else renderSpc();
}
function showNext(v){const b=$("#next");b.style.display=v?"inline-block":"none";b.disabled=!v;}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    meta, words = load_data()
    print("[0/4] data ok")

    sents = build_sents(words)
    audio, n_moji = gen_audio(meta, words)
    qs = build_questions(words, audio)
    clips = build_clips(words)
    for lid, c in clips.items():
        audio[lid] = c["mp3"]
    words_out = words  # 原样嵌入（含 encounter/core/senses/contrast/...）

    print("[2/4] generating quiz banks...")
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, l in BANK_META:
        print(f"      {l}: {counts[k]} 問")

    tags = (f"<span>{len(words)} 個新詞</span><span>初遇情景</span>"
            f"<span>{len(clips)} 段番剧原声</span>"
            f"<span>{n_moji} MOJi 原声</span><span>间隔复习</span>")
    sched = {w["id"]: build_schedule(w) for w in words}

    print("[3/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__META__", j(meta))
            .replace("__WORDS__", j(words_out))
            .replace("__SENTS__", j(sents))
            .replace("__CLIPS__", j(clips))
            .replace("__BANKS__", j(BANK_META))
            .replace("__SCHED__", j(sched))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[4/4] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()