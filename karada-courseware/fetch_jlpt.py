#!/usr/bin/env python3
"""Extract the JLPT N1 past-exam corpus for the からだの慣用句 courseware.

Reads : /home/naruto/scratch/jlpt-question-bank/n1/past-exams/<y>/<m>/<sec>/*.json
Writes: <this dir>/jlpt.json

Two layers:
  exams — full questions whose stem/options test a body idiom or body vocabulary
          (curated whitelist; question text/options/answers pulled from the bank).
  sents — real-exam sentences (読解/文法 passages) containing a courseware idiom,
          quoted with source and linked to the item.

Re-run after the bank grows to refresh the corpus. build.py only consumes
jlpt.json, so the courseware builds without the bank present.
"""
import json
import glob
import re
import unicodedata
from pathlib import Path

BANK = Path("/home/naruto/scratch/jlpt-question-bank/n1/past-exams")
HERE = Path(__file__).parent
OUT = HERE / "jlpt.json"

KIND = {
    ("vocab", "reading"): "語彙・漢字読み",
    ("vocab", "context"): "語彙・文脈規定",
    ("vocab", "paraphrase"): "語彙・言い換え",
    ("vocab", "usage"): "語彙・使い方",
    ("grammar", "choice"): "文法・文法選択",
    ("grammar", "composition"): "文法・文の並べ替え",
    ("grammar", "passage"): "文法・文章の文法",
    ("reading", "short"): "読解・短文",
    ("reading", "mid"): "読解・中文",
    ("reading", "long"): "読解・長文",
}

# ── curated exam whitelist: question id → item ids it illustrates (+ memo)
# Only questions that genuinely revolve around からだ expressions are listed.
# Body-vocabulary questions (念頭・胸中・背後…) carry no item id; they stay in
# the 真題 tab as 体のことば corpus.
EXAMS = [
    dict(qid="2011-07-vocab-reading-06", items=[], memo="肝心＝要紧、关键（肝）"),
    dict(qid="2012-12-vocab-context-13", items=[], memo="頭痛がひどい／痛みが和らぐ"),
    dict(qid="2013-12-vocab-context-09", items=[], memo="念頭にない＝全然没想过（頭）"),
    dict(qid="2013-12-vocab-context-12", items=["udemae"], memo="腕前を披露する"),
    dict(qid="2013-12-vocab-usage-22", items=["kuchi-dasu"], memo="口出し＝插嘴、多管闲事；与「口を出す」同源"),
    dict(qid="2014-07-vocab-usage-22", items=["kokorogamae"], memo="心構えが決まらない"),
    dict(qid="2014-07-vocab-usage-24", items=["shigamitsuku"], memo="足にしがみつく"),
    dict(qid="2014-12-vocab-usage-21", items=["urahara"], memo="〜とは裏腹に"),
    dict(qid="2015-07-vocab-usage-21", items=["hitode"], memo="人手が要る"),
    dict(qid="2015-12-vocab-reading-05", items=[], memo="目指す（目）"),
    dict(qid="2016-07-vocab-usage-21", items=[], memo="入手＝到手（手）"),
    dict(qid="2016-12-vocab-reading-02", items=[], memo="指摘（指）"),
    dict(qid="2017-12-vocab-reading-05", items=[], memo="指図＝指使、吩咐（指）"),
    dict(qid="2017-12-vocab-usage-22", items=["atama-ni-ukabu"], memo="頭に浮かぶ；真っ先に＝最先"),
    dict(qid="2017-12-vocab-usage-25", items=["unadareru"], memo="うなだれる＝垂下头"),
    dict(qid="2018-07-vocab-usage-25", items=["kokoroatari"], memo="心当たりがない＝毫无头绪"),
    dict(qid="2018-12-vocab-paraphrase-17", items=["tetate"], memo="手立て＝方法"),
    dict(qid="2019-07-vocab-paraphrase-17", items=[], memo="目撃者（目）"),
    dict(qid="2019-07-vocab-reading-01", items=["hara-tateru"], memo="腹が立つ"),
    dict(qid="2019-07-vocab-usage-23", items=[], memo="目安＝基准、大致目标（目）"),
    dict(qid="2020-12-vocab-paraphrase-15", items=["te-ni-suru"], memo="手にした新聞＝拿在手里的报纸"),
    dict(qid="2020-12-vocab-paraphrase-17", items=["ome-ni-kakaru"], memo="お目にかかる＝拜见"),
    dict(qid="2020-12-vocab-reading-03", items=[], memo="手品師（手）"),
    dict(qid="2021-12-vocab-paraphrase-19", items=["otetsuage"], memo="お手上げ＝束手无策"),
    dict(qid="2022-07-vocab-paraphrase-15", items=["heikou"], memo="閉口＝吃不消"),
    dict(qid="2022-07-vocab-paraphrase-18", items=["tewake"], memo="手分け＝分工"),
    dict(qid="2022-12-vocab-context-10", items=["te-ni-ireru"], memo="念願のマイホームを手に入れる"),
    dict(qid="2022-12-vocab-usage-24", items=["teitai"], memo="手痛いミス"),
    dict(qid="2023-07-vocab-paraphrase-19", items=[], memo="没頭＝埋头、热衷（頭）"),
    dict(qid="2023-07-vocab-usage-22", items=["me-ga-saeru"], memo="目がさえて眠れない"),
    dict(qid="2024-07-vocab-context-13", items=["ashidematoi", "honeori", "urame", "oyogoshi"],
         memo="正解足手まとい；干扰项骨折り/裏目/および腰"),
    dict(qid="2024-07-vocab-paraphrase-18", items=["unadareru", "shitamuku"], memo="うなだれて＝下を向いて"),
    dict(qid="2024-07-vocab-usage-20", items=[], memo="本题考風潮，正解2；选项1的搭配有误"),
    dict(qid="2024-07-vocab-usage-23", items=["ase-kaku"], memo="本题考補填，正解3；选项2含「汗をかく」的正确用法"),
    dict(qid="2024-12-vocab-context-10", items=["ashidome", "ikinuki"], memo="正解足止め；干扰项息抜き"),
    dict(qid="2024-12-vocab-context-13", items=[], memo="腕をつかむ（字面身体动作）"),
    dict(qid="2024-12-vocab-paraphrase-14", items=["shuwan"], memo="手腕＝才能、本领"),
    dict(qid="2024-12-vocab-paraphrase-17", items=[], memo="目下＝当前、目前（目）"),
    dict(qid="2024-12-vocab-reading-02", items=[], memo="背後（背）"),
    dict(qid="2025-07-vocab-context-08", items=["tesaki", "temoto", "tekiva"],
         memo="正解手先；手元/手芸/手際为干扰项"),
    dict(qid="2025-07-vocab-context-13", items=["kao-ga-ukabu"], memo="顔が脳裏に浮かぶ（正解：脳裏）"),
    dict(qid="2025-07-vocab-paraphrase-14", items=[], memo="着手＝开始做（手）"),
    dict(qid="2025-07-vocab-paraphrase-18", items=["kaina-kao"], memo="怪訝な顔＝一脸狐疑"),
    dict(qid="2025-07-vocab-reading-06", items=[], memo="胸中＝内心（胸）"),
    dict(qid="2018-07-grammar-choice-35", items=["koe-wo-kakeru"], memo="声をかける＝打招呼"),
    dict(qid="2019-07-grammar-choice-34", items=["koe-wo-kakeru"], memo="声をかけられた"),
    dict(qid="2025-07-grammar-choice-31", items=[], memo="足首を痛める（足）"),
    dict(qid="2018-12-grammar-composition-38", items=[], memo="目先の利益（目先）"),
    dict(qid="2020-12-grammar-choice-26", items=["kokorozukai"], memo="細やかな心遣い"),
    dict(qid="2021-12-grammar-composition-40", items=["hitode"], memo="人手不足が深刻する"),
    dict(qid="2025-07-grammar-composition-37", items=["hitode"], memo="近年の人手不足などにより"),
    dict(qid="2024-07-reading-long-57", items=[], memo="頭のなかに飼っておく＝把疑问留在脑中"),
]

# ── curated sentence corpus: real-exam sentences containing an idiom
SENTS = [
    dict(qid="2015-12-grammar-passage-45", jp="その「じゃあね」に頭にきたと。", items=["atama-ni-kuru"]),
    dict(qid="2019-12-reading-mid-50", jp="つまり、その瞬間の頭に浮かんだものは、ばらばらな断片と大まかな展望に他ならない。", items=["atama-ni-ukabu"]),
    dict(qid="2013-07-reading-long-63", jp="この丁寧さのおかげで、客は喜んで買い物をして、何度も足を運ぶ客となっていく。", items=["ashi-hakobu"]),
    dict(qid="2025-07-reading-long-57", jp="しかし、その逆に、子どもの頃から美術館に何度も足を運び、なじみのある場所になっていれば、芸術作品との距離はもっと近づくだろう。", items=["ashi-hakobu"]),
    dict(qid="2013-07-grammar-passage-41", jp="それに落葉の光景も思わず息を呑むほどのものであるらしい。", items=["iki-nomu"]),
    dict(qid="2013-12-reading-long-65", jp="パソコンから顔を上げて、まっすぐ目を見て話しましょう。", items=["kao-wo-ageru"]),
    dict(qid="2012-12-grammar-passage-41", jp="今まで「さぁ、踊って踊って」と、恥ずかしがる子供たちを踊りの輪に入れていた大人たちが、子供に声をかけなくなる。", items=["koe-wo-kakeru"]),
    dict(qid="2013-12-grammar-passage-41", jp="目を合わせない、それが礼儀作法です。", items=["me-awaseru"]),
    dict(qid="2013-07-reading-short-49", jp="絶望と見える対象を嫌ったり恐れたりして目をつぶって、そこを去れば、もう希望とは決して会えない。", items=["me-tsuburu"]),
    dict(qid="2013-12-grammar-passage-41", jp="さて、よく吠えられても動じず、目をそらしつつ、手のひらに犬クッキーを載せて差し出します。", items=["me-wo-sorasu"]),
    dict(qid="2011-07-grammar-passage-43", jp="みんながテレビの前で身を乗り出している瞬間にCMを入れれば、見られる。", items=["mi-noridasu"]),
    dict(qid="2013-12-reading-long-65", jp="そして下を向いていればいるほど、良くない事態が悪化します。", items=["shitamuku"]),
    dict(qid="2025-07-reading-long-60", jp="人は不安な状態に陥ると、反射的に下を向いています。", items=["shitamuku"]),
    dict(qid="2011-12-reading-long-63", jp="自分がお金を出して手に入れたものだからこそ、愛着も出てくるだろうし、身近において毎日みていることで、いろんな刺激を受けていくはずだ。", items=["te-ni-ireru"]),
    dict(qid="2014-07-reading-long-63", jp="気軽に手にしたマンガをきっかけに、知的好奇心が刺激されたりすることもあるだろう。", items=["te-ni-suru"]),
    dict(qid="2020-12-reading-long-61", jp="現代は、どれだけ多くの情報を手にしているかで決まると言っても過言ではない。", items=["te-ni-suru"]),
    dict(qid="2019-12-reading-short-48", jp="例えば、自分自身の気持ちとは裏腹に、あえて「人とは異なる発言、あるいは行動をする」ことを学んでいく。", items=["urahara"]),
    dict(qid="2022-07-reading-long-61", jp="人の話に耳を傾け、言葉と心のキャッチポールができる人は、間違いなく好印象を与えます。", items=["mimi-katamukeru"]),
    dict(qid="2023-07-reading-short-48", jp="よくよく考えたうえで口にする他人の異なる思いや考えに、これまたよく耳を澄ますことで、じぶんの考えを再点検しはじめるからだ。", items=["mimi-sumasu"]),
    dict(qid="2012-12-vocab-paraphrase-16", jp="友人はしきりにうなずきながら話を聞いていた。", items=["unazuku"]),
    dict(qid="2016-07-reading-long-65", jp="編集者の言うことは、一般の人に対する情報発信の心構えとして、現時点では適切と言うほかありません。", items=["kokorogamae"]),
    dict(qid="2021-12-reading-long-62", jp="このたび、貴社のテナント募集にあたり、弊店にもお声をかけて頂きましてありがとうございます。", items=["koe-wo-kakeru"]),
    dict(qid="2024-07-reading-mid-49", jp="毎朝、新聞を適当に開き、目をつぶって紙面を指す。", items=["me-tsuburu"]),
    dict(qid="2019-12-reading-long-64", jp="「欲しい物」というのは具体的な物であり、それを手に入れる方法は限られていた。", items=["te-ni-ireru"]),
    dict(qid="2024-12-reading-long-57", jp="慣れてくると手際が良くなる。", items=["tekiva"]),
]

# 手修正：题库转写里的错字/OCR 噪点
FIXES = {
    "2017-12-vocab-usage-22": [("真つ先", "真っ先")],
    "2020-12-vocab-paraphrase-15": [("じっト見た", "じっと見た")],
}

# usage 题的 notes 不完整时，手工补全正确答案句（用于 TTS 与解析）
ANSWER_SENTENCE = {
    "2017-12-vocab-usage-22": "真っ先に私の頭に浮かんだのは石川さんだった。",
    "2017-12-vocab-usage-25": "姉は知らせを聞いてがっかりしたのか、うなだれて顔を上げなかった。",
}

# 読み問題の前置き（転写ノイズ）を落とす
READING_PREAMBLE = re.compile(r"^_+の言葉の読み方として最もよいものを、.*?選びなさい。\s*")


def clean(s, keep_spaces=False):
    """Normalize Kangxi-radical variants (⽬→目 etc.) and tidy whitespace."""
    if not s:
        return s
    out = []
    for ch in s:
        o = ord(ch)
        if 0x2E80 <= o <= 0x2FDF or 0x3005 <= o <= 0x3007:
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    s = "".join(out)
    if not keep_spaces:
        # 转写里偶有汉字/假名之间的多余空格（進めら れて）；保留括号内空栏（ ）与英文空格
        s = re.sub(r"(?<=[\u4e00-\u9fff\u3041-\u30ff]) (?=[\u4e00-\u9fff\u3041-\u30ff])", "", s)
    return s


def apply_fixes(d, qid):
    fixes = FIXES.get(qid)
    if not fixes:
        return d
    def fx(s):
        if isinstance(s, str):
            for a, b in fixes:
                s = s.replace(a, b)
        return s
    d["question"] = fx(d.get("question") or "")
    d["options"] = [fx(o) for o in (d.get("options") or [])]
    d["notes"] = fx(d.get("notes") or "")
    return d


def fill_blank(text, word):
    return re.sub(r"[（(][\s　]*[）)]", word, text, count=1)


def main():
    by_id = {}
    for f in glob.glob(str(BANK) + "/**/*.json", recursive=True):
        d = json.load(open(f, encoding="utf-8"))
        by_id[d["id"]] = d

    exams = []
    for spec in EXAMS:
        qid = spec["qid"]
        d = by_id.get(qid)
        if not d:
            print(f"[!] missing {qid}, skipped")
            continue
        d = apply_fixes(d, qid)
        question = clean(d.get("question") or "")
        if d.get("type") == "reading":
            question = READING_PREAMBLE.sub("", question)
        options = [clean(o) for o in (d.get("options") or [])]
        raw_ans = str(d.get("answer") or "").strip()
        ans_idx = None
        if raw_ans.isdigit() and 1 <= int(raw_ans) <= len(options):
            ans_idx = int(raw_ans) - 1
        answer_text = options[ans_idx] if ans_idx is not None else raw_ans
        notes = clean(d.get("notes") or "")
        notes = re.sub(r"\[[^\]]+\]", "", notes)   # 去掉 [tryni-api]・[auto-proofread …] 等标注
        notes = re.sub(r"\s+", " ", notes).strip()
        answer_sentence = ""
        if d.get("type") == "usage":
            answer_sentence = ANSWER_SENTENCE.get(qid, "")
            if not answer_sentence and len(notes) > 12:
                answer_sentence = notes
        elif d.get("type") in ("context", "choice") and ans_idx is not None:
            answer_sentence = fill_blank(question, options[ans_idx])
            if answer_sentence == question:
                answer_sentence = ""
        if answer_sentence and not answer_sentence.endswith(("。", "！", "？", "」", "）")):
            answer_sentence += "。"
        exams.append(dict(
            id=qid,
            year=d["year"], month=d["month"],
            section=d["section"], type=d["type"], number=d["number"],
            kind=KIND.get((d["section"], d["type"]), d["type"]),
            question=question,
            options=options,
            answer=ans_idx,
            answer_text=answer_text,
            answer_sentence=answer_sentence,
            source=clean(d.get("source") or "", keep_spaces=True),
            item_ids=spec["items"],
            memo=spec.get("memo", ""),
        ))

    sents = []
    for i, spec in enumerate(SENTS):
        d = by_id.get(spec["qid"])
        if not d:
            print(f"[!] missing {spec['qid']} (sent), skipped")
            continue
        sents.append(dict(
            id=f"{spec['qid']}-s{i}",
            jp=clean(spec["jp"]),
            qid=spec["qid"],
            source=clean(d.get("source") or "", keep_spaces=True),
            item_ids=spec["items"],
        ))

    payload = dict(
        meta=dict(
            title="からだの JLPT N1 過去問コーパス",
            source="JLPT N1 2010-07 ～ 2025-07（jlpt-question-bank n1/ 収録分）",
            note="JLPT 官方不公开真题；题目来自考生回忆/学习站点整理，仅作个人学习之非商业性引用。",
            exams=len(exams), sents=len(sents),
        ),
        exams=exams,
        sents=sents,
    )
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(exams)} exams, {len(sents)} sents")
    linked = sum(1 for e in exams if e["item_ids"])
    print(f"      exams linked to items: {linked}")


if __name__ == "__main__":
    main()
