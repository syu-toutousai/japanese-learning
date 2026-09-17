#!/usr/bin/env python3
"""Build the self-contained 「見えない助詞」courseware HTML.

「見えない助詞・句法の省略と中止形」：格助詞の省略（零助詞）、連用中止形、
係り・切れ目（修飾の归属）、言い切り（体言止め等）——日语里助词看不见但语法仍在。
随时丢进 mienai.json，跑一次 build.py，卡片、发音、题库自动重新长出来。

诚实化立场：省略/中止是文法的本体（语序+活用形+语调），标点、空白、停顿只是把它
「变醒目」的表现层。每张中止卡片都附「真話」注，避免把理论教错。

音频：edge-tts 日语神经网络语音（ja-JP-Nanami），按文本哈希缓存到 audio/；
MOJi 原生 mp3（readAudio/example.audio）优先覆盖；Nadeshiko 原声（nadeshiko[]）内嵌。
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

ROOT = Path(__file__).parent
POCKET = ROOT / "mienai.json"
AUDIO_DIR = ROOT / "audio"
NADE_CACHE = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20260918          # 固定随机种子：干扰项抽样可复现，重复构建 diff 干净
MIN_MP3 = 300            # 小于该字节数视为合成失败
BANK_META = [
    ["recog",   "📘 働き認識"],
    ["fill",    "✍️ 補格填空"],
    ["chuushi", "🔀 中止形判別"],
    ["listen",  "🎧 聴解判別"],
    ["custom",  "⭐ 自作題"],
]

CHUUSHI_ROLE = {   # 中止形の功能 → 选项显示
    "next":      "順序（〜て 的替代）",
    "cause":     "因果（〜から/ので 的替代）",
    "contrast":  "対比（〜が/けど 的替代）",
    "parallel":  "並列・列挙（〜たり/〜て 的替代）",
    "polite":    "文語・ご書面（書き言葉の丁寧中止）",
}


def j(obj):
    """json for embedding inside <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_pocket():
    try:
        data = json.loads(POCKET.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] pocket.json 不是合法 JSON：{e}")
    errors = []
    groups = data.get("groups")
    items = data.get("items")
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
            errors.append(f"条目 id「{iid}」只能用字母数字-_（要嵌进 HTML 属性）")
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

    if errors:
        print("[!] pocket.json 有问题，先修好再构建：")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    return groups, items


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
    ("降り続く", "ふりつづく"),
    ("他人の真似", "たにんのまね"),
    ("空回り", "からまわり"),
    ("雨が降り", "あめがふり"),
    ("風邪を引き", "かぜをひき"),
    ("物語", "ものがたり"),
    ("彼は", "かれは"),
    ("経歴も", "けいれきも"),
    ("究明し", "きゅうめいし"),
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
    remaining kanji via pykakasi. Only affects the display copy."""
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


def gen_audio(items, sents):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []                       # (logical_id, text)
    for it in items:
        native_read = it.get("readAudio")
        if not (native_read and (ROOT / native_read).exists()):
            tasks.append((it["id"], it["read"]))
        for i, ex in enumerate(it.get("examples") or []):
            native_ex = ex.get("audio")
            if not (native_ex and (ROOT / native_ex).exists()):
                tasks.append((f"{it['id']}-e{i}", ex["jp"]))

    todo = [(lid, txt) for lid, txt in tasks
            if not (p := cache_path(lid, txt)).exists() or p.stat().st_size <= MIN_MP3]
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
    mount_native_audio(audio, items)
    return audio


def mount_native_audio(audio, items):
    """用 MOJi 原生 mp3 覆盖 TTS：item.readAudio 作兼類詞读音，example.audio 作例句读音。"""
    for it in items:
        rel = it.get("readAudio")
        if rel and (ROOT / rel).exists():
            audio[it["id"]] = "data:audio/mpeg;base64," + base64.b64encode((ROOT / rel).read_bytes()).decode()
        for i, ex in enumerate(it.get("examples") or []):
            rel = ex.get("audio")
            if rel and (ROOT / rel).exists():
                audio[f"{it['id']}-e{i}"] = "data:audio/mpeg;base64," + base64.b64encode((ROOT / rel).read_bytes()).decode()

# ---------------------------------------------------------------- nadeshiko audio

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


def embed_nade_thumbs(items):
    """下载 Nadeshiko 缩略图 webp 并 base64 内嵌到 scene.thumb（离线）。"""
    NADE_CACHE.mkdir(exist_ok=True)
    tasks = []
    for it in items:
        for i, sc in enumerate(it.get("nadeshiko") or []):
            if sc.get("thumb") and not sc["thumb"].startswith("data:"):
                tasks.append((f"{it['id']}-n{i}", sc))
    if not tasks:
        return
    ok = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        def dl(lid, sc):
            url = sc["thumb"]
            h = hashlib.sha1(url.encode()).hexdigest()[:10]
            path = NADE_CACHE / f"{lid}-{h}.webp"
            if path.exists() and path.stat().st_size > 200:
                return "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode()
            for _ in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read()
                    if len(data) > 200:
                        path.write_bytes(data)
                        return "data:image/webp;base64," + base64.b64encode(data).decode()
                except Exception:
                    pass
            return None
        futures = {ex.submit(dl, lid, sc): (lid, sc) for lid, sc in tasks}
        for fut in futures:
            data = fut.result()
            if data:
                futures[fut][1]["thumb"] = data
                ok += 1
    print(f"      thumb: {ok}/{len(tasks)} embedded")


def gen_nade_audio(items):
    tasks = []
    for it in items:
        for i, sc in enumerate(it.get("nadeshiko") or []):
            if sc.get("audio"):
                tasks.append((f"{it['id']}-n{i}", sc["audio"]))
    if not tasks:
        return {}
    nade_audio = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(download_nade_audio, url, lid): lid for lid, url in tasks}
        for fut in futures:
            data = fut.result()
            if data:
                nade_audio[futures[fut]] = data
    print(f"      nade: {len(nade_audio)}/{len(tasks)} clips cached")
    return nade_audio


# ---------------------------------------------------------------- auto quizzes

def build_questions(groups, items, audio):
    rng = random.Random(SEED)
    dup_words = {}
    for it in items:
        dup_words[it["word"]] = dup_words.get(it["word"], 0) + 1

    def disp(it):
        return f'{it["word"]}（{it["read"]}）' if dup_words[it["word"]] > 1 else it["word"]

    def others(it, field, count=3):
        own = it.get(field)
        if not own:
            return []
        rest = [x[field] for x in items
                if x["id"] != it["id"] and x.get(field, "") != own]
        rng.shuffle(rest)
        out = []
        for cand in rest:
            if len(out) >= count:
                break
            if cand not in out:
                out.append(cand)
        return out

    def part_pool():
        pool = []
        for it in items:
            d = it.get("drop")
            if d and d not in pool:
                pool.append(d)
        return pool

    def blank_particle(sentence, drop):
        return sentence.replace(drop, "（　）", 1) if drop in sentence else sentence

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    ppool = part_pool()
    for it in items:
        iid = it["id"]
        note = it.get("note", "")

        # 📘 働き認識：这个省略/中止/切れ目 表达的作用
        add("recog", f"{iid}:recog", type="choice",
            q=f"「{disp(it)}」这个句子/句式在日语里起什么作用？",
            opts=[it["meaning"]] + others(it, "meaning"), ans=0,
            exp=f'{it["word"]}＝{it["meaning"]}'
                + (f"<br>💡 {note}" if note else ""))

        # ✍️ 補格填空（零助詞组）：把「見えない」助词找回来
        drop = it.get("drop")
        if drop and it.get("standard"):
            opts = [drop] + [p for p in ppool if p != drop][:3]
            rng.shuffle(opts)
            add("fill", f"{iid}:fill", type="choice",
                q=f'標準形：{blank_particle(it["standard"], drop)}<br>'
                  f'<span class="hint">口语里，这道（　）里的助词被吞掉了。它本来是什么？</span>',
                opts=opts, ans=opts.index(drop),
                exp=f'入れると：{it["standard"]}<br>省略すると：{it["word"]}<br>省略＝語順が役割を守っているから')

        # 🔀 中止形判別（中止形组）：連用形中止的功能
        role = it.get("role")
        if role in CHUUSHI_ROLE:
            ex = (it.get("examples") or [{}])[0]
            role_opts = [CHUUSHI_ROLE[role]] + \
                        [v for k, v in CHUUSHI_ROLE.items() if k != role][:3]
            rng.shuffle(role_opts)
            add("chuushi", f"{iid}:chuushi", type="choice",
                q=f'「{ex.get("jp", "")}」<br>'
                  f'<span class="hint">句中的<code class="inline">連用形中止</code>强调的是哪个功能？</span>',
                opts=role_opts, ans=role_opts.index(CHUUSHI_ROLE[role]),
                exp=f'連用形中止 → {CHUUSHI_ROLE[role]}。<br>'
                    + (f'（ないし は 標準形：{it["standard"]}）' if it.get("standard") else "")
                    + f'<br>接续力在活用形本身，逗号只是把边界显示出来。')

        # 🎧 聴解判別：听例句，判断是哪个「見えない助詞/句式」
        exs = it.get("examples") or []
        if exs and f"{iid}-e0" in audio:
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 听音频：这个省略/中止表达对应下面哪一项？",
                opts=[disp(it)] + others(it, "word"), ans=0,
                exp=f'原句：{exs[0]["jp"]}<br>{exs[0].get("cn", "")}'
                    + f'<br>{it["word"]}＝{it["meaning"]}')

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
<title>見えない助詞 ・ 句法の省略と中止形</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#0f766e,#14b8a6);color:#fff;padding:26px 20px 20px}
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
/* intro */
.intro{background:linear-gradient(135deg,#e6fbf7,#f0fdfa)}
.intro h2{font-size:17px;margin-bottom:8px;color:#0f766e}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#0f766e}
/* group & mini cards */
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#0ca678)}
.mini .em{font-size:26px}
.mini .nm{font-weight:800;font-size:16px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
/* detail cards */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#0ca678)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.chip.pfx{background:#e7f1ff;color:#1971c2}
.chip.sfx{background:#fff0e6;color:#e8590c}
.mini-btn{width:26px;height:26px;font-size:12px}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
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
/* source badges */
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;
margin-left:6px;vertical-align:1px}
.src-moji{background:var(--okbg);color:var(--ok)}
.src-nade{background:#ede7f6;color:#5e35b1}
/* nadeshiko scene */
.nade-card{background:#faf5ff;border:1px solid #e0d0f0;border-radius:12px;padding:12px;margin:10px 0}
.nade-card .nade-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.nade-card .nade-media{font-weight:700;color:#5e35b1;font-size:13px}
.nade-card .nade-ep{font-size:11.5px;color:var(--sub)}
.nade-card .nade-jp{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif;
font-size:15.5px;line-height:2}
.nade-card .nade-jp ruby rt{font-size:.52em;color:var(--sub)}
.nade-card .nade-en{font-size:12.5px;color:var(--sub);margin-top:3px;font-style:italic}
.nade-card .nade-cn{font-size:13px;color:var(--ink);margin-top:2px}
.nade-card .nade-row{display:flex;gap:10px;align-items:flex-start}
.nade-card .nade-thumb{width:84px;height:52px;border-radius:8px;object-fit:cover;flex:none}
.nade-card a{color:#5e35b1;font-size:11.5px;text-decoration:none}
.nade-card a:hover{text-decoration:underline}
</style>
</head>
<body>
<header><div class="wrap">
<h1>見えない助詞</h1>
<div class="kana">みえないじょし ／ 格助詞の省略・中止形・係り切れ目・言い切り —— 助词不见了，语法还在 ◌</div>
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
const SENTS=__SENTS__;
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
function rowHTML(sid){
  const s=SENTS[sid];
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  const chip=s.src==="moji"?`<span class="src src-moji">MOJi</span>`:"";
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
const TABS=[["list","🗺️ 一覧"],["detail","📖 詳細"],["quiz","🎯 クイズ"]];
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
  let h=`<div class="card intro"><h2>这是什么课？◌</h2>
  <p>日语句子里的<code class="inline">助词</code>并不总是现身：<b>「ご飯食べる？」</b>少了 を、<b>「天気いいね」</b>少了 が/は、
  <b>「脱ぎ、入った」</b>用<code class="inline">連用形中止</code>代替了 て——省略・中止是<code class="inline">文法本体</code>，
  语序、活用形、停顿把语法关系稳稳扛住；字面上的<b>空格和标点只是把它「变 醒目」</b>而已。
  本课件按四类「見えない文法」整理，新句式丢进 <code class="inline">mienai.json</code> 照着加一条，跑
  <code class="inline">python3 build.py</code>——卡片、发音、题库自动重新长出来。</p>
  <div class="steps">
    <div><b>① 省略的助词：語順が役割を守る</b><br>ご飯食べる？＝ご飯<u>を</u>食べる？——助词消失后，名词与动词的先后位置继续表明对象关系。</div>
    <div><b>② 中止形：接续力在活用形</b><br>脱ぎ、入った＝脱い<u>で</u>入った——是「連用形」在绑定两件事，逗号只是显示边界。</div>
    <div><b>③ 切れ目：停顿改变修饰归属</b><br>泣きながら、走る彼を追いかけた——逗号让「泣きながら」跳去修饰主句动词。</div>
    <div><b>④ 言い切り：句号承担语气的余韵</b><br>降り続く雨。／行きたいんですけど。——名词收尾、言いさし、语気断ち。</div>
  </div></div>`;
  h+=GROUPS.map(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return "";
    return `<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.name}</h3><span>${members.length} 词</span></div>
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="em">${n.emoji||"📌"}</div>
        <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
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
      const tagChip=n.tag?`<span class="chip pfx" title="句式类型">${n.tag}</span>`:"";
      const dropChip=n.drop?`<span class="chip sfx" title="被省略的助词">◌ ${n.drop} が見えない</span>`:"";
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji||"◌"} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta">${tagChip}${dropChip}
          <code>${n.read}</code>
          ${AUDIO[iid]?`<button class="btn mini-btn" title="听读音" onclick="play('${iid}',this)">▶</button>`:""}
        </div>
        <div class="meanbox">📌 <b>働き</b>　${n.meaning}${n.standard?`<br>◌ <b>助词在位时</b>　${n.standard}`:""}</div>
        ${(n.examples||[]).map((_,i)=>rowHTML(`${iid}-e${i}`)).join("")}
        ${(n.nadeshiko&&n.nadeshiko.length)?`<h3 class="sec">🎬 原声台词</h3>`:""}
        ${(n.nadeshiko||[]).map((sc,i)=>nadeHTML(sc,iid,i)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("mienai-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("mienai-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("mienai-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const customHint=countBank("custom")?"":"";
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 mienai.json 自动生成 · 条目越多题库越大 · 全部随机打乱</div>
      ${rows}${customHint}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨句式穿插练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('mienai-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
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
  const msg=pct===100?"🏆 完璧！日本人也没听出这助词不见了！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 詳細タブで復習してから再挑戦";
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
    groups, items = load_pocket()
    n_group = len(groups)
    n_nade = sum(len(it.get("nadeshiko", [])) for it in items)

    audio = gen_audio(items, None)

    print(f"[1/4] audio: {len(audio)} clips ready")

    print("[2/4] nadeshiko: downloading CDN audio...")
    nade_audio = gen_nade_audio(items)
    embed_nade_thumbs(items)

    qs = build_questions(groups, items, audio)

    tags = (f"<span>{len(items)} 句式</span><span>{n_group} 大分類</span>"
            f"<span>{len(audio)} 音声{' + ' + str(n_nade) + ' 原声' if n_nade else ''}</span>"
            f"<span>mienai.json 随手加</span>")
    banks_meta = [[k, l] for k, l in BANK_META]

    print("[3/4] generating quiz banks...")
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, l in BANK_META:
        print(f"      {l}: {counts[k]} 問")

    # display 副本：给例句和 Nadeshiko 台词加 ruby 振假名（只作用展示，不动素文/出题/音频）
    display = copy.deepcopy(items)
    sents = {}
    for it in display:
        for i, ex in enumerate(it.get("examples") or []):
            ex["jp"] = add_furigana(ex["jp"])
            sents[f"{it['id']}-e{i}"] = {"jp": ex["jp"], "cn": ex.get("cn", ""), "src": ex.get("src", "")}
        for sc in it.get("nadeshiko") or []:
            sc["jp"] = add_furigana(sc["jp"])

    print("[4/4] rendering template...")
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__AUDIO__", j(audio))
            .replace("__NADE_AUDIO__", j(nade_audio))
            .replace("__GROUPS__", j(groups))
            .replace("__ITEMS__", j(display))
            .replace("__SENTS__", j(sents))
            .replace("__BANKS__", j(banks_meta))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[5/4] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
