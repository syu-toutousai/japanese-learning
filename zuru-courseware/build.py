#!/usr/bin/env python3
"""Build the self-contained 「ずる⇄じるの系譜」courseware HTML.

用法：往 zuru.json 里随手加一条「漢語＋する → ずる → じる」→ 运行本脚本 →
index.html 自动长出卡片、发音和题库。

音频：优先内嵌 MOJi 原生 mp3；无原生音频的用 edge-tts 日语神经网络语音
（ja-JP-Nanami）合成，按文本哈希缓存到 audio/，改了句子会自动重录；
某条合成失败只跳过该条发音，不影响整体构建。
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
POCKET = ROOT / "zuru.json"
AUDIO_DIR = ROOT / "audio"
NADE_CACHE = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20261009          # 固定随机种子：干扰项抽样可复现，重复构建 diff 干净
MIN_MP3 = 300            # 小于该字节数视为合成失败
BANK_META = [
    ["listen2", "🎧 聴解・意味理解"],
    ["listen3", "🎧 聴解・書き取り"],
    ["listen",  "🎧 聴解判別"],
    ["fill",    "✍️ 運用填空"],
    ["pair",    "💱 ずる⇄じる 対応"],
    ["katsuyo", "🧬 活用の型"],
    ["form",    "🌀 活用形ドリル"],
]


def j(obj):
    """json for embedding inside <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_pocket():
    try:
        data = json.loads(POCKET.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] zuru.json 不是合法 JSON：{e}")
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
        for key in ("word", "zuru", "jiru", "readZuru", "readJiru",
                    "base", "level", "meaning", "chain", "register"):
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
        print("[!] zuru.json 有问题，先修好再构建：")
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
    ("胸の内", "むねのうち"),
    ("真意", "しんい"),
    ("玉串", "たまぐし"),
    ("神前", "しんぜん"),
    ("重んずる", "おもんずる"),
    ("重んじる", "おもんじる"),
    ("軽んずる", "かろんずる"),
    ("軽んじる", "かろんじる"),
    ("疎んずる", "うとんずる"),
    ("疎んじる", "うとんじる"),
    ("肯んじる", "がえんじる"),
    ("重ん", "おもん"),
    ("軽ん", "かろん"),
    ("疎ん", "うとん"),
    ("肯ん", "がえん"),
    ("甘んじて", "あまんじて"),
    ("甘んずる", "あまんずる"),
    ("甘んじる", "あまんじる"),
    ("安んずる", "やすんずる"),
    ("安んじる", "やすんじる"),
    ("按ずる", "あんずる"),
    ("按じる", "あんじる"),
    ("順じて", "じゅんじて"),
    ("順じる", "じゅんじる"),
    ("殉じる", "じゅんじる"),
    ("殉ずる", "じゅんずる"),
    ("混ずる", "こんずる"),
    ("混じる", "こんじる"),
    ("敵を", "てきを"),
    ("浅さ", "あささ"),
    ("用を便じる", "ようをべんじる"),
    ("用が便じない", "ようがべんじない"),
    ("月を", "つきを"),
    ("古の人", "いにしえのひと"),
    ("動じない人", "どうじないひと"),
    ("重んずる人", "おもんずるひと"),
    ("悪が", "あくが"),
    ("進もう", "すすもう"),
    ("一計", "いっけい"),
    ("眼中", "がんちゅう"),
    ("大通り", "おおどおり"),
    ("手取り", "てどり"),
    ("店を開いた", "みせをひらいた"),
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
        if not (it.get("zuruAudio") and (ROOT / it["zuruAudio"]).exists()):
            tasks.append((f"{it['id']}-z", it["readZuru"]))
        if not (it.get("jiruAudio") and (ROOT / it["jiruAudio"]).exists()):
            tasks.append((f"{it['id']}-j", it["readJiru"]))
        for i, ex in enumerate(it.get("examples") or []):
            if not (ex.get("audio") and (ROOT / ex["audio"]).exists()):
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

    # 清理不再被引用的旧缓存（改句子后遗留的 mp3）
    keep = {cache_path(lid, txt).name for lid, txt in tasks}
    for it in items:
        for field in ("zuruAudio", "jiruAudio"):
            rel = it.get(field)
            if rel:
                keep.add(Path(rel).name)
        for ex in it.get("examples") or []:
            rel = ex.get("audio")
            if rel:
                keep.add(Path(rel).name)
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
    """用 MOJi 原生 mp3 覆盖 TTS：zuruAudio/jiruAudio 作词条读音，example.audio 作例句读音。"""
    for it in items:
        for key, field in (("z", "zuruAudio"), ("j", "jiruAudio")):
            rel = it.get(field)
            if rel and (ROOT / rel).exists():
                lid = f"{it['id']}-{key}"
                audio[lid] = "data:audio/mpeg;base64," + base64.b64encode(
                    (ROOT / rel).read_bytes()).decode()
        for i, ex in enumerate(it.get("examples") or []):
            rel = ex.get("audio")
            if rel and (ROOT / rel).exists():
                audio[f"{it['id']}-e{i}"] = "data:audio/mpeg;base64," + base64.b64encode(
                    (ROOT / rel).read_bytes()).decode()


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

def surface_of(text, it):
    """Find the longest inflected surface of the item verb inside text.
    Returns (surface, suffix, is_zuru) or None."""
    pz, pj = it["zuru"][:-2], it["jiru"][:-2]
    best = None
    for p, is_z in ((pz, True), (pj, False)):
        if not p:
            continue
        for suf in ["ずる", "ずれ", "ぜよ", "ぜ", "じる", "じれ", "じろ",
                    "じて", "じた", "じます", "じない", "じ", "ず"]:
            s = p + suf
            i = text.find(s)
            if i >= 0 and (best is None or len(s) > len(best[0])):
                best = (s, suf, is_z)
    return best


def build_questions(groups, items, audio):
    rng = random.Random(SEED)
    sent_pool = []
    for it in items:
        exs = it.get("examples") or []
        if exs and exs[0].get("jp") and exs[0].get("cn"):
            sent_pool.append({"id": it["id"], "jp": exs[0]["jp"], "cn": exs[0]["cn"]})

    def disp(it):
        return it["word"]

    def pick_distractors(it, field):
        own = it[field]
        same = [x[field] for x in items
                if x["group"] == it["group"] and x["id"] != it["id"] and x[field] != own]
        rest = [x[field] for x in items
                if x["group"] != it["group"] and x[field] != own]
        rng.shuffle(same)
        rng.shuffle(rest)
        out = []
        for cand in same + rest:
            if len(out) >= 3:
                break
            if cand not in out:
                out.append(cand)
        return out

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    FORM_QS = [
        ("z_mizen", "「{zuru}」の未然形（「〜ない」に続く形）は？",
         lambda it: it["zuru"][:-2] + "ぜ",
         lambda it: [it["zuru"][:-2] + s for s in ("じ", "ずれ", "じれ")],
         lambda it: f'{it["zuru"]} → 未然形「{it["zuru"][:-2]}ぜ」＋「ない」＝{it["zuru"][:-2]}ぜない。<br>'
                    f'一方 {it["jiru"]} は上一段なので「{it["jiru"][:-2]}じない」。'),
        ("j_mizen", "「{jiru}」の未然形（「〜ない」に続く形）は？",
         lambda it: it["jiru"][:-2] + "じ",
         lambda it: [it["jiru"][:-2] + s for s in ("ぜ", "ずれ", "じれ")],
         lambda it: f'{it["jiru"]} は上一段活用で、未然形は「{it["jiru"][:-2]}じ」：'
                    f'「{it["jiru"][:-2]}じない」。'),
        ("z_katei", "「{zuru}」の仮定形（「〜ば」に続く形）は？",
         lambda it: it["zuru"][:-2] + "ずれ",
         lambda it: [it["zuru"][:-2] + s for s in ("じれ", "ぜ", "じ")],
         lambda it: f'{it["zuru"]} → 仮定形「{it["zuru"][:-2]}ずれ」＋「ば」：'
                    f'「{it["zuru"][:-2]}ずれば」。'),
        ("j_katei", "「{jiru}」の仮定形（「〜ば」に続く形）は？",
         lambda it: it["jiru"][:-2] + "じれ",
         lambda it: [it["jiru"][:-2] + s for s in ("ずれ", "じ", "ぜ")],
         lambda it: f'{it["jiru"]} → 仮定形「{it["jiru"][:-2]}じれ」＋「ば」：'
                    f'「{it["jiru"][:-2]}じれば」。'),
        ("z_meirei", "「{zuru}」の命令形は？",
         lambda it: it["zuru"][:-2] + "ぜよ",
         lambda it: [it["zuru"][:-2] + s for s in ("じろ", "ずれ", "じれ")],
         lambda it: f'{it["zuru"]} の命令形は「{it["zuru"][:-2]}ぜよ」——演説・時代劇調の'
                    f'「{it["zuru"][:-2]}ぜよ！」。'),
        ("j_meirei", "「{jiru}」の命令形は？",
         lambda it: it["jiru"][:-2] + "じろ",
         lambda it: [it["jiru"][:-2] + s for s in ("ぜよ", "じれ", "ずれ")],
         lambda it: f'{it["jiru"]} の命令形は「{it["jiru"][:-2]}じろ」。'
                    f'一見乱暴だが上一段の規則どおり。'),
    ]

    for it in items:
        iid = it["id"]
        note = it.get("note", "")
        z, jf = it["zuru"], it["jiru"]

        # 💱 ずる⇄じる 対応
        add("pair", f"{iid}:pair", type="choice",
            q=f'「{z}」の上一段形（現代口語の相棒）は？',
            opts=[jf] + pick_distractors(it, "jiru"), ans=0,
            exp=f'{z}（サ行変格）⇄ {jf}（上一段）<br>💡 {it["chain"]}')

        # 🧬 活用の型
        add("katsuyo", f"{iid}:katsuyo-z", type="choice",
            q=f'「{z}」の活用の型は？',
            opts=[f"{z}＝サ行変格活用", f"{z}＝上一段活用",
                  f"{z}＝五段活用", f"{z}＝下一段活用"], ans=0,
            exp=f'{z} はサ行変格活用（する型の連濁）。終止形「{z}」・仮定形「{z[:-2]}ずれ」。')
        add("katsuyo", f"{iid}:katsuyo-j", type="choice",
            q=f'「{jf}」の活用の型は？',
            opts=[f"{jf}＝上一段活用", f"{jf}＝サ行変格活用",
                  f"{jf}＝五段活用", f"{jf}＝下一段活用"], ans=0,
            exp=f'{jf} はザ行上一段活用。「{jf[:-2]}じ・{jf[:-2]}じ・{jf}・{jf}・'
                f'{jf[:-2]}じれ・{jf[:-2]}じろ」と規則的に活用する。')

        # 🌀 活用形ドリル（6 問から 2 問を固定シードで抽出）
        for tag, qtpl, ok_f, ng_f, exp_f in rng.sample(FORM_QS, 2):
            add("form", f"{iid}:form-{tag}", type="choice",
                q=qtpl.format(zuru=z, jiru=jf),
                opts=[ok_f(it)] + ng_f(it), ans=0,
                exp=exp_f(it) + (f'<br>💡 {note}' if note else ''))

        # ✍️ 運用填空：例句中实际出现的活用形挖空
        for i, ex in enumerate(it.get("examples") or []):
            hit = surface_of(ex["jp"], it)
            if not hit:
                continue
            surf, suf, is_z = hit
            blanked = ex["jp"].replace(surf, "（　）", 1)
            stems = []
            for x in items:
                if x["id"] == iid:
                    continue
                stems.append(x["zuru"][:-2] if is_z else x["jiru"][:-2])
            rng.shuffle(stems)
            d = []
            for stem in stems:
                cand = stem + suf
                if cand != surf and cand not in d:
                    d.append(cand)
                if len(d) >= 3:
                    break
            add("fill", f"{iid}:fill-{i}", type="choice",
                q=f'{blanked}<br><span class="hint">（　）里填回哪个词形？</span>',
                opts=[surf] + d, ans=0,
                exp=f'完整句子：{ex["jp"]}<br>{ex["cn"]}'
                    + (f'（{z}／{jf}：{it["meaning"]}）'))

        # 🎧 聴解判別：听例句判断用了哪个词
        exs = it.get("examples") or []
        if exs and f"{iid}-e0" in audio:
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 听音频：句子里用了哪一组「ずる／じる」动词？",
                opts=[disp(it)] + pick_distractors(it, "word"), ans=0,
                exp=f'原句：{exs[0]["jp"]}<br>{exs[0]["cn"]}'
                    + (f'<br>💡 {note}' if note else ''))
        # 🎧 聴解・意味理解／書き取り
        ex0 = (exs or [{}])[0]
        if f"{iid}-e0" in audio and ex0.get("cn"):
            cn_cands, jp_cands = [], []
            for s in sent_pool:
                if s["id"] == iid:
                    continue
                if s["cn"] != ex0["cn"] and s["cn"] not in cn_cands:
                    cn_cands.append(s["cn"])
                if s["jp"] != ex0["jp"] and s["jp"] not in jp_cands:
                    jp_cands.append(s["jp"])
            rng.shuffle(cn_cands)
            rng.shuffle(jp_cands)
            if len(cn_cands) >= 3:
                add("listen2", f"{iid}:listen2", type="listen", aid=f"{iid}-e0",
                    q="🎧 听音频：这句话的意思最接近哪一项？",
                    opts=[ex0["cn"]] + cn_cands[:3], ans=0,
                    exp=f'原句：{ex0["jp"]}<br>{ex0["cn"]}')
            if len(jp_cands) >= 3:
                add("listen3", f"{iid}:listen3", type="listen", aid=f"{iid}-e0",
                    q="🎧 听音频：说的是哪一句？",
                    opts=[ex0["jp"]] + jp_cands[:3], ans=0,
                    exp=f'原句：{ex0["jp"]}<br>{ex0["cn"]}')

        # ⭐ 自作题原样收录
        for n, cq in enumerate(it.get("quizzes") or []):
            q = dict(cq)
            aid = q.get("aid")
            if isinstance(aid, int):
                q["aid"] = f"{iid}-e{aid}"
            q.setdefault("exp", "")
            add("custom", f"{iid}:custom-{n}", **q)

    rng.shuffle(qs)
    return qs


# ---------------------------------------------------------------- html template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ずる⇄じるの系譜 ・ 漢語動詞の二つの着こなし</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#312e81,#7c3aed);color:#fff;padding:26px 20px 20px}
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
.intro{background:linear-gradient(135deg,#eef0ff,#f7f5ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#4c3d9e}
.intro p{font-size:14px;line-height:1.75}
.intro b{color:#6d28d9}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#6d28d9}
/* group & mini cards */
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-note{font-size:12px;color:var(--sub);margin:6px 2px 0;line-height:1.6}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#6d28d9)}
.mini .em{font-size:26px}
.mini .nm{font-weight:800;font-size:15.5px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.mini .kn{font-size:11.5px;color:#6d28d9;font-weight:700;margin-bottom:2px}
/* detail cards */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#6d28d9)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.chip.reg{background:#efe9ff;color:#6d28d9}
.mini-btn{width:26px;height:26px;font-size:12px}
.meanbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.origin{background:#fffaf0;border-left:4px solid var(--gold);border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.7;margin:2px 0 10px}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* conjugation table */
table.conj{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0 2px}
table.conj th,table.conj td{border:1px solid var(--line);padding:5px 7px;text-align:center;line-height:1.6}
table.conj th{background:#f6f4ff;color:#4c3d9e;font-weight:700}
table.conj td:first-child{background:#fafaff;font-weight:700;color:var(--sub);white-space:nowrap}
table.conj .z{color:#b45309}
table.conj .j{color:#0f766e}
/* contrast tab */
.flow{display:flex;flex-direction:column;gap:8px;margin:8px 0}
.flow .st{border-radius:12px;padding:10px 14px;font-size:13.5px;line-height:1.7;background:#fff;border-left:5px solid #6d28d9}
.flow .st b{color:#6d28d9}
.flow .st .arrow{color:#9ca3af;font-size:12px}
table.cmp{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
table.cmp th,table.cmp td{border:1px solid var(--line);padding:7px 9px;line-height:1.65}
table.cmp th{background:#f6f4ff;color:#4c3d9e}
table.cmp td:first-child{white-space:nowrap;background:#fafaff;font-weight:700;color:var(--sub)}
.tagz{color:#b45309;font-weight:700}.tagj{color:#0f766e;font-weight:700}
.warn{background:#fff7ed;border-left:4px solid #f59e0b;border-radius:10px;padding:10px 13px;font-size:13.5px;line-height:1.75;margin:8px 0}
.good{background:#f0fdf4;border-left:4px solid #22c55e;border-radius:10px;padding:10px 13px;font-size:13.5px;line-height:1.75;margin:8px 0}
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
<h1>ずる⇄じるの系譜</h1>
<div class="kana">ずる と じる ／ 漢語動詞の二つの着こなし —— 連濁と上一段化 🌀</div>
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
  const s=SENTS[sid];if(!s)return"";
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
/* 活用対照 mini table */
function conjTable(n){
  const z=n.zuru.slice(0,-2), j=n.jiru.slice(0,-2);
  return `<table class="conj">
    <tr><th>活用形</th><th>${n.zuru}</th><th>${n.jiru}</th></tr>
    <tr><td>未然形（〜ない）</td><td class="z">${z}ぜない</td><td class="j">${j}じない</td></tr>
    <tr><td>連用形（〜ます）</td><td class="z">${z}じます</td><td class="j">${j}じます</td></tr>
    <tr><td>終止形</td><td class="z">${n.zuru}</td><td class="j">${n.jiru}</td></tr>
    <tr><td>仮定形（〜ば）</td><td class="z">${z}ずれば</td><td class="j">${j}じれば</td></tr>
    <tr><td>命令形</td><td class="z">${z}ぜよ</td><td class="j">${j}じろ</td></tr>
  </table>`;
}

/* ---------- tabs ---------- */
const TABS=[["list","🗺️ 分類"],["detail","📖 詳細"],["contrast","⚖️ 対照"],["quiz","🎯 クイズ"]];
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
  let h=`<div class="card intro"><h2>一つの動詞、二つの着こなし</h2>
  <p>漢語名詞を述語にするための最小装置「する」。その「す」が濁って
  <b>ずる</b> になり、さらに上一段化して <b>じる</b> になる——という
  三階建ての系譜を、できるだけ多くの語で見渡す図鑑です。各カードに
  <b>ずる形／じる形の発音・活用対照・原声</b> が付きます。</p>
  <div class="steps">
    <div><b>STEP 1 名詞＋する</b><br>論（ろん）＋する。漢語を述語にする最小装置。</div>
    <div><b>STEP 2 連濁で「ずる」</b><br>撥音・長音のあとでスが濁り、論ずる・信ずるに。サ行変格活用。</div>
    <div><b>STEP 3 上一段化で「じる」</b><br>論じる・信じるに。規則的な上一段活用として現代口語の主役に。</div>
  </div>
  <p style="margin-top:10px;font-size:12.5px;color:var(--sub)">※「愛する・発する・達する」のように「する」のままの語も多い——
  「ずる組」は歴史的に固定された語彙の集合で、撥音・長音はあくまで相性の良い条件です。</p>
  </div>`;
  h+=GROUPS.map(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return "";
    return `<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.emoji||""} ${g.name}</h3><span>${members.length} 語</span></div>
    ${g.note?`<div class="grp-note">${g.note}</div>`:""}
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="em">${n.emoji||"📌"}</div>
        <div class="nm">${n.word} <small style="color:${g.color};font-size:10.5px">${n.level}</small></div>
        <div class="kn">${n.base}</div>
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
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.emoji||""} ${g.name}</h3>`;
    members.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji||"📌"} ${n.word}<span class="jl">${n.level}</span></h2>
        <div class="meta"><span class="chip reg">${n.register}</span>
          <code>語幹 ${n.base}</code>
          ${AUDIO[iid+'-z']?`<button class="btn mini-btn" title="ずる形" onclick="play('${iid}-z',this)">▶</button><code>ずる形</code>`:""}
          ${AUDIO[iid+'-j']?`<button class="btn mini-btn" title="じる形" onclick="play('${iid}-j',this)">▶</button><code>じる形</code>`:""}
        </div>
        <div class="meanbox">📌 <b>意思</b>　${n.meaning}</div>
        <div class="origin">🌀 <b>系譜</b>　${n.chain}</div>
        ${conjTable(n)}
        <h3 class="sec">📝 例句</h3>
        ${(n.examples||[]).map((_,i)=>rowHTML(`${iid}-e${i}`)).join("")}
        ${(n.nadeshiko&&n.nadeshiko.length)?`<h3 class="sec">🎬 原声台词</h3>`:""}
        ${(n.nadeshiko||[]).map((sc,i)=>nadeHTML(sc,iid,i)).join("")}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- contrast ---------- */
function renderContrast(){
  $("#main").innerHTML=`<div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">三階建ての系譜</h2>
    <div class="flow">
      <div class="st"><b>① 名詞＋す（する）</b><br>
        論（ろん）＋す。漢語名詞を述語に変える最小装置。<span class="arrow">↓</span></div>
      <div class="st"><b>② 連濁で「ず」——論ずる</b><br>
        「ん」や長音のあとでスが濁り、論ず・信ずの形に。<br>
        文語サ行変格として、漢文訓読調・明治の学術文体・演説の「格調」を担った。
        <span class="arrow">↓</span></div>
      <div class="st"><b>③ 上一段化で「じる」——論じる</b><br>
        言文一致以降、ザ行上一段活用として規則的に整理。現代口語では
        じる形が日常の主役になり、ずる形は書き言葉・演説に残った。</div>
    </div></div>

    <div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">活用対照（論ずる／論じる）</h2>
    <table class="cmp">
      <tr><th>活用形</th><th class="tagz">サ行変格・ずる</th><th class="tagj">上一段・じる</th></tr>
      <tr><td>未然形＋ない</td><td>論ぜない</td><td>論じない</td></tr>
      <tr><td>未然形＋ず</td><td>論ぜず</td><td>論じず（古）</td></tr>
      <tr><td>連用形＋ます</td><td>論じます</td><td>論じます</td></tr>
      <tr><td>終止形</td><td>論ずる</td><td>論じる</td></tr>
      <tr><td>仮定形＋ば</td><td>論ずれば</td><td>論じれば</td></tr>
      <tr><td>命令形</td><td>論ぜよ</td><td>論じろ</td></tr>
      <tr><td>意志・勧誘（〜う）</td><td>論じよう</td><td>論じよう</td></tr>
    </table>
    <div class="hint">未然形（ぜ／じ）・仮定形（ずれ／じれ）・命令形（ぜよ／じろ）が両者の分かれ目。
    連用形と終止形以外はかなり形が違うので、試験でも活用形が問われやすい。</div>
    </div>

    <div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">語感の対照</h2>
    <div class="good"><b>ずる形＝かたい書き言葉の装い</b><br>
      漢文訓読の名残・格調・演説・詔勅・報道の文体。
      「信ずる者は救われる」「断じて〜」「〜ぜよ」など、いまも生きる場面がある。</div>
    <div class="warn"><b>じる形＝現代口語の標準</b><br>
      上一段活用として規則的で、会話・新聞・ビジネス文書の主役。
      「感じる」「信じる」「通じる」は日常ほぼこの形。</div>
    <div class="hint">ただし語によって残り方が違う：<br>
      ・<b>じる優位</b>：感じる・信じる・通じる・命じる・演じる<br>
      ・<b>両形現役</b>：論ずる／論じる・禁ずる／禁じる・重んずる／重んじる<br>
      ・<b>ずるが古風</b>：案ずる・変ずる・殉ずる・献ずる<br>
      ・<b>じる自体が文章語</b>：長じる・疎んじる・肯んじる・詠じる・映じる</div>
    </div>

    <div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">「する」のままの組 vs「ずる」組</h2>
    <p style="font-size:13.5px;line-height:1.75">すべての漢語がずる／じるになるわけではない。
    撥音・長音で終わっても「する」のままの語は多く、ずる組は歴史的に固定された集合。</p>
    <div class="warn"><b>する固守組（ずる化しない）</b><br>
      愛する・発する・達する・接する・適する・察する・訳する・略する・会する・
      称する・属する・面する・存する——これらは「〜ずる」とは言わない。</div>
    <div class="good"><b>和語＋「ん」＋ずる の型</b><br>
      形容詞・和語に撥音「ん」を挿入して作る：重し→<b>重んずる</b>、軽し→<b>軽んずる</b>、
      疎し→<b>疎んずる</b>、甘し→<b>甘んずる</b>、肯ぬ→<b>肯んずる</b>、安し→<b>安んずる</b>。</div>
    <div class="hint">注意ペア：<br>
      ・<b>混じる</b>は日常「まじる」（五段）。漢語「こんじる」は文章語。<br>
      ・<b>存ずる／存じる</b>は謙譲語（思う・知る）。<br>
      ・<b>高ずる／高じる</b>には同系の<b>昂ずる／昂じる</b>（感情が昂じる）がある。<br>
      ・同じ「じる」でも<b>準じる・殉じる・順じる</b>は字も意味も別の語。</div>
    </div>`;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("zuru-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("zuru-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("zuru-wrong",w);}
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
      <div class="hint" style="margin-bottom:18px">题目由 zuru.json 自动生成 · 词条越多题库越大 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨词条交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('zuru-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
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
  const msg=pct===100?"🏆 完璧！系譜がまた一本つながった！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 詳細タブで復習してから再挑戦";
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
  else if(tab==="contrast")renderContrast();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    groups, items = load_pocket()
    n_nade = sum(len(it.get("nadeshiko", [])) for it in items)

    # display 副本：例句＋Nadeshiko 全部 pykakasi 振假名（只作用展示，不动素文/出题/音频）
    display = copy.deepcopy(items)
    sents = {}
    for it in display:
        for i, ex in enumerate(it.get("examples") or []):
            ex["jp"] = add_furigana(ex["jp"])
            sents[f"{it['id']}-e{i}"] = {"jp": ex["jp"], "cn": ex.get("cn", ""), "src": ex.get("src", "")}
        for sc in it.get("nadeshiko") or []:
            sc["jp"] = add_furigana(sc["jp"])

    audio = gen_audio(items)
    print(f"[1/4] audio: {len(audio)} clips ready")

    print("[2/4] nadeshiko: downloading CDN audio...")
    nade_audio = gen_nade_audio(items)

    qs = build_questions(groups, items, audio)

    tags = (f"<span>{len(items)} 語のずる／じるペア</span>"
            f"<span>{len(groups)} 分類</span>"
            f"<span>{len(audio)} 音声{' + ' + str(n_nade) + ' 原声' if n_nade else ''}</span>"
            f"<span>zuru.json 随手加</span>")
    banks_meta = [[k, l] for k, l in BANK_META]

    print("[3/4] generating quiz banks...")
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, l in BANK_META:
        print(f"      {l}: {counts[k]} 問")

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
