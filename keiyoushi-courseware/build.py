#!/usr/bin/env python3
"""Build the self-contained 「動詞系形容詞」courseware HTML.

用法：往 keiyoushi.json 里随手加一条「動詞系形容詞」→ 运行本脚本 →
index.html 自动长出卡片、读音、TTS、Nadeshiko 场景与题库。

音频：例句与词头用 edge-tts（ja-JP-Nanami）合成，按文本哈希缓存到
audio/；Nadeshiko 原声从 CDN 下载缓存到 nade_audio/；失败只跳过该条。
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
POCKET = ROOT / "keiyoushi.json"
sys.path.insert(0, str(ROOT.parent))
from tools.tts_kana import sanitize_for_edge_tts
AUDIO_DIR = ROOT / "audio"
NADE_CACHE = ROOT / "nade_audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20261112
MIN_MP3 = 300
BANK_META = [
    ["path",    "🧭 成り立ち判定"],
    ["origin",  "🎞️ 原動詞・原義"],
    ["meaning", "📌 意味選択"],
    ["fill",    "✍️ 穴埋め"],
    ["listen",  "🎧 聴解判別"],
    ["judge",   "⭕ 正誤判定"],
    ["custom",  "⚔️ 類義辨析"],
]
PATH_OPTS = [
    ("rentai", "連用形＋た（連体詞）"),
    ("nai", "動詞＋〜ない（形容詞）"),
    ("kogo", "古語・形容詞"),
    ("idiom", "連語・俗語（連体詞的）"),
]
PATH_INDEX = {k: i for i, (k, _) in enumerate(PATH_OPTS)}


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- load & validate

def load_pocket():
    try:
        data = json.loads(POCKET.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[!] keiyoushi.json 不是合法 JSON：{e}")
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
        for key in ("name", "color", "pathLabel"):
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
        for key in ("word", "read", "surface", "status", "origin", "originShort",
                    "physical", "meaning", "label", "engine", "level", "emoji"):
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
        for n, q in enumerate(it.get("judge") or []):
            if not q.get("q") or not isinstance(q.get("ans"), bool):
                errors.append(f"「{label}」judge#{n+1} 需要 q 和布尔 ans")
        for n, q in enumerate(it.get("quizzes") or []):
            qt = q.get("type")
            if qt not in ("choice", "judge", "listen"):
                errors.append(f"「{label}」自作题#{n+1} type 必须是 choice/judge/listen")
            elif qt != "judge":
                if not q.get("opts") or not isinstance(q.get("ans"), int) \
                        or not 0 <= q["ans"] < len(q["opts"]):
                    errors.append(f"「{label}」自作题#{n+1} 需要 opts 和范围内的 ans")

    if errors:
        print("[!] keiyoushi.json 有问题，先修好再构建：")
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

_READING_OVERRIDES = [
    # 見出し語・成句
    ("初めて", "はじめて"),
    ("改めて", "あらためて"),
    ("続いて", "つづいて"),
    ("追って", "おって"),
    ("併せて", "あわせて"),
    ("極めて", "きわめて"),
    ("努めて", "つとめて"),
    ("進んで", "すすんで"),
    ("強いて", "しいて"),
    ("思い切って", "おもいきって"),
    ("決して", "けっして"),
    ("果たして", "はたして"),
    ("全て", "すべて"),
    ("割合", "わりあい"),
    ("言わば", "いわば"),
    ("思わず", "おもわず"),
    ("各々", "おのおの"),
    ("現に", "げんに"),
    ("別に", "べつに"),
    # 例句の読み間違い防止
    ("大人", "おとな"),
    ("今日は", "きょうは"),
    ("今年", "ことし"),
    ("今いる", "いまいる"),
    ("この間", "このあいだ"),
    ("その年", "そのとし"),
    ("彼の", "かれの"),
    ("君だろう", "きみだろう"),
    ("君には", "きみには"),
    ("君は", "きみは"),
    ("悪あがき", "わるあがき"),
    ("愛して", "あいして"),
    ("行いたく", "いきたく"),
    ("健康", "けんこう"),
    ("人は", "ひとは"),
    ("すべての人に", "すべてのひとに"),
    ("15世紀の人", "じゅうごせいきのひと"),
    ("恩", "おん"),
    ("遺憾", "いかん"),
    ("担当者", "たんとうしゃ"),
    ("詳細", "しょうさい"),
    ("行方", "ゆくえ"),
    ("会議", "かいぎ"),
    ("参加者", "さんかしゃ"),
    ("3校合わせて", "さんこうあわせて"),
    ("1万円", "いちまんえん"),
    ("9千円", "きゅうせんえん"),
    ("市場", "しじょう"),
    ("表情", "ひょうじょう"),
    ("魚", "さかな"),
    ("故郷", "こきょう"),
    ("責任", "せきにん"),
    ("電車", "でんしゃ"),
    ("電話", "でんわ"),
    ("足", "あし"),
    ("姿", "すがた"),
    ("本当", "ほんとう"),
    ("意図", "いと"),
    ("孫", "まご"),
    ("調査", "ちょうさ"),
    ("議題", "ぎだい"),
    ("選手", "せんしゅ"),
    ("登場", "とうじょう"),
    ("発表", "はっぴょう"),
    ("結果", "けっか"),
    ("感謝", "かんしゃ"),
    ("祈る", "いのる"),
    ("記念", "きねん"),
    ("開催", "かいさい"),
    ("不明", "ふめい"),
    ("問題", "もんだい"),
    ("解決", "かいけつ"),
    ("子ども", "こども"),
    ("無理", "むり"),
    ("困難", "こんなん"),
    ("中国語", "ちゅうごくご"),
    ("薬", "くすり"),
    ("害", "がい"),
    ("説明", "せつめい"),
    ("混乱", "こんらん"),
    ("静か", "しずか"),
    ("声", "こえ"),
    ("危険", "きけん"),
    ("冒す", "おかす"),
    ("態度", "たいど"),
    ("借金", "しゃっきん"),
    ("避ける", "さける"),
    ("冷静", "れいせい"),
    ("振る舞う", "ふるまう"),
    ("笑顔", "えがお"),
    ("意見", "いけん"),
    ("述べる", "のべる"),
    ("仕事", "しごと"),
    ("引き受ける", "ひきうける"),
    ("頼む", "たのむ"),
    ("案", "あん"),
    ("髪", "かみ"),
    ("短く", "みじかく"),
    ("切る", "きる"),
    ("忘れる", "わすれる"),
    ("悪い", "わるい"),
    ("値段", "ねだん"),
    ("高い", "たかい"),
    ("思った", "おもった"),
    ("本物", "ほんもの"),
    ("会える", "あえる"),
    ("毎日", "まいにち"),
    ("遊ぶ", "あそぶ"),
    ("学校", "がっこう"),
    ("成績", "せいせき"),
    ("全員", "ぜんいん"),
    ("出席", "しゅっせき"),
    ("会場", "かいじょう"),
    ("変更", "へんこう"),
    ("必要", "ひつよう"),
    ("平和", "へいわ"),
    ("世界", "せかい"),
    ("尽くす", "つくす"),
    ("個人", "こじん"),
    ("社会", "しゃかい"),
    ("全体", "ぜんたい"),
    ("活力", "かつりょく"),
    ("倒産", "とうさん"),
    ("業界", "ぎょうかい"),
    ("信用", "しんよう"),
    ("揺るがす", "ゆるがす"),
    ("新入生", "しんにゅうせい"),
    ("礼儀正しい", "れいぎただしい"),
    ("地域", "ちいき"),
    ("作柄", "さくがら"),
    ("良好", "りょうこう"),
    ("失う", "うしなう"),
    ("失って", "しなって"),
    ("失った", "しなった"),
    ("神", "かみ"),
    ("意志", "いし"),
    ("平等", "びょうどう"),
    ("機会", "きかい"),
    ("与える", "あたえる"),
    ("終わる", "おわる"),
    ("失敗", "しっぱい"),
    ("信じる", "しんじる"),
    ("時計", "とけい"),
    ("喜び", "よろこび"),
    ("上げる", "あげる"),
    ("食べる", "たべる"),
    ("不安", "ふあん"),
    ("安い", "やすい"),
    ("買う", "かう"),
    ("甘い", "あまい"),
    ("手", "て"),
    ("伸びる", "のびる"),
    ("騒がしい", "さわがしい"),
    ("大きい", "おおきい"),
    ("若い", "わかい"),
    ("勉強", "べんきょう"),
    ("暖かい", "あたたかい"),
    ("今夏", "こんか"),
    ("暑い", "あつい"),
    ("鋭い", "するどい"),
    ("音楽", "おんがく"),
    ("愛する", "あいする"),
    ("名前", "なまえ"),
    ("存じ上げる", "ぞんじあげる"),
    ("伺う", "うかがう"),
    ("地球", "ちきゅう"),
    ("自転", "じてん"),
    ("新しい", "あたらしい"),
    ("変化", "へんか"),
    ("対応", "たいおう"),
    ("感じる", "かんじる"),
    ("嫌", "いや"),
    ("考える", "かんがえる"),
    ("最近", "さいきん"),
    ("雨", "あめ"),
    ("激しい", "はげしい"),
    ("存在", "そんざい"),
    ("思い出", "おもいで"),
    ("浮かぶ", "うかぶ"),
    ("場面", "ばめん"),
    ("目", "め"),
    ("驚いた", "おどろいた"),
    ("顔", "かお"),
    ("想像", "そうぞう"),
    ("生き生き", "いきいき"),
    ("子どもたち", "こどもたち"),
    ("絵", "え"),
    ("動き", "うごき"),
    ("描く", "えがく"),
    ("恐らく", "おそらく"),
    ("不可能", "ふかのう"),
    ("間違い", "まちがい"),
    ("救世主", "きゅうせいしゅ"),
    ("人生", "じんせい"),
    ("縮図", "しゅくず"),
    ("第二", "だいに"),
    ("涙", "なみだ"),
    ("出る", "でる"),
    ("本音", "ほんね"),
    ("漏らす", "もらす"),
    ("笑う", "わらう"),
    ("母", "はは"),
    ("料理", "りょうり"),
    ("一番", "いちばん"),
    ("努力", "どりょく"),
    ("才能", "さいのう"),
    ("敵う", "かなう"),
    ("試合", "しあい"),
    ("中止", "ちゅうし"),
    ("不在", "ふざい"),
    ("手持ち", "てもち"),
    ("足りる", "たりる"),
    ("困る", "こまる"),
    ("楽しい", "たのしい"),
    ("道", "みち"),
    ("判断", "はんだん"),
    ("経験", "けいけん"),
    ("言った", "いった"),
    ("見る", "みる"),
    ("行きたい", "いきたい"),
    ("変わった", "かわった"),
    ("話", "はなし"),
    ("部屋代", "へやだい"),
    ("食費", "しょくひ"),
    ("払う", "はらう"),
    ("悪口", "わるぐち"),
    ("中", "なか"),
    ("踏む", "ふむ"),
    ("負ける", "まける"),
    ("彼女", "かのじょ"),
    ("方", "ほう"),
    ("うわさ", "うわさ"),
    ("聞く", "きく"),
    ("聞こう", "きこう"),
    ("見える", "みえる"),
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
    tasks = []
    sanitized = 0
    for it in items:
        tasks.append((f"{it['id']}-w", it["read"]))
        for i, ex in enumerate(it.get("examples") or []):
            raw = ex["jp"]
            tts_text = sanitize_for_edge_tts(raw)
            if tts_text != raw:
                sanitized += 1
            tasks.append((f"{it['id']}-e{i}", tts_text))
    todo = [(lid, txt) for lid, txt in tasks
            if not (cache_path(lid, txt).exists()
                    and cache_path(lid, txt).stat().st_size > MIN_MP3)]
    print(f"[1/4] audio: {len(tasks)} clips total, {len(tasks)-len(todo)} cached, "
          f"{len(todo)} to synthesize...")
    print(f"      tts kana: {sanitized} examples sanitized")
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
    failed = [lid for lid, ok in results.items() if not ok]
    if failed:
        print(f"[!] {len(failed)} 条发音合成失败（课件照常生成）：")
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
            audio[lid] = "data:audio/mpeg;base64," + base64.b64encode(
                p.read_bytes()).decode()
    return audio


# ---------------------------------------------------------------- nadeshiko audio

def download_nade_audio(url, logical_id):
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

def build_questions(groups, items, audio):
    rng = random.Random(SEED)
    by_group = {g["id"]: g for g in groups}

    def uniq_pick(own, field, n=3):
        cands = []
        for x in items:
            v = x.get(field)
            if x["id"] != own["id"] and v and v != own.get(field) and v not in cands:
                cands.append(v)
        rng.shuffle(cands)
        return cands[:n]

    def pick_surfaces(own, n=3):
        cands = []
        for x in items:
            v = x.get("surface")
            if x["id"] != own["id"] and v and v != own.get("surface") and v not in cands:
                cands.append(v)
        rng.shuffle(cands)
        return cands[:n]

    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref})
        qs.append(kw)

    for it in items:
        iid = it["id"]
        note = it.get("note", "")

        # 🧭 成り立ち判定
        add("path", f"{iid}:path", type="choice",
            q=f'「{it["word"]}」の成り立ちは？',
            opts=[label for _, label in PATH_OPTS],
            ans=PATH_INDEX[it["group"]],
            exp=f'<b>{by_group[it["group"]]["name"]}</b>。<br>'
                f'🎞️ {it["origin"]}<br>🧭 {it["engine"]}')

        # 🎞️ 原動詞・原義
        dist = uniq_pick(it, "originShort", 3)
        if len(dist) >= 3:
            add("origin", f"{iid}:origin", type="choice",
                q=f'「{it["word"]}」の原動詞（または語源）として最も近いものは？',
                opts=[it["originShort"]] + dist, ans=0,
                exp=f'🎞️ <b>動作の残像</b>：{it["physical"]}<br>'
                    f'📌 <b>抽象ラベル</b>：{it["meaning"]}<br>'
                    f'🧭 {it["engine"]}')

        # 📌 意味選択
        dist = uniq_pick(it, "label", 3)
        if len(dist) >= 3:
            add("meaning", f"{iid}:meaning", type="choice",
                q=f'「{it["word"]}」の意味として最も近いものは？',
                opts=[it["label"]] + dist, ans=0,
                exp=f'📌 {it["meaning"]}<br>🎞️ {it["origin"]}<br>'
                    + (f'💡 {note}' if note else ''))

        # ✍️ 穴埋め（最初の2例まで）
        for i, ex in enumerate((it.get("examples") or [])[:2]):
            surf = it["surface"]
            if surf not in ex["jp"]:
                continue
            blanked = ex["jp"].replace(surf, "（　）", 1)
            dist = [s for s in pick_surfaces(it, 3) if s not in blanked]
            if len(dist) < 3:
                continue
            add("fill", f"{iid}:fill-{i}", type="choice",
                q=f'{blanked}<br><span class="hint">（　）里填回哪个词？</span>',
                opts=[surf] + dist, ans=0,
                exp=f'完整句子：{ex["jp"]}<br>{ex["cn"]}<br>'
                    f'📌 {it["word"]}：{it["meaning"]}')

        # 🎧 聴解判別
        if f"{iid}-e0" in audio:
            ex0 = it["examples"][0]
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 听音频：句子里用了哪个动词系副词？",
                opts=[it["word"]] + uniq_pick(it, "word", 3), ans=0,
                exp=f'原句：{ex0["jp"]}<br>{ex0["cn"]}<br>'
                    f'🎞️ {it["origin"]}'
                    + (f'<br>💡 {note}' if note else ''))

        # ⭕ 正誤判定
        for n, cq in enumerate(it.get("judge") or []):
            add("judge", f"{iid}:judge-{n}", type="judge",
                q=cq["q"], ans=cq["ans"], exp=cq.get("exp", ""))

        # ⚔️ 自作題
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
<title>動詞系形容詞 ・ 活用形が形容詞になるとき</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#7c3aed;--acc2:#f5f3ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#3b0764,#7c3aed);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:900px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:7px;margin:-18px 0 16px;position:relative;z-index:2;flex-wrap:wrap}
nav button{flex:1;min-width:100px;border:none;border-radius:12px;padding:11px 2px;font-size:13.5px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:2.05;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.intro{background:linear-gradient(135deg,#f5f3ff,#faf8ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:#5b21b6}
.intro p{font-size:14px;line-height:1.8}
.intro b{color:#7c3aed}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.65;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#7c3aed}
.pathgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:9px;margin-top:12px}
.path{background:#fff;border-radius:13px;padding:11px 13px;border-top:4px solid var(--c,#be123c);
box-shadow:0 1px 6px rgba(30,40,90,.08);font-size:12.5px;line-height:1.65}
.path b{color:var(--c,#be123c);font-size:14px}
.path .ex{color:var(--sub);font-size:11.5px;margin-top:4px}
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-note{font-size:12px;color:var(--sub);margin:6px 2px 0;line-height:1.6}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#7c3aed)}
.mini .em{font-size:24px}
.mini .nm{font-weight:800;font-size:15px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.mini .md{font-size:10.5px;font-weight:700;color:var(--g,#7c3aed);margin-bottom:2px}
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#7c3aed)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.chip.path{background:#f5f3ff;color:#6d28d9}
.jlpt{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;
background:#eef2ff;color:#3730a3;white-space:nowrap}
.freq{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:800;
background:#f1f5f9;color:#475569;white-space:nowrap}
.freq-S{background:#fef3c7;color:#92400e}
.freq-A{background:#dcfce7;color:#166534}
.freq-B{background:#dbeafe;color:#1d4ed8}
.freq-C{background:#f3e8ff;color:#6b21a8}
.freq-D{background:#f1f5f9;color:#64748b}
.dict{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;background:#f5f3ff;color:#6d28d9;white-space:nowrap}
.drift{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;background:#fff7ed;color:#b45309;white-space:nowrap}
.mini .badges{display:flex;gap:4px;align-items:center;margin:4px 0 2px;flex-wrap:wrap}
.status{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700}
.status.s0{background:#e9f7ef;color:#188a52}
.status.s1{background:#fff7ed;color:#b45309}
.status.s2{background:#f1f5f9;color:#475569}
.mini-btn{width:26px;height:26px;font-size:12px}
.meanbox{background:var(--g-bg,#fff1f2);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.75;margin:10px 0}
.origin{background:#fffaf0;border-left:4px solid var(--gold);border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.75;margin:8px 0}
.chain{background:#f8fafc;border-left:4px solid #64748b;border-radius:10px;padding:10px 13px;
font-size:13.5px;line-height:1.75;margin:8px 0}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
.pitfall{background:#fff7ed;border-left:4px solid #f59e0b;border-radius:10px;padding:10px 13px;
font-size:13px;line-height:1.7;margin:10px 0}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* etymology */
.etym{background:linear-gradient(135deg,#f5f3ff,#fff);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.etym h2{font-size:17px;margin-bottom:8px;color:#5b21b6}
.etym p{font-size:13.5px;line-height:1.8}
.flow{display:flex;flex-direction:column;gap:7px;margin:10px 0}
.flow .st{border-radius:12px;padding:10px 14px;font-size:13.5px;line-height:1.75;background:#fff;border-left:5px solid #7c3aed}
.flow .st b{color:#7c3aed}
table.cmp{width:100%;border-collapse:collapse;font-size:12.8px;margin:8px 0}
table.cmp th,table.cmp td{border:1px solid var(--line);padding:7px 9px;line-height:1.65;vertical-align:top}
table.cmp th{background:#f5f3ff;color:#5b21b6}
table.cmp td:first-child{white-space:nowrap;background:#fffdfd;font-weight:700;color:var(--sub)}
.tagz{color:#b45309;font-weight:700}.tagj{color:#0f766e;font-weight:700}
.warn{background:#fff7ed;border-left:4px solid #f59e0b;border-radius:10px;padding:10px 13px;font-size:13.5px;line-height:1.75;margin:8px 0}
.good{background:#f0fdf4;border-left:4px solid #22c55e;border-radius:10px;padding:10px 13px;font-size:13.5px;line-height:1.75;margin:8px 0}
.kaisetsu{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif;font-size:15px;line-height:2.1;
background:#fffdf8;border:1px dashed #f0c9a0;border-radius:10px;padding:12px 14px;margin:8px 0}
.kaisetsu .lbl{font-size:11.5px;font-weight:700;color:#6d28d9;background:#f5f3ff;border-radius:6px;padding:1px 7px;margin-right:6px}
.verdict{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:9px;margin:8px 0}
.verdict div{background:#fff;border:1px solid var(--line);border-radius:11px;padding:9px 12px;font-size:12.5px;line-height:1.7}
.verdict b{color:#be123c}
.verdict .ok{color:#188a52;font-weight:700}.verdict .no{color:#d33f49;font-weight:700}
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
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;
margin-left:6px;vertical-align:1px}
.src-moji{background:var(--okbg);color:var(--ok)}
.src-self{background:#eef2f7;color:#64748b}
.src-nade{background:#ede7f6;color:#5e35b1}
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
@media(max-width:640px){nav button{min-width:86px;font-size:12px}.jp{font-size:15.5px}}
</style>
</head>
<body>
<header><div class="wrap">
<h1>動詞系形容詞 ・ 活用形が形容詞になるとき</h1>
<div class="kana">どうしけいけいようし ／ 動詞の活用形が形容詞になるとき 🧥</div>
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
  const chip=s.src==="moji"?`<span class="src src-moji">MOJi</span>`:
             s.src==="self"?`<span class="src src-self">作例</span>`:"";
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
const TABS=[["list","🗺️ 体系"],["etym","📜 語源"],["detail","📖 詳細"],["contrast","⚖️ 対照"],["freq","📊 頻度"],["quiz","🎯 クイズ"]];
let tab="list";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}
function shortMean(m){return m.split(/[：:；;，,（(]/)[0];}
function statusClass(s){return s==="確実"?"s0":s==="通説"?"s1":"s2";}
function groupOf(id){return GROUPS.find(g=>g.id===id)||GROUPS[0];}

function renderList(){
  let h=`<div class="card intro"><h2>動詞系形容詞とは——活用形が形容詞になるとき</h2>
  <p>「改まる」は「新しくなる」、「澄ます」は「濁りを取る」。ところが「改まった席」「澄ました顔」は、もう動作の意味ではない。
  動詞の活用形が一語化し、辞書が<b>連体詞・形容詞</b>として認め、元動詞から意味を直接導けなくなった語を集める。</p>
  <div class="steps">
    <div><b>① 形態</b><br>改まった＝改まる＋た／澄ました＝澄ます＋た。動詞の活用形が残る。</div>
    <div><b>② 辞書</b><br>辞書が見出しを連体詞・形容詞として立てているか。</div>
    <div><b>③ 漂白</b><br>元動詞から意味を直接導けないか。導けるなら生産的パターン。</div>
  </div></div>`;
  h+=`<div class="card"><h2 style="font-size:17px;margin-bottom:6px">四つの出口</h2>
  <div class="pathgrid">
    <div class="path" style="--c:#7c3aed"><b>① 連用形＋た</b><br>改まった・澄ました・かしこまった・くだけた<br>
      <span class="ex">結果状態が連体詞化。核心11語。</span></div>
    <div class="path" style="--c:#0f766e"><b>② 動詞＋ない</b><br>思いがけない・たまらない・くだらない・心許ない<br>
      <span class="ex">否定の慣用化。核心4語。</span></div>
    <div class="path" style="--c:#b45309"><b>③ 古語・複合</b><br>ありがたい・めでたい・見苦しい・見窄らしい<br>
      <span class="ex">古語形容詞・複合。核心4語。</span></div>
    <div class="path" style="--c:#475569"><b>④ 連語・俗語</b><br>図に乗った・間が抜けた・参った・ぶっ飛んだ<br>
      <span class="ex">連体詞的用法。周辺11語。</span></div>
  </div>
  <div class="warn" style="margin-top:12px"><b>核心19語＋周辺11語：</b>核心は辞書が<b>連体詞・形容詞</b>と明記する語。
  周辺は連語・感動詞・俗語で、連体詞的に使う語——同じ棚に混ぜず、別グループで検品する。</div>
  </div>`;
  h+=GROUPS.map(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return "";
    return `<div class="grp"><div class="grp-h" style="background:${g.color}"><h3>${g.emoji||""} ${g.name}</h3><span>${members.length} 語</span></div>
    ${g.note?`<div class="grp-note">${g.note}</div>`:""}
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" style="--g:${g.color}" onclick="goDetail('${n.id}')">
        <div class="em">${n.emoji||"📌"}</div>
        <div class="nm">${n.word}</div>
        <div class="badges"><span class="jlpt">JLPT ${n.jlpt||n.level}</span><span class="freq f-${n.freq.tier}">📊 ${n.freq.tier}${n.freq.note?"*":""}</span><span class="dict">${n.dict||""}</span></div>
        <div class="md">${g.pathLabel}</div>
        <div class="im">${shortMean(n.meaning)}</div></button>`).join("")}</div></div>`;
  }).join("");
  $("#main").innerHTML=h;
}

function renderEtymology(){
  $("#main").innerHTML=`<div class="etym"><h2>📜 語源の地図——動詞はどうやって形容詞になるか</h2>
  <p>動詞の活用形がそのまま形容詞として辞書に載るには、<b>形態の固定 → 意味の漂白 → 辞書認定</b>の三段階を踏む。
  「改まる」は「新しくなる」だが、「改まった席」は「正式な席」——動作の画面は残るのに、意味は直接導けない。</p>
  <div class="flow">
    <div class="st"><b>① 形態の固定</b>　改まる→改まった／畏まる→かしこまった。連用形＋たが一語化。</div>
    <div class="st"><b>② 意味の漂白</b>　「改められた」→「正式な」／「砕けた」→「くだけた」。物理画面から評価へ。</div>
    <div class="st"><b>③ 辞書認定</b>　辞書が連体詞・形容詞として見出しを立てる。ここで「動詞の活用形」から「形容詞」へ。</div>
  </div>
  <div class="warn">核心19語は辞書が<b>連体詞・形容詞</b>と明記する語。周辺11語は<b>連語・俗語</b>で、連体詞的に使う語——同じ棚に混ぜず、別グループで検品する。</div>
  </div>

  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">四つの出口——判定表</h2>
  <table class="cmp">
    <tr><th>出口</th><th>形</th><th>核心</th><th>代表</th><th>辞書認定</th></tr>
    <tr><td>① 連用形＋た</td><td>改まった・澄ました</td><td>結果状態の連体詞化</td><td>改まった・落ち着いた・澄ました・かしこまった・くだけた・しゃれた・凝った・ひねった・呆れた・とんだ・決まった</td><td>連体詞</td></tr>
    <tr><td>② 動詞＋ない</td><td>思いがけない・たまらない</td><td>否定の慣用化</td><td>思いがけない・たまらない・くだらない・心許ない</td><td>形容詞</td></tr>
    <tr><td>③ 古語・複合</td><td>ありがたい・見窄らしい</td><td>古語形容詞・複合</td><td>ありがたい・めでたい・見苦しい・見窄らしい</td><td>形容詞</td></tr>
    <tr><td>④ 連語・俗語</td><td>図に乗った・やられた</td><td>連体詞的用法（周辺）</td><td>いかした・いかれた・やられた・図に乗った・間が抜けた・気が抜けた・調子に乗った・恐れ入った・参った・たまげた・ぶっ飛んだ</td><td>連語・俗語</td></tr>
  </table>
  <div class="hint">①〜③が核心19語。④は周辺11語で、辞書見出しが連語・感動詞・俗語の場合がある。</div>
  </div>

  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">語源検品——意味の漂白</h2>
  <div class="verdict">
    <div><b>改まった</b>　改まる（新しくなる）→ 正式な。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>澄ました</b>　澄ます（濁りを取る）→ 若无其事的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>かしこまった</b>　畏まる（恐れつつしむ）→ 拘谨的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>くだけた</b>　砕ける（固いものが崩れる）→ 轻松的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>しゃれた</b>　洒落る（身なりを飾る）→ 时髦的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>凝った</b>　凝る（こだわる）→ 精致的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>ひねった</b>　捻る（ねじる）→ 别出心裁的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>呆れた</b>　呆れる（あっけにとられる）→ 令人无语的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>とんだ</b>　飛ぶ（空中を飛ぶ）→ 意外的。<span class="ok">連体詞・漂白 高</span></div>
    <div><b>思いがけない</b>　思い掛ける（思い当たる）→ 意想不到的。<span class="ok">形容詞・漂白 高</span></div>
    <div><b>たまらない</b>　堪る（こらえる）→ 受不了的。<span class="ok">形容詞・漂白 高</span></div>
    <div><b>くだらない</b>　下る（下に降りる）→ 无聊的。<span class="ok">形容詞・漂白 高</span></div>
    <div><b>ありがたい</b>　有り＋難し（有ることが難しい）→ 值得感激的。<span class="ok">形容詞・漂白 高</span></div>
    <div><b>めでたい</b>　愛でる＋たし → 可喜可贺的。<span class="ok">形容詞・漂白 高</span></div>
    <div><b>見窄らしい</b>　見る＋窄らし → 寒酸的。<span class="ok">形容詞・漂白 高</span></div>
  </div>
  </div>

  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">⚠️ Nadeshiko 誤命中検品</h2>
  <p style="font-size:13.5px;line-height:1.8">Nadeshiko は同形の動詞用法・受身・慣用句を大量に拾う。形容詞の検品では、後続語と辞書認定まで見る。</p>
  <table class="cmp">
    <tr><th>検索語</th><th>誤命中の例</th><th>なぜ違う</th><th>形容詞の用例</th></tr>
    <tr><td>改まった</td><td>改まっちゃって</td><td>動詞「改まる」のて形</td><td>改まった席</td></tr>
    <tr><td>ひねった</td><td>足をひねった</td><td>動詞「捻る」の物理用法</td><td>ひねった問題</td></tr>
    <tr><td>いかれた</td><td>連れていかれた</td><td>「行く」の受身・可能</td><td>いかれた奴</td></tr>
    <tr><td>やられた</td><td>やられた！</td><td>受身「やられる」の間投詞</td><td>やられた顔</td></tr>
    <tr><td>図に乗った</td><td>図に乗るな</td><td>動詞句の命令形</td><td>図に乗った態度</td></tr>
    <tr><td>参った</td><td>参った 参った</td><td>間投詞「参った」</td><td>参った顔</td></tr>
  </table>
  <div class="good"><b>検品の型：</b>① 形態が動詞の活用形か。② 辞書が見出しを連体詞・形容詞にしているか。③ 元動詞から意味を直接導けるか（導けるなら生産的パターン）。④ 連体修飾で名詞を修飾するか。</div>
  </div>

  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">判定四問</h2>
  <div class="flow">
    <div class="st"><b>Q1 形態は動詞の活用形か？</b><br>改まった＝改まる＋た、澄ました＝澄ます＋た。単なる形容詞ではなく、動詞の活用形が残っている。</div>
    <div class="st"><b>Q2 辞書は形容詞・連体詞と認定しているか？</b><br>「改まった」「澄ました」は連体詞、「思いがけない」「たまらない」は形容詞。連語・俗語は周辺グループ。</div>
    <div class="st"><b>Q3 元動詞から意味を直接導けるか？</b><br>「読みやすい」「見にくい」は導ける（生産的）。「改まった席」「澄ました顔」は導けない（慣用化）。</div>
    <div class="st"><b>Q4 連体修飾で名詞を修飾するか？</b><br>改まった席／澄ました顔／とんだ誤解／くだらない話。連体修飾の位置で形容詞として働く。</div>
  </div>
  </div>`;
}

function renderDetail(){
  let h=`<div class="card" style="padding:11px 16px;font-size:12.5px;color:var(--sub)">
    <span class="src src-moji">MOJi</span> 辞書例句　<span class="src src-self">作例</span> 本课件作例　<span class="src src-nade">Nadeshiko</span> 真实台词
    <span style="margin-left:10px"><span class="freq f-S">📊 S</span> 頻度（Nadeshiko hit）　<span class="jlpt">JLPT N1</span> 目安　<span class="dict">連体詞</span> 辞書認定　<span class="drift">漂白 高</span> 意味の漂白</span>
    <span style="margin-left:10px">· 点击 ▶ 播放；词头发音用平假名读音合成</span></div>`;
  GROUPS.forEach(g=>{
    const members=ITEMS.filter(n=>n.group===g.id);
    if(!members.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.emoji||""} ${g.name}</h3>`;
    members.forEach(n=>{
      const iid=n.id;
      h+=`<div class="card noun" id="n-${iid}" style="--g:${g.color};--g-bg:${g.color}14">
        <h2>${n.emoji||"📌"} ${n.word}<span class="jl">JLPT ${n.jlpt||n.level}</span></h2>
        <div class="meta"><span class="chip path">${g.pathLabel}</span>
          <span class="dict">${n.dict||"—"}</span>
          <span class="drift">漂白 ${n.drift||"—"}</span>
          <span class="status ${statusClass(n.status)}">語源 ${n.status}</span>
          <span class="freq f-${n.freq.tier}">📊 ${n.freq.tier} ${"★".repeat(n.freq.stars)}${n.freq.note?" *":""}</span>
          <button class="btn mini-btn" title="${n.read}" onclick="play('${iid}-w',this)">▶</button><code>${n.read}</code>
        </div>
        <div class="note" style="margin-top:4px">📊 <b>頻度</b>　Nadeshiko ${n.freq.nade.toLocaleString()} hit${n.freq.wf!=null?` / wordfreq ${n.freq.wf}`:""}${n.freq.note?` · ${n.freq.note}`:""}</div>
        <div class="origin">🎞️ <b>動作の残像（原動詞）</b>　${n.origin}<br>
          <span style="color:var(--sub)">物理画面：${n.physical}</span></div>
        <div class="meanbox">📌 <b>形容詞の機能</b>　${n.meaning}</div>
        <div class="chain">🧭 <b>派生チェーン</b>　${n.engine}</div>
        ${n.literal?`<div class="note" style="margin-top:4px">🔤 <b>直訳</b>　${n.literal}</div>`:""}
        <h3 class="sec">📝 例句</h3>
        ${(n.examples||[]).map((_,i)=>rowHTML(`${iid}-e${i}`)).join("")}
        ${(n.nadeshiko&&n.nadeshiko.length)?`<h3 class="sec">🎬 原声台词</h3>`:""}
        ${(n.nadeshiko||[]).map((sc,i)=>nadeHTML(sc,iid,i)).join("")}
        ${n.pitfall?`<div class="pitfall">⚠️ <b>誤命中メモ</b>　${n.pitfall}</div>`:""}
        ${n.contrast?`<div class="note">⚖️ <b>辨析</b>　${n.contrast}</div>`:""}
        ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
      </div>`;
    });
  });
  $("#main").innerHTML=h;
}

function renderFreq(){
  const sorted=[...ITEMS].sort((a,b)=>(b.freq.stars-a.freq.stars)||((b.freq?.nade||0)-(a.freq?.nade||0)));
  const rows=sorted.map((n,i)=>{
    const g=groupOf(n.group);
    return `<tr><td>${i+1}</td><td><b>${n.word}</b></td><td>${n.jlpt||n.level}</td>
      <td><span class="freq f-${n.freq.tier}">${n.freq.tier}${n.freq.note?"*":""} ${"★".repeat(n.freq.stars)}</span></td>
      <td>${n.freq.nade.toLocaleString()}</td><td>${n.freq.wf!=null?n.freq.wf:"—"}</td>
      <td>${n.dict||"—"}</td><td>${g.emoji||""} ${g.pathLabel}</td></tr>`;
  }).join("");
  $("#main").innerHTML=`<div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">📊 頻度マップ — Nadeshiko 台詞ヒット順</h2>
    <p style="font-size:13.5px;line-height:1.8">頻度は <b>Nadeshiko 全庫の表層一致ヒット数</b>。
    同形の動詞用法（改まって・足をひねった・連れていかれた・やられた）を含むため、形容詞用法の頻度としては参考値。
    S≥2000 / A≥700 / B≥200 / C≥50 / D&lt;50。<b>wordfreq</b> は見出し形が収録されている場合だけ zipf を表示（未収録は「—」）。</p>
    <div class="hint" style="margin-bottom:8px">JLPT は目安。頻度と JLPT は別物——N1 でも台詞でよく出る語、N3 でも書き言葉だけの語がある。</div>
    <table class="cmp">
      <tr><th>#</th><th>語</th><th>JLPT</th><th>頻度</th><th>Nadeshiko hit</th><th>wordfreq</th><th>辞書</th><th>分類</th></tr>
      ${rows}
    </table>
    <div class="warn"><b>読み方：</b>S/A は「まず覚える」、B は「読解で見たら止まる」、C/D は「語源と例文で拾う」。<br><b>*</b> は同形の動詞用法を含む参考値（改まった・ひねった・いかれた・やられた・図に乗った など）。</div>
  </div>`;
}

function renderContrast(){
  const all=GROUPS.map(g=>{
    const rows=ITEMS.filter(n=>n.group===g.id).map(n=>
      `<tr><td>${n.word}</td><td>${n.jlpt||n.level}</td><td><span class="freq f-${n.freq.tier}">${n.freq.tier}</span></td><td>${n.dict||"—"}</td><td>${n.originShort}</td><td>${n.label}</td><td>${n.status}</td></tr>`).join("");
    return `<h3 class="sec" style="color:${g.color}">${g.emoji} ${g.name}</h3>
      <table class="cmp"><tr><th>語</th><th>JLPT</th><th>頻度</th><th>辞書</th><th>原動詞・語源</th><th>形容詞ラベル</th><th>語源信頼度</th></tr>${rows}</table>`;
  }).join("");
  $("#main").innerHTML=`<div class="card">
    <h2 style="font-size:17px;margin-bottom:6px">⚖️ 類義・誤用の境界線</h2>
    <table class="cmp">
      <tr><th></th><th>语感 / 机制</th><th>典型句</th><th>误用点</th></tr>
      <tr><td>改まった</td><td>形式ばった・改めの結果</td><td>改まった席</td><td>かしこまったより事務的</td></tr>
      <tr><td>かしこまった</td><td>緊張して姿勢を正す</td><td>かしこまった態度</td><td>身体の緊張が強い</td></tr>
      <tr><td>落ち着いた</td><td>安定・沈静</td><td>落ち着いた雰囲気</td><td>「冷静」は判断の語</td></tr>
      <tr><td>澄ました</td><td>平然・とぼけ</td><td>澄ました顔</td><td>「平然」より表情寄り</td></tr>
      <tr><td>くだけた</td><td>固さが取れた</td><td>くだけた態度</td><td>「カジュアル」は外来語</td></tr>
      <tr><td>しゃれた</td><td>洗練・粋</td><td>しゃれた店</td><td>「おしゃれ」は装い寄り</td></tr>
      <tr><td>凝った</td><td>手が込んだ</td><td>凝った料理</td><td>「肩が凝った」は物理</td></tr>
      <tr><td>ひねった</td><td>ねじれた発想</td><td>ひねった問題</td><td>「足をひねった」は物理</td></tr>
      <tr><td>呆れた</td><td>あきれ返る</td><td>呆れた顔</td><td>「驚いた」は驚愕</td></tr>
      <tr><td>とんだ</td><td>予想外・糟糕</td><td>とんだ誤解</td><td>「思いがけない」は中立</td></tr>
      <tr><td>思いがけない</td><td>予期しない</td><td>思いがけない出会い</td><td>「とんだ」は評価が悪い</td></tr>
      <tr><td>たまらない</td><td>堪えられない</td><td>暑くてたまらない</td><td>「仕方ない」はやむを得ない</td></tr>
      <tr><td>くだらない</td><td>価値がない</td><td>くだらない話</td><td>「つまらない」は退屈</td></tr>
      <tr><td>ありがたい</td><td>感謝・貴重</td><td>ありがたい話</td><td>「めでたい」は祝い</td></tr>
      <tr><td>めでたい</td><td>祝い・吉</td><td>めでたい席</td><td>「おめでたい」は皮肉にも</td></tr>
      <tr><td>見苦しい</td><td>みっともない</td><td>見苦しい負け方</td><td>「見窄らしい」は貧弱</td></tr>
      <tr><td>見窄らしい</td><td>貧弱・寒酸</td><td>見窄らしい身なり</td><td>「見苦しい」は評価</td></tr>
      <tr><td>図に乗った</td><td>調子づく</td><td>図に乗った態度</td><td>「調子に乗った」とほぼ同義</td></tr>
      <tr><td>恐れ入った</td><td>感服・恐縮</td><td>恐れ入ったよ</td><td>「参った」は降参寄り</td></tr>
    </table>
  </div>
  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">核心と周辺の見分け</h2>
  <div class="warn"><b>核心19語：</b>辞書が連体詞・形容詞として見出しを立てる語。形態は動詞の活用形のままだが、意味は元動詞から直接導けない。<br>
  <b>周辺11語：</b>連語・感動詞・俗語で、連体詞的に使う語。辞書見出しが「図に乗る」「参る」などの動詞・連語である場合は、形容詞として断定しない。</div>
  <div class="good"><b>逆方向の注意：</b>「読みやすい」「見にくい」「わかりやすい」は生産的で、元動詞から意味を直接導ける。本课件の核心には入れず、必要なら「生産的パターン」として別扱いにする。</div>
  </div>
  <div class="card"><h2 style="font-size:17px;margin-bottom:6px">全一覧</h2>${all}</div>`;
}

const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("keiyoushi-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("keiyoushi-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("keiyoushi-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}

let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:420px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 keiyoushi.json 自动生成 · 词条越多题库越大 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:420px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨语源交错练习，记忆更牢固</span></button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('keiyoushi-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
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
  const msg=pct===100?"🏆 完璧！動作の残像がすっかり剥がれた！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 詳細タブで復習してから再挑戦";
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

function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="list")renderList();
  else if(tab==="etym")renderEtymology();
  else if(tab==="detail")renderDetail();
  else if(tab==="contrast")renderContrast();
  else if(tab==="freq")renderFreq();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    groups, items = load_pocket()
    for it in items:
        it.setdefault("jlpt", it.get("level", "—"))
        it.setdefault("freq", {
            "tier": "—", "stars": 0, "nade": 0, "wf": None,
            "basis": "未設定", "note": "未設定（Nadeshiko words で hit を取って tier を付ける）",
        })
    n_nade = sum(len(it.get("nadeshiko", [])) for it in items)

    display = copy.deepcopy(items)
    sents = {}
    for it in display:
        for i, ex in enumerate(it.get("examples") or []):
            ex["jp"] = add_furigana(ex["jp"])
            sents[f"{it['id']}-e{i}"] = {
                "jp": ex["jp"], "cn": ex.get("cn", ""), "src": ex.get("src", "")
            }
        for sc in it.get("nadeshiko") or []:
            sc["jp"] = add_furigana(sc["jp"])

    audio = gen_audio(items)
    print(f"[1/4] audio: {len(audio)} clips ready")

    print("[2/4] nadeshiko: downloading CDN audio...")
    nade_audio = gen_nade_audio(items)

    qs = build_questions(groups, items, audio)

    tags = (f"<span>{len(items)} 語（核心19＋周辺11）</span>"
            f"<span>{len(groups)} 分類</span>"
            f"<span>{len(audio)} 音声{' + ' + str(n_nade) + ' 原声' if n_nade else ''}</span>"
            f"<span>頻度 S〜D</span>"
            f"<span>JLPT N1–N4</span>"
            f"<span>keiyoushi.json 随手加</span>")
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
