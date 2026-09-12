#!/usr/bin/env python3
"""Extract JLPT N1 文法 questions from the question-bank and build koou.json
for the 呼応・搭配 courseware.

Reads:  /home/naruto/scratch/jlpt-n1-question-bank/past-exams/<y>/<m>/grammar/*.json
Writes: <this dir>/koou.json

The courseware theme is 呼応・搭配 (correlative / collocation patterns).
Every 問題5 文法選択 + 問題6 並べ替え question is attached to a pattern item
when its correct answer / stem matches; the rest stay in the exhaustive 真题
list (group "other"). 問題7 文章文法 passages are listed in the 真题 tab but
not attached to patterns.
"""
import json
import glob
import re
import os
import unicodedata
from pathlib import Path

BANK = Path("/home/naruto/scratch/jlpt-n1-question-bank")
HERE = Path(__file__).parent
OUT = HERE / "koou.json"

SECTION_NAME = {"choice": "問題5 文法選択", "composition": "問題6 並べ替え",
                "passage": "問題7 文章文法"}


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("〜", "").replace("～", "").replace(" ", "").replace("　", "")
    s = s.replace("correct", "").strip()
    return s


# ───────────────────────────── pattern catalog
# ans  : normalized correct-answer texts that select this pattern
# stems: substrings in the stem that select this pattern (used when ans misses)
# order matters (first match wins)
CATALOG = [
 # ===== A 仮定・譲歩 =====
 dict(id="tatoe", word="たとえ〜ても", read="たとえても", group="hypothesis", level="N2",
      emoji="🌧️", trigger="たとえ", response="〜ても／でも／としても／だろうと",
      meaning="即使…也…", engine="たとえ（极端假定触发器）＋ 〜ても（让步形态）",
      blueprint="先把最坏情况推到极端，再声明结论不变",
      ans=["自分の責任", "チームだろうと", "それは"], stems=[],
      note="⚠️ 「ことのたとえ」（比喻）不是呼応，勿混；本项只收たとえ〜ても 的真题。"),
 dict(id="donna", word="どんなに／どんな〜ても", read="どんなにても", group="hypothesis", level="N2",
      emoji="🌀", trigger="どんなに／どんな", response="〜ても／ようと／であろうと",
      meaning="无论多么…也…", engine="どんなに（程度无上限）＋ 〜ても／ようと（让步）",
      blueprint="把程度开到无穷大，结论依旧成立",
      ans=["浴びようとも", "しようと"], stems=["どんな反論", "どのような批判", "どんな困難", "どんな理由"],
      note="与「いくら〜ても」同族：都在放大前项、锁定后项。"),
 dict(id="darou", word="〜だろうが〜だろうが／〜だろうと〜だろうと", read="だろうが", group="hypothesis", level="N2",
      emoji="♾️", trigger="だろうが／だろうと", response="同一让步形态重复列举",
      meaning="无论是…还是…都…", engine="名词/句子＋だろうが／だろうと（列举让步）",
      blueprint="列举所有可能身份，一一排除例外",
      ans=["先輩だろうが上司だろうが", "雨だろうと雪だろうと", "悪人で"], stems=[]),
 dict(id="ikura", word="いくら〜ても／たら", read="いくらても", group="hypothesis", level="N3",
      emoji="🔁", trigger="いくら", response="〜ても／〜たら",
      meaning="无论怎么…也…", engine="いくら（次数/量无上限）＋ 〜ても",
      blueprint="把尝试次数开到无限，结果仍不变",
      ans=["飽きそうなものなのに", "べきではないのでしょうか"], stems=["いくら"],
      note="「いくら注意しても」「いくら見ていたら」——前项表反复，后项表结论不变。"),
 dict(id="surumoshinaimo", word="〜するもしないも", read="するもしないも", group="hypothesis", level="N1",
      emoji="🎲", trigger="するもしないも", response="〜にかかっている",
      meaning="做还是不做，全看…", engine="动词辞书形＋も＋否定形＋も（正反并列名词化）",
      blueprint="把两种可能摆在一起，交由对方决定",
      ans=["するもしないも"], stems=[]),
 dict(id="kari", word="かりに〜とすると", read="かりにとすると", group="hypothesis", level="N2",
      emoji="❓", trigger="かりに／仮に", response="〜とすると／としたら／とすれば",
      meaning="假设…的话", engine="かりに（假定标记）＋ 〜とすると（条件归结）",
      blueprint="先虚构一个前提，再推演其后果",
      ans=[], stems=[],
      note="⚠️ 2010–2025 筆試文法題未直接考；仅読解语料。见 README 缺口清单。"),

 # ===== B 全面否定 =====
 dict(id="kesshite", word="決して〜ない", read="けっしてない", group="negation", level="N3",
      emoji="🚫", trigger="決して", response="〜ない（全面否定）",
      meaning="绝不…", engine="決して（否定强调副词）＋ 否定述语",
      blueprint="把否定强度拉满，不留例外",
      ans=["認めようとはしなかった", "着ようとはしなかった"], stems=["決して"]),
 dict(id="issai", word="いっさい〜ない", read="いっさいない", group="negation", level="N2",
      emoji="0️⃣", trigger="いっさい／一切", response="〜ない／ず（全面否定）",
      meaning="完全不…、一点也不…", engine="いっさい（范围归零）＋ 否定",
      blueprint="把范围清零，一个都不剩",
      ans=["いっさい"], stems=[]),
 dict(id="nanra", word="なんら〜ない", read="なんら", group="negation", level="N2",
      emoji="∅", trigger="なんら／何ら", response="〜ない（全面否定）",
      meaning="毫无…、没有任何…", engine="なんら（否定强调，书面）＋ 否定",
      blueprint="强调「连一点都没有」",
      ans=["なんら"], stems=[]),
 dict(id="kanarazushimo", word="必ずしも〜ない／とは限らない", read="かならずしも", group="negation", level="N2",
      emoji="⚖️", trigger="必ずしも", response="〜ない／とは限らない",
      meaning="未必…、不一定…", engine="必ずしも（部分否定）＋ 否定/とは限らない",
      blueprint="承认可能，但拒绝必然",
      ans=["なくしてはならないかというと"], stems=["必ずしも"]),
 dict(id="nanimo", word="なにも〜なくても", read="なにも", group="negation", level="N3",
      emoji="🙅", trigger="なにも／何も", response="〜なくても／ない",
      meaning="用不着…、没必要…", engine="なにも（全面否定）＋ 〜なくても",
      blueprint="把整件事一笔勾销：不必如此",
      ans=["なにも"], stems=["なにもそんなに"]),
 dict(id="sou", word="そう〜ない", read="そう", group="negation", level="N3",
      emoji="📉", trigger="そう＋否定", response="そう簡単には〜ない",
      meaning="并不那么…", engine="そう（程度指示）＋ 否定",
      blueprint="把对方的预期调低一档",
      ans=["そう"], stems=[]),
 dict(id="totemo", word="とても／全然〜ない", read="とても", group="negation", level="N3",
      emoji="😵", trigger="とても／全然", response="〜ない",
      meaning="怎么也…不了；完全不…", engine="程度副词＋否定（能力/程度达不到）",
      blueprint="把能力/程度标到极限，仍够不着",
      ans=["わかんないんだもん"], stems=["全然"]),
 dict(id="nakanaka", word="なかなか〜ない", read="なかなか", group="negation", level="N3",
      emoji="⏳", trigger="なかなか", response="〜ない",
      meaning="怎么也不…、迟迟不…", engine="なかなか（期待落空）＋ 否定",
      blueprint="等的人迟迟不来，强调「难以实现」",
      ans=["なかなか"], stems=[]),

 # ===== C 推量・様態 =====
 dict(id="douyara", word="どうやら〜ようだ／らしい", read="どうやら", group="conjecture", level="N3",
      emoji="🔮", trigger="どうやら", response="〜ようだ／らしい／そうだ",
      meaning="好像…、看来…", engine="どうやら（委婉推断）＋ 推量述语",
      blueprint="不把话说死，用证据轻轻推出结论",
      ans=["どうやら"], stems=[]),
 dict(id="hatashite", word="はたして〜だろうか", read="はたして", group="conjecture", level="N1",
      emoji="🤔", trigger="はたして／果たして", response="〜だろうか／でしょうか",
      meaning="究竟…吗（质疑）", engine="はたして（疑问强化）＋ 疑问述语",
      blueprint="先把疑问举起来，再暗示怀疑",
      ans=["はたして", "果たして", "といえるだろうか"], stems=["はたして", "果たして"]),
 dict(id="marude", word="まるで〜ない／ようだ", read="まるで", group="conjecture", level="N3",
      emoji="🪞", trigger="まるで", response="〜ない／かのようだ",
      meaning="完全（不）…；简直像…", engine="まるで（比喻/全面强调）＋ 否定或比况",
      blueprint="用「像…一样」或「完全不…」放大印象",
      ans=["まるで"], stems=[]),
 dict(id="atakamo", word="あたかも〜かのようだ／かのごとく", read="あたかも", group="conjecture", level="N1",
      emoji="🎭", trigger="あたかも", response="〜かのようだ／かのごとく",
      meaning="仿佛…一般", engine="あたかも（比况，书面）＋ かのようだ／かのごとく",
      blueprint="把「并非如此」说成「宛如如此」",
      ans=["かのごとく"], stems=["あたかも"]),
 dict(id="sasuga", word="さすが〜だけあって", read="さすが", group="conjecture", level="N2",
      emoji="👏", trigger="さすが", response="〜だけあって／だけのことはある",
      meaning="不愧是…、到底…", engine="さすが（预期验证）＋ だけあって（相符）",
      blueprint="先抬高预期，再确认「果然名不虚传」",
      ans=["なかなか", "していただけあって", "だけあって"], stems=["さすが"]),

 # ===== D 強調・程度 =====
 dict(id="semete", word="せめて〜だけでも", read="せめて", group="emphasis", level="N3",
      emoji="🥺", trigger="せめて", response="〜だけでも／だけは",
      meaning="至少…也好", engine="せめて（最低愿望）＋ だけでも（限定让步）",
      blueprint="退而求其次，把愿望压到最低线",
      ans=["だけでも"], stems=["せめて"]),
 dict(id="nanto", word="なんと〜ことか／どんなに〜ことか", read="なんと", group="emphasis", level="N2",
      emoji="❗", trigger="なんと／どんなに", response="〜ことか",
      meaning="多么…啊！", engine="疑问词（程度感叹）＋ ことか（感叹句尾）",
      blueprint="用反问句把感叹推到顶点",
      ans=["ことか", "感謝してもしきれない"], stems=["なんと美しかった"]),
 dict(id="douse", word="どうせ〜から", read="どうせ", group="emphasis", level="N3",
      emoji="🤷", trigger="どうせ", response="〜から（既定前提）",
      meaning="反正…", engine="どうせ（放弃/既定）＋ 理由句",
      blueprint="先认定结果不会变，再行动",
      ans=["だけ"], stems=["どうせ"]),
 dict(id="tada", word="ただ／単に〜だけ／のみ", read="ただ", group="emphasis", level="N3",
      emoji="☝️", trigger="ただ／単に", response="〜だけ／のみ（限定）",
      meaning="只是…、仅仅…", engine="ただ／単に（限定）＋ だけ／のみ",
      blueprint="把范围缩到最小，排除其他",
      ans=["のみではなく", "ただ", "だけ"], stems=["単に"]),

 # ===== E 文末モダリティ =====
 dict(id="chigainai", word="〜に違いない", read="にちがいない", group="modality", level="N3",
      emoji="🔒", trigger="に違いない", response="〜に違いない",
      meaning="一定是…", engine="推量 + 确信（に違いない）",
      blueprint="证据充分，把推测升级为断定",
      ans=["彼の性格", "育ち続けるに違いない"], stems=["に違いない"]),
 dict(id="hazu", word="〜はずだ", read="はずだ", group="modality", level="N3",
      emoji="📐", trigger="はず", response="〜はずだ／はずがない",
      meaning="理应…；不可能…", engine="客观推论（はず）",
      blueprint="按道理/逻辑推出来的结论",
      ans=["はずだ", "悪人で"], stems=["はず"]),
 dict(id="kimatteru", word="〜に決まっている", read="にきまっている", group="modality", level="N2",
      emoji="✅", trigger="に決まっている", response="〜に決まっている",
      meaning="肯定…、一定…", engine="主观强断定（に決まっている）",
      blueprint="不留余地地拍板",
      ans=["言われるに決まっている"], stems=["に決まっている"]),
 dict(id="osore", word="〜おそれがある", read="おそれがある", group="modality", level="N2",
      emoji="⚠️", trigger="おそれ／恐れ", response="〜おそれがある",
      meaning="有…之虞、恐怕会…", engine="负面可能性（おそれがある）",
      blueprint="预告一个不希望发生的风险",
      ans=["恐れがある以上", "失わせてしまうおそれがある"], stems=["おそれ"]),
 dict(id="hokanaranai", word="〜にほかならない", read="にほかならない", group="modality", level="N2",
      emoji="🎯", trigger="にほかならない", response="〜にほかならない",
      meaning="无非是…、正是…", engine="排他性断定（にほかならない）",
      blueprint="排除其他可能，锁定唯一原因",
      ans=["サッカーを続けてこられたのは"], stems=["にほかならない"]),
 dict(id="kotoniwa", word="〜ことには〜ない", read="ことには", group="modality", level="N2",
      emoji="🔗", trigger="ことには", response="〜ない",
      meaning="不…就不…", engine="条件（ことには）＋ 否定结果",
      blueprint="把前项设为后项成立的必要条件",
      ans=["知られないことには"], stems=["ことには"]),
 dict(id="bakoso", word="〜ばこそ", read="ばこそ", group="modality", level="N1",
      emoji="💗", trigger="ばこそ", response="〜ばこそ（强调理由）",
      meaning="正因为…才…", engine="条件形＋こそ（强调理由）",
      blueprint="把原因单拎出来强调",
      ans=["思えばこそだったのだと"], stems=["ばこそ"]),
 dict(id="monka", word="〜もんか／ものか", read="もんか", group="modality", level="N2",
      emoji="🙅‍♂️", trigger="ものか／もんか", response="〜もんか（强烈否定）",
      meaning="绝不会…！", engine="反问式强烈否定（ものか／もんか）",
      blueprint="用反问把否定说到最狠",
      ans=["もんか", "などするものか", "ものか"], stems=[]),
 dict(id="karatoitte", word="〜からといって", read="からといって", group="modality", level="N2",
      emoji="↩️", trigger="からといって", response="〜とは限らない／ものでもない",
      meaning="虽说…也未必…", engine="からといって（理由让步）＋ 否定",
      blueprint="承认理由，但否认必然结论",
      ans=["かというと"], stems=["からといって"]),
 dict(id="mikomi", word="〜見込みだ", read="みこみだ", group="modality", level="N2",
      emoji="📈", trigger="見込み", response="〜見込みだ",
      meaning="预计会…", engine="客观预测（見込みだ）",
      blueprint="基于现状给出未来预期",
      ans=["見込みだ", "強まる見込みです"], stems=["見込み"]),

 # ===== F 助詞系文型 =====
 dict(id="wokiniki", word="〜を機に", read="をきに", group="particle", level="N2", emoji="🔄",
      trigger="を機に", response="〜を機に", meaning="以…为契机",
      engine="を（对象）＋ 機（节点）＋ に（时点）", blueprint="把一个事件钉成转折点",
      ans=["を機に"], stems=[]),
 dict(id="nisakidachi", word="〜に先立ち", read="にさきだち", group="particle", level="N1", emoji="⏮️",
      trigger="に先立ち", response="〜に先立ち", meaning="在…之前（先行准备）",
      engine="に（时点）＋ 先立つ（先行）", blueprint="正式场合的「先做铺垫」",
      ans=["に先立ち"], stems=[]),
 dict(id="woukete", word="〜を受けて", read="をうけて", group="particle", level="N2", emoji="📥",
      trigger="を受けて", response="〜を受けて", meaning="接受…之后、响应…",
      engine="を（对象）＋ 受ける（承接）", blueprint="把前项当作触发条件承接过来",
      ans=["を受けて"], stems=[]),
 dict(id="wokawagiri", word="〜を皮切りに", read="をかわぎりに", group="particle", level="N1", emoji="🎬",
      trigger="を皮切りに", response="〜を皮切りに", meaning="以…为开端（接连发生）",
      engine="を（起点）＋ 皮切り（开端）", blueprint="把第一件事设为连锁起点",
      ans=["を皮切りに"], stems=[]),
 dict(id="wohikaete", word="〜を控えて", read="をひかえて", group="particle", level="N1", emoji="⏳",
      trigger="を控えて", response="〜を控えて", meaning="面临…、临近…",
      engine="を（对象）＋ 控える（临近）", blueprint="站在大事前的临场感",
      ans=["を控えて"], stems=[]),
 dict(id="womotte", word="〜をもって", read="をもって", group="particle", level="N1", emoji="📅",
      trigger="をもって", response="〜をもって", meaning="以…为界；用…（手段）",
      engine="を（对象）＋ 持つ（凭借/以此为界）", blueprint="用某物/某时点作为分界或手段",
      ans=["をもって"], stems=[]),
 dict(id="womottesureba", word="〜をもってすれば", read="をもってすれば", group="particle", level="N1", emoji="🛠️",
      trigger="をもってすれば", response="〜をもってすれば", meaning="凭…的话（就能…）",
      engine="をもって（手段）＋ すれば（条件）", blueprint="假设拥有某手段，结果就不同",
      ans=["をもってすれば"], stems=[]),
 dict(id="toatte", word="〜とあって", read="とあって", group="particle", level="N2", emoji="🎉",
      trigger="とあって", response="〜とあって", meaning="因为（特殊状况）…",
      engine="と（引用）＋ あって（原因）", blueprint="给出特殊背景，解释反常现象",
      ans=["とあって"], stems=[]),
 dict(id="toatteha", word="〜とあっては", read="とあっては", group="particle", level="N1", emoji="😤",
      trigger="とあっては", response="〜とあっては", meaning="既然是…（就不得不）",
      engine="とあっては（既定条件）", blueprint="既成事实当前，只能如此",
      ans=["とあっては"], stems=[]),
 dict(id="dakeni", word="〜だけに", read="だけに", group="particle", level="N2", emoji="🎯",
      trigger="だけに", response="〜だけに", meaning="正因为…（所以更加）",
      engine="だけ（程度）＋ に（理由）", blueprint="把原因与结果强度挂钩",
      ans=["が"], stems=["時期が時期だけに"]),
 dict(id="bakarini", word="〜ばかりに", read="ばかりに", group="particle", level="N2", emoji="😖",
      trigger="ばかりに", response="〜ばかりに", meaning="只因为…（导致坏结果）",
      engine="ばかり（限定原因）＋ に", blueprint="把坏事归因于单一原因",
      ans=["ばかりに"], stems=[]),
 dict(id="yueni", word="〜ゆえに／がゆえに", read="ゆえに", group="particle", level="N1", emoji="🧬",
      trigger="ゆえに／がゆえに", response="〜ゆえに", meaning="因为…（书面）",
      engine="ゆえ（原因名词）＋ に", blueprint="把原因名词化后作状语",
      ans=["ゆえに", "がゆえに", "であるがゆえの"], stems=[]),
 dict(id="nitsuke", word="〜につけ", read="につけ", group="particle", level="N1", emoji="🔔",
      trigger="につけ", response="〜につけ（每当…就…）", meaning="每当…就…",
      engine="に（时点）＋ つけ（每次）", blueprint="把一个契机与自动反应绑定",
      ans=["につけ"], stems=[]),
 dict(id="nitomonai", word="〜にともない", read="にともない", group="particle", level="N2", emoji="🔗",
      trigger="にともない", response="〜にともない", meaning="随着…（书面）",
      engine="に（伴随对象）＋ ともなう（同行）", blueprint="把两个变化捆成因果套餐",
      ans=["にともない"], stems=[]),
 dict(id="naradeha", word="〜ならではの", read="ならではの", group="particle", level="N2", emoji="🏅",
      trigger="ならでは", response="〜ならではの", meaning="只有…才有的",
      engine="ならでは（限定主体）＋ の", blueprint="强调「非它不可」的独特性",
      ans=["ならではの"], stems=[]),
 dict(id="nishiteha", word="〜にしては", read="にしては", group="particle", level="N3", emoji="🤨",
      trigger="にしては", response="〜にしては", meaning="就…而言却…（反差）",
      engine="に（基准）＋ しては（对比）", blueprint="拿标准对比，凸显意外",
      ans=["にしては"], stems=[]),
 dict(id="toaimatte", word="〜と相まって", read="とあいまって", group="particle", level="N1", emoji="➕",
      trigger="と相まって", response="〜と相まって", meaning="与…相辅相成",
      engine="と（共同）＋ 相まつ（相互作用）", blueprint="两个因素叠加，效果加倍",
      ans=["と相まって"], stems=[]),
 dict(id="hadouare", word="〜はどうあれ", read="はどうあれ", group="particle", level="N1", emoji="🤷‍♀️",
      trigger="はどうあれ", response="〜はどうあれ", meaning="不管…如何",
      engine="は（主题）＋ どうあれ（无论怎样）", blueprint="把方式排除在讨论之外",
      ans=["はどうあれ"], stems=[]),
 dict(id="nikagitte", word="〜に限って", read="にかぎって", group="particle", level="N2", emoji="🎯",
      trigger="に限って", response="〜に限って", meaning="偏偏在…；唯独…",
      engine="に（限定）＋ 限る（仅此）", blueprint="把范围缩到唯一，制造巧合",
      ans=["というときに限って"], stems=["に限って"]),
 dict(id="toshinagaramo", word="〜としながらも", read="としながらも", group="particle", level="N1", emoji="↔️",
      trigger="としながらも", response="〜としながらも", meaning="虽然…却…",
      engine="と（内容）＋ しながら（同时）＋ も（逆接）", blueprint="承认前项，同时亮出反调",
      ans=["としながらも"], stems=[]),
 dict(id="niatte", word="〜にあっても／にあって", read="にあっても", group="particle", level="N1", emoji="🏔️",
      trigger="にあって", response="〜にあって（は）", meaning="身处…之中（书面）",
      engine="に（存在点）＋ あって（ある）", blueprint="把处境设为讨论的舞台",
      ans=["にあっても"], stems=[]),
 dict(id="karashika", word="〜からしか〜ない", read="からしか", group="particle", level="N2", emoji="🚪",
      trigger="からしか", response="〜からしか〜ない", meaning="只能从…",
      engine="から（起点）＋ しか（限定）＋ 否定", blueprint="把来源唯一化",
      ans=["からしか"], stems=[]),
 dict(id="woomotte", word="〜を思って", read="をおもって", group="particle", level="N2", emoji="💭",
      trigger="を思って", response="〜を思って", meaning="为…着想",
      engine="を（对象）＋ 思う（挂念）", blueprint="把动机归到对某人的牵挂",
      ans=["を思って"], stems=[]),
 dict(id="nowoii", word="〜のをいいことに", read="のをいいことに", group="particle", level="N1", emoji="😈",
      trigger="のをいいことに", response="〜のをいいことに", meaning="趁…之机（做坏事）",
      engine="のを（内容）＋ いいことに（当作好机会）", blueprint="把对方的疏忽当挡箭牌",
      ans=["のをいいことに"], stems=[]),
 dict(id="tohikikae", word="〜と引きかえに", read="とひきかえに", group="particle", level="N2", emoji="⚖️",
      trigger="と引きかえに", response="〜と引きかえに", meaning="作为…的代价/交换",
      engine="と（交换对象）＋ 引きかえ（对换）", blueprint="得到什么，就用什么交换",
      ans=["と引きかえに"], stems=[]),
 dict(id="yosoni", word="〜をよそに", read="をよそに", group="particle", level="N1", emoji="🙉",
      trigger="をよそに", response="〜をよそに", meaning="不顾…、无视…",
      engine="を（对象）＋ よそ（别处）", blueprint="把周遭反应当耳旁风",
      ans=["よそに"], stems=[]),
 dict(id="mamade", word="〜まま（に）", read="まま", group="particle", level="N2", emoji="🧊",
      trigger="まま", response="〜まま（に）", meaning="保持…状态；任凭…",
      engine="名詞/動詞た形＋まま（状态保持）", blueprint="把状态原样保留，不加干预",
      ans=["まま"], stems=[]),
 dict(id="toshite", word="〜として", read="として", group="particle", level="N3", emoji="🎓",
      trigger="として", response="〜として", meaning="作为…",
      engine="と（引用）＋ して（立场）", blueprint="给主体贴一个身份标签",
      ans=["として"], stems=[]),
 dict(id="deha", word="〜では", read="では", group="particle", level="N4", emoji="📊",
      trigger="では", response="〜では", meaning="用…的话（就…）",
      engine="で（范围）＋ は（提示）", blueprint="限定范围后给出评价",
      ans=["では"], stems=[]),
 dict(id="katsu", word="〜かつ", read="かつ", group="particle", level="N2", emoji="➕",
      trigger="かつ", response="〜かつ〜", meaning="既…又…（书面并列）",
      engine="かつ（并列）", blueprint="把两个属性用同一个主语连起来",
      ans=["かつ"], stems=[]),
 dict(id="shidai", word="〜次第で／次第では", read="しだいで", group="particle", level="N2", emoji="🎚️",
      trigger="次第", response="〜次第で（は）", meaning="取决于…",
      engine="しだい（依存）＋ で", blueprint="把结果挂在一个变量上",
      ans=["次第では", "次第で"], stems=[]),
 dict(id="baaiwonoite", word="〜場合を除いて", read="ばあいをのぞいて", group="particle", level="N2", emoji="➖",
      trigger="場合を除いて", response="〜場合を除いて", meaning="除了…的情况",
      engine="場合（情况）＋ 除く（排除）", blueprint="先划出例外，再讲通则",
      ans=["場合を除いて"], stems=[]),
 dict(id="tomonareba", word="〜ともなれば", read="ともなれば", group="particle", level="N1", emoji="⬆️",
      trigger="ともなれば", response="〜ともなれば", meaning="一旦到了…（就…）",
      engine="とも（假定）＋ なれば（条件）", blueprint="把某个阶段设为分水岭",
      ans=["ともなれば"], stems=[]),
 dict(id="nitaishite", word="〜に対して", read="にたいして", group="particle", level="N3", emoji="🆚",
      trigger="に対して", response="〜に対して", meaning="对…；与…相反",
      engine="に（对象）＋ 対する（面对）", blueprint="把动作对准目标，或形成对照",
      ans=[], stems=["に対して"]),
 dict(id="nisuginai", word="〜にすぎない", read="にすぎない", group="particle", level="N2", emoji="🔽",
      trigger="にすぎない", response="〜にすぎない", meaning="不过是…",
      engine="に（归着）＋ すぎる（超出）＋ ない", blueprint="把评价压到最低",
      ans=["にすぎません"], stems=["にすぎ"]),
 dict(id="kotonodakara", word="〜ことだから", read="ことだから", group="particle", level="N2", emoji="🙋",
      trigger="ことだから", response="〜ことだから", meaning="因为（是…的人/事）",
      engine="こと（性质）＋ だから（理由）", blueprint="拿对方一贯作风当理由",
      ans=["ことだから"], stems=[]),
]


def _P(iid, word, group, level, emoji, meaning, engine, blueprint,
       ans=(), stems=(), trigger="", response=""):
    return dict(id=iid, word=word, read=re.sub(r"[〜／/・（）()]", "", word),
                group=group, level=level, emoji=emoji,
                trigger=trigger or word, response=response or word,
                meaning=meaning, engine=engine, blueprint=blueprint,
                ans=list(ans), stems=list(stems))


# 补充：常见 N1/N2 文型（与呼応・搭配相关，扩大覆盖）
EXTRA = [
 _P("nihodohodogaaru", "〜にもほどがある", "emphasis", "N2", "🧨",
    "…也该有个限度", "名词/动词＋にもほどがある", "把「过度」直接判定为越界",
    ans=["にもほどがある"]),
 _P("toomoikiya", "〜かと思いきや", "modality", "N2", "🎭",
    "本以为…却…", "かと思いきや（预期反转）", "先设一个预期，立刻推翻",
    ans=["待たされるかと思いきや"], stems=["と思いきや"]),
 _P("kiwamarinai", "〜極まりない", "emphasis", "N1", "💥",
    "极其…、…之至", "極まりない（极端程度）", "把负面评价推到极点",
    ans=["極まりない"]),
 _P("zaruwoenai", "〜ざるを得ない", "modality", "N2", "⛓️",
    "不得不…", "ない形＋ざるを得ない（被迫）", "排除选项后只剩一条路",
    ans=["ざるを得ない"], stems=["ざるを得"]),
 _P("tsutsuaru", "〜つつある", "modality", "N2", "📈",
    "正在…（持续变化）", "ます形＋つつある（进行）", "把变化描述成正在推进",
    ans=["となりつつある", "高まりつつある"], stems=["つつある"]),
 _P("beku", "〜べく", "modality", "N2", "🎯",
    "为了…（书面）", "辞书形＋べく（目的）", "把目的前置为文言状语",
    ans=["帰るべく", "守るべく"], stems=["べく"]),
 _P("gatai", "〜がたい", "modality", "N2", "🧱",
    "难以…", "ます形＋がたい（心理上难）", "强调「想做却难做到」",
    ans=["受け入れがたい"], stems=["がたい"]),
 _P("kanenai", "〜かねない", "modality", "N2", "⚠️",
    "恐怕会…（坏结果）", "ます形＋かねない（可能）", "预告一个不愿见到的可能",
    ans=["失われかねない"], stems=["かねない"]),
 _P("dokorodehanai", "〜どころではない", "emphasis", "N2", "🚫",
    "根本不是…的时候", "どころではない（否定前项）", "把某事的可能性彻底否掉",
    ans=["どころではない", "楽しむどころではなかった"], stems=["どころではない"]),
 _P("tehirarenai", "〜てはいられない", "modality", "N2", "🏃",
    "不能一直…", "て形＋はいられない", "强调不能再维持现状",
    ans=["てはいられない"]),
 _P("kuseni", "〜くせに", "modality", "N2", "😤",
    "明明…却…（责难）", "くせに（逆接+不满）", "带着责备揭穿矛盾",
    ans=["ないくせに"], stems=["くせに"]),
 _P("gachi", "〜がちな", "modality", "N2", "📊",
    "容易…、往往…", "がち（倾向）", "指出某种反复出现的倾向",
    ans=["がちな"], stems=["がちな"]),
 _P("niokeru", "〜における／において", "particle", "N2", "📍",
    "在…（方面/场合）", "に＋於ける（书面场所/领域）", "把讨论限定在某个领域",
    ans=["における"], stems=["における"]),
 _P("totanni", "〜とたんに", "particle", "N2", "⚡",
    "刚一…就…", "た形＋とたんに（瞬间）", "两个动作零时差衔接",
    ans=["出ようとしたとたんに"], stems=["とたん"]),
 _P("uchini", "〜うちに", "particle", "N3", "⏳",
    "趁着…；在…过程中", "うちに（期间）", "在窗口关闭前完成",
    ans=["するうちに"], stems=["うちに"]),
 _P("nari", "〜なり", "particle", "N1", "🌀",
    "刚一…就…", "辞书形/た形＋なり", "前一动作刚结束，后一动作立刻发生",
    ans=["帰るなり", "なり"], stems=[]),
 _P("mononara", "〜ものなら", "modality", "N2", "🪄",
    "如果能…的话（假定）", "可能形＋ものなら", "把难实现的事设为条件",
    ans=["抜かずに済むものなら"], stems=["ものなら"]),
 _P("wakeniha", "〜わけにはいかない", "modality", "N2", "⛔",
    "不能…", "わけにはいかない（社会性禁止）", "从情理上排除某行为",
    ans=["待っていただくわけにはいきませんか", "わけにもいかず"], stems=["わけには", "わけにも"]),
 _P("zuniwa", "〜ずにはいられない", "modality", "N2", "💓",
    "忍不住…", "ない形＋ずにはいられない", "情绪推着人不由自主地做",
    ans=["指摘せずにはいられない"], stems=["ずにはいられない"]),
 _P("kotoda", "〜ことだ", "modality", "N2", "📌",
    "最好…；应该…", "ことだ（忠告）", "以建议/劝告的语气收尾",
    ans=["ことだ"], stems=[]),
 _P("koso", "〜こそ／からこそ", "emphasis", "N3", "💎",
    "正是…才…", "こそ（强调）", "把焦点单挑出来强调",
    ans=["こそ"], stems=[]),
 _P("monono", "〜ものの", "modality", "N2", "↔️",
    "虽然…但是…", "ものの（逆接，书面）", "承认前项，但结论不随之走",
    ans=["疑いようがないものの", "診てもらうほどではないものの", "つかもうとしたものの"], stems=["ものの"]),
 _P("tohaie", "〜とはいえ", "hypothesis", "N2", "↩️",
    "虽说…但…", "とはいえ（让步）", "承认事实，再补充反例",
    ans=["とはいえ"], stems=["とはいえ"]),
 _P("nimokakawarazu", "〜にもかかわらず", "hypothesis", "N2", "🚧",
    "尽管…却…", "にもかかわらず（逆接）", "前后项形成强烈反差",
    ans=["それにもかかわらず"], stems=["にもかかわらず"]),
 _P("nagaramo", "〜ながらも", "hypothesis", "N2", "↔️",
    "虽然…却…", "ながら（も）（逆接）", "同时容纳两个相反面",
    ans=["日本の将来を見据えながら"], stems=["ながらも"]),
 _P("nikagirazu", "〜に限らず", "particle", "N2", "➕",
    "不限于…", "に限らず（范围扩大）", "把范围从特例推广到全体",
    ans=["なるかどうかに限らず"], stems=["に限らず"]),
 _P("wotowazu", "〜を問わず", "particle", "N2", "🔓",
    "不论…", "を問わず（不问条件）", "把条件排除在外",
    ans=[], stems=["を問わず"]),
 _P("nimegutte", "〜をめぐって", "particle", "N2", "🌀",
    "围绕…", "をめぐって（争论焦点）", "把争议圈在一个议题上",
    ans=[], stems=["をめぐって"]),
 _P("nioujite", "〜に応じて", "particle", "N2", "🎚️",
    "根据…相应", "に応じて（对应变化）", "按条件调整对策",
    ans=[], stems=["に応じて"]),
 _P("nisotte", "〜に沿って", "particle", "N2", "🛤️",
    "沿着…、按照…", "に沿って（顺着）", "顺着既定方向/方针推进",
    ans=[], stems=["に沿って"]),
 _P("nihanshite", "〜に反して", "particle", "N2", "↩️",
    "与…相反", "に反して（违反预期）", "现实与期待反向而行",
    ans=[], stems=["に反して"]),
 _P("nikagiru", "〜に限る", "particle", "N2", "🏆",
    "最好是…", "に限る（最佳选择）", "把某方案判为最优",
    ans=[], stems=["に限る"]),
 _P("nikonoshita", "〜に越したことはない", "modality", "N2", "👍",
    "最好是…", "に越したことはない", "把建议说到「没有更好的」",
    ans=[], stems=["に越したことはない"]),
 _P("madenaku", "〜までもなく", "particle", "N2", "➖",
    "不必…、用不着…", "までもなく（无需）", "把前提条件判定为多余",
    ans=["までも"], stems=["までも"]),
 _P("tokorowo", "〜ところを", "particle", "N2", "🙏",
    "（本应…）却…", "ところを（逆接，常带感谢/道歉）", "把本该如此的场面转折",
    ans=["言うべきところを"], stems=["ところを"]),
 _P("tokorodatta", "〜ところだった", "modality", "N2", "😰",
    "差点就…了", "ところだった（险些）", "把「幸免于难」说出来",
    ans=["見逃す", "させられるところだった"], stems=["ところだった", "ところでした"]),
 _P("monodakara", "〜ものだから", "modality", "N2", "🙇",
    "因为…（辩解）", "ものだから（主观理由）", "拿理由当借口/解释",
    ans=[], stems=["ものだから"]),
 _P("kotokara", "〜ことから", "particle", "N2", "🔎",
    "由于…（判断依据）", "ことから（根据）", "把事实作为推理的根据",
    ans=["しきれなくなったことから"], stems=["ことから"]),
 _P("karaniwa", "〜からには", "modality", "N2", "🔥",
    "既然…就…", "からには（既定条件）", "既已如此，责无旁贷",
    ans=["おいでいただくからには"], stems=["からには"]),
 _P("ijou", "〜以上（は）", "modality", "N2", "⚖️",
    "既然…就…", "以上は（既定条件）", "把已成立的事设为义务前提",
    ans=["お出しする以上"], stems=["以上"]),
 _P("mosarukotonagara", "〜もさることながら", "emphasis", "N1", "🥇",
    "…自不必说，更…", "もさることながら", "先肯定次要，再突出主要",
    ans=["機能性もさることながら"], stems=["もさることながら"]),
 _P("dokoroka", "〜どころか", "emphasis", "N2", "🙃",
    "岂止…反而…", "どころか（否定并升级）", "先否定小程度，再推向反面",
    ans=[], stems=["どころか"]),
 _P("haoroka", "〜はおろか", "emphasis", "N1", "⛔",
    "别说…就连…", "はおろか（极端举例）", "把最不可能的也纳入",
    ans=[], stems=["はおろか"]),
 _P("kkonai", "〜っこない", "modality", "N2", "🙅",
    "绝不可能…", "っこない（口语强否定）", "一口咬定做不到",
    ans=[], stems=["っこない"]),
 _P("wakeganai", "〜わけがない", "modality", "N2", "🚫",
    "不可能…", "わけがない（逻辑否定）", "从道理上断然否定",
    ans=["取らずに済むわけがない"], stems=["わけがない"]),
 _P("hazuganai", "〜はずがない", "modality", "N3", "🚫",
    "不可能…", "はずがない（推断否定）", "从预期上否认可能性",
    ans=[], stems=["はずがない"]),
 _P("bekida", "〜べきだ／べきではない", "modality", "N3", "📏",
    "应该…／不应该…", "べき（义务）", "以社会规范下判断",
    ans=["べきではないのでしょうか"], stems=["べきでは"]),
 _P("mai", "〜まい", "modality", "N2", "🙅‍♂️",
    "不会…吧；绝不…", "まい（否定推量/意志）", "用文言否定收束",
    ans=["あるまい", "しかあるまい", "でもあるまい"], stems=[]),
 _P("niitaru", "〜に至る／に至るまで", "particle", "N2", "🪜",
    "达到…；甚至…", "に至る（到达）", "把过程推进到某个终点",
    ans=[], stems=["に至"]),
 _P("niwatatte", "〜にわたって", "particle", "N2", "📅",
    "历经…、跨越…", "にわたって（时间/范围跨度）", "强调范围之广、时间之长",
    ans=[], stems=["にわたって", "にわたり"]),
 _P("wotsuujite", "〜を通じて／を通して", "particle", "N2", "🌉",
    "通过…、经由…", "を通じて（媒介）", "把手段/期间作为通道",
    ans=[], stems=["を通じ", "を通して"]),
 _P("wohajime", "〜をはじめ", "particle", "N2", "🥇",
    "以…为首", "をはじめ（代表例）", "举出代表，暗示其余",
    ans=[], stems=["をはじめ"]),
 _P("wochuushinni", "〜を中心に", "particle", "N2", "🎯",
    "以…为中心", "を中心に（核心）", "把核心范围圈出来",
    ans=[], stems=["を中心に"]),
 _P("woyoginakusareru", "〜を余儀なくされる", "particle", "N1", "😣",
    "被迫…", "を余儀なくされる（被迫）", "把被迫的决定说成客观结果",
    ans=["を"], stems=["余儀なく"]),
 _P("wokinjienai", "〜を禁じ得ない", "modality", "N1", "😢",
    "不禁…（情感）", "を禁じ得ない", "感情无法压抑",
    ans=[], stems=["禁じ得ない"]),
 _P("womonotomosezu", "〜をものともせず", "particle", "N1", "💪",
    "不把…当回事", "をものともせず（无视困难）", "把困难视为无物",
    ans=[], stems=["をものともせず"]),
 _P("wokagirini", "〜を限りに", "particle", "N1", "🏁",
    "以…为最后界限", "を限りに（终结）", "把某点设为终止线",
    ans=[], stems=["を限りに"]),
 _P("kaneru", "〜かねる", "modality", "N2", "🙏",
    "难以…（委婉拒绝）", "ます形＋かねる", "用「难以」委婉推辞",
    ans=["いたしかねます"], stems=["かね"]),
 _P("tsutsumo", "〜つつ（も）", "hypothesis", "N2", "↔️",
    "虽然…却…；一边…一边…", "つつ（も）（逆接/同时）", "让两个动作或矛盾并存",
    ans=[], stems=["つつも"]),
 _P("deare", "〜であれ", "hypothesis", "N1", "♾️",
    "无论是…", "であれ（让步）", "列举可能性，结论不变",
    ans=[], stems=["であれ"]),
 _P("yainaya", "〜や否や／が早いか／そばから", "particle", "N1", "⚡",
    "刚一…就…", "や否や／が早いか／そばから", "强调几乎同时发生",
    ans=[], stems=["や否や", "が早いか", "そばから"]),
 _P("youganai", "〜ようがない／ようもない", "modality", "N2", "🙈",
    "无法…、无从…", "ようがない（方法不存在）", "强调「想也没办法」",
    ans=["疑いようがないものの", "きれいにしようがないくらい"], stems=["ようがない"]),
 _P("youninaru", "〜ようになる／ようにする", "modality", "N3", "🔄",
    "变得…；设法…", "ようになる／ようにする（变化/努力）", "描述能力或习惯的变化",
    ans=["ようになるぐらいだ"], stems=["ようになる"]),
 _P("tsumori", "〜つもり", "modality", "N3", "🧠",
    "打算…；自认为…", "つもり（意图/信念）", "陈述主观意图或错觉",
    ans=["知っているつもりの", "洗ったつもりでも", "泣かせるつもりはなかった"], stems=["つもり"]),
 _P("tara", "〜たら／でもしたら", "hypothesis", "N4", "❓",
    "如果…的话", "たら（假定）", "假设一种情况，推出后果",
    ans=["刺されでもしたら"], stems=["でもしたら"]),
 _P("toshiteha", "〜としては／にとっては", "particle", "N3", "🎓",
    "作为…；对…来说", "としては／にとっては（立场）", "从某人立场出发",
    ans=["私としては"], stems=["としては"]),
 _P("toiukoto", "〜ということ", "particle", "N3", "💬",
    "…这件事", "ということ（名词化）", "把句子打包成名词",
    ans=["食べるということが"], stems=["ということが"]),
 _P("nishitemireba", "〜にしてみれば／にとって", "particle", "N2", "👤",
    "在…看来", "にしてみれば／にとって（立场）", "切换到当事人的视角",
    ans=["にしてみれば", "わたしにとっては", "にとっても"], stems=["にしてみれば", "にとって"]),
 _P("toshitemo", "〜としても", "hypothesis", "N2", "🌧️",
    "即使…也…", "としても（让步）", "退一步承认，结论不变",
    ans=["くらいはいいとしても"], stems=["としても"]),
 _P("toiu", "〜という", "particle", "N4", "💬",
    "叫做…；所谓…", "という（称谓/内容）", "给事物贴上名称或内容",
    ans=["という", "自分は花粉症だという"], stems=[]),
 _P("kke", "〜っけ", "modality", "N3", "❔",
    "（是不是）…来着", "っけ（确认/回忆）", "向对方确认记忆",
    ans=["書くんだっけ"], stems=["っけ"]),
 _P("mon", "〜もん／もの", "modality", "N3", "🥺",
    "因为…嘛（辩解）", "もん（口语理由）", "带撒娇/辩解的理由",
    ans=["読めるんだもん"], stems=["もん"]),
 _P("amari", "〜あまり／あまりに", "emphasis", "N2", "🌊",
    "因过于…而…", "あまり（过度原因）", "把原因归到「太过」",
    ans=["あまりに"], stems=["あまりに"]),
 _P("hodo", "〜ほど", "emphasis", "N3", "📏",
    "到…程度", "ほど（程度）", "用程度衡量",
    ans=["迷うほど", "見きれないほどの"], stems=[]),
 _P("gurai", "〜ぐらい", "emphasis", "N3", "📏",
    "大约…；到…程度", "ぐらい（程度/轻蔑）", "把程度说小或说大",
    ans=["立ち上がれそうにないぐらい", "降るんじゃないかというぐらい"], stems=[]),
 _P("nante", "〜なんて", "emphasis", "N3", "😮",
    "…之类的（轻视/惊讶）", "なんて（轻视/感叹）", "带情绪地举例",
    ans=["睡眠の質があるなんて"], stems=["なんて"]),
 _P("shimatsu", "〜始末だ", "modality", "N2", "😩",
    "（落得）…下场", "始末だ（坏结果收尾）", "把一连串坏事的结局说出来",
    ans=["始末だった"], stems=["始末"]),
 _P("ppanashi", "〜っぱなし", "modality", "N2", "📌",
    "一直…着（放任）", "っぱなし（放置）", "强调状态被搁置不管",
    ans=["入れっぱなしだった"], stems=["っぱなし"]),
 _P("kiri", "〜っきり", "modality", "N2", "✂️",
    "自从…就再没…", "っきり（最后一次）", "把某次设为终止点",
    ans=["それっきりだから"], stems=["っきり"]),
 _P("kagiranai", "〜とは限らない", "modality", "N2", "⚖️",
    "未必…", "とは限らない（部分否定）", "承认例外存在",
    ans=["ならないともかぎらない"], stems=["とは限らない", "ともかぎらない"]),
 _P("ttatte", "〜ったって", "modality", "N2", "🗣️",
    "即使说…也…（口语）", "ったって（口语让步）", "口语里的「虽说…」",
    ans=["探すったって"], stems=["ったって"]),
 _P("noni", "〜のに", "modality", "N3", "😔",
    "明明…却…（遗憾）", "のに（逆接+遗憾）", "带惋惜的逆接",
    ans=["くれればよかったのに"], stems=["のに"]),
 _P("zuni", "〜ずに", "particle", "N3", "🚶",
    "不…就…", "ない形＋ずに（＝ないで）", "把未做的动作作为前提",
    ans=["買えずにいた"], stems=["ずに"]),
 _P("nikumo", "〜ようにも〜ない", "modality", "N2", "🤐",
    "想…也…不了", "ようにも＋否定", "把意愿与能力对立",
    ans=["聞こうにも"], stems=["ようにも"]),
 _P("nimonatte", "〜にもなって", "modality", "N2", "😳",
    "都到…了还…", "にもなって（责备）", "责备对方不成器",
    ans=["にもなっておらず"], stems=["にもなって"]),
 _P("tari", "〜たり〜たり", "particle", "N4", "🔀",
    "又是…又是…", "たり（列举）", "列举反复出现的动作",
    ans=["来たり来なかったり"], stems=["たり"]),
 _P("toiukotoda", "〜というものだ", "modality", "N2", "📖",
    "才叫…；这就是…", "というものだ（本质判断）", "下本质性结论",
    ans=[], stems=["というものだ"]),
 _P("tomokaku", "〜ともかく／はともかく", "modality", "N2", "🙂",
    "姑且不论…", "ともかく（搁置）", "先把某点放一边",
    ans=["ともかく", "なるかどうかはともかく"], stems=["ともかく"]),
 _P("kke2", "〜とかで", "modality", "N2", "🗣️",
    "据说因为…", "とかで（传闻理由）", "用传闻解释现状",
    ans=["とかで"], stems=["とかで"]),
 _P("tokorodaga", "〜ところだが", "modality", "N2", "↔️",
    "本应…但…", "ところだが（让步）", "承认本该如此，再转",
    ans=["押さえたいところだが"], stems=["ところだが"]),
 _P("toha", "〜とは", "emphasis", "N2", "😮",
    "竟然…（惊讶）", "とは（感叹/意外）", "对意外事实表示惊讶",
    ans=["解けるとは"], stems=["とは"]),
 _P("tehanaranai", "〜てはならない", "modality", "N2", "🚫",
    "不可以…", "てはならない（禁止）", "以规范禁止某事",
    ans=["があってはならない"], stems=["てはならない"]),
 _P("kamoshirenai", "〜かもしれない", "modality", "N4", "❓",
    "也许…", "かもしれない（推测）", "保留可能性的推测",
    ans=["なっていたかもしれない"], stems=["かもしれない"]),
 _P("nadoniyori", "〜などにより", "particle", "N2", "🔗",
    "由于…等", "などにより（原因列举）", "列举原因",
    ans=["近年の人手不足などにより"], stems=["などにより"]),
 _P("ippoude", "〜一方で", "modality", "N2", "↔️",
    "另一方面…", "一方で（对比）", "并列两个对立面",
    ans=["一方で"], stems=["一方で"]),
 _P("tabini", "〜たびに", "particle", "N3", "🔁",
    "每当…就…", "たびに（每次）", "把动作与结果绑定",
    ans=[], stems=["たびに"]),
 _P("bakari", "〜ばかりだ／一方だ", "modality", "N2", "📈",
    "一味地…", "ばかりだ／一方だ（单向变化）", "强调只往一个方向变",
    ans=[], stems=["ばかりだ"]),
 _P("kotoninaru", "〜ことになっている", "modality", "N3", "📜",
    "（规定）要…", "ことになっている（既定规则）", "陈述制度性安排",
    ans=[], stems=["ことになっている"]),
]
CATALOG.extend(EXTRA)

GROUP_DEFS = [
 dict(id="hypothesis", name="A 仮定・譲歩", color="#2563a8", emoji="🌧️",
      note="把前项推到极端或设为假定，声明后项结论不变。代表：たとえ〜ても／どんなに〜ても／いくら〜ても。"),
 dict(id="negation", name="B 全面否定", color="#d33f49", emoji="🚫",
      note="前项副词预告「彻底否定」，后项必须是ない／ず。代表：決して／いっさい／なんら／必ずしも〜ない。"),
 dict(id="conjecture", name="C 推量・様態", color="#6a3de8", emoji="🔮",
      note="前项副词提示推断或比况，后项用ようだ／らしい／かのようだ收束。代表：どうやら／はたして／まるで／あたかも。"),
 dict(id="emphasis", name="D 強調・程度", color="#b8860b", emoji="❗",
      note="前项限定或感叹，后项以だけ／のみ／ことか呼应。代表：せめて〜だけでも／なんと〜ことか。"),
 dict(id="modality", name="E 文末モダリティ", color="#188a52", emoji="🔒",
      note="前半给出条件/推量，后半用固定述语收尾。代表：に違いない／はずだ／おそれがある／にほかならない。"),
 dict(id="particle", name="F 助詞系文型", color="#e67e22", emoji="🧩",
      note="以助词为核心的固定搭配。代表：を機に／を受けて／をもって／ならではの／とあって。"),
 dict(id="other", name="G その他（文型・敬語・語彙）", color="#5b6478", emoji="📦",
      note="不属上述呼応类型，或为敬語・語彙・その他文型；保留在真题総覧中备查。"),
]


def parse_nadeshiko(path):
    """Parse analysis/<session>-<type>.md → {question_number: [clip,...]}."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"\n## Q(\d+)", text)
    out = {}
    for i in range(1, len(parts), 2):
        num = int(parts[i])
        body = parts[i + 1]
        # cut the 作品台词 block
        m = re.search(r"\*\*作品台词\*\*：?\n(.*?)(?=\n- \*\*|\n---|\Z)", body, re.S)
        if not m:
            continue
        clips = []
        lines = m.group(1).split("\n")
        cur = None
        for ln in lines:
            jp = re.match(r"^\s{2}- (.+)$", ln)
            if jp:
                if cur:
                    clips.append(cur)
                cur = {"jp": jp.group(1).strip(), "en": "", "media": "", "ep": "", "at": "", "url": "", "audio": "", "thumb": "", "cn": ""}
                continue
            if cur is None:
                continue
            d = re.match(r"^\s{4}- EN:\s*(.+)$", ln)
            if d:
                cur["en"] = d.group(1).strip(); continue
            d = re.match(r"^\s{4}- ≪(.+?)≫\s*(.*)$", ln)
            if d:
                cur["media"] = d.group(1).strip()
                rest = d.group(2)
                ep = re.search(r"(EP\S+)\s*@\s*(\S+)", rest)
                if ep:
                    cur["ep"], cur["at"] = ep.group(1), ep.group(2)
                u = re.search(r"(https?://\S+)", rest)
                if u:
                    cur["url"] = u.group(1)
                continue
            d = re.match(r"^\s{4}- (https?://\S+)\s+🖼\s+(https?://\S+)", ln)
            if d:
                cur["audio"], cur["thumb"] = d.group(1), d.group(2)
                continue
        if cur:
            clips.append(cur)
        out[num] = [c for c in clips if c.get("audio")]
    return out


def main():
    qs = []
    for f in sorted(glob.glob(str(BANK / "past-exams/*/*/grammar/*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        opts = d["options"]
        a = str(d["answer"])
        ai = int(a) if a.isdigit() else None
        at = opts[ai - 1] if ai and 1 <= ai <= len(opts) else ""
        qs.append(dict(
            id=d["id"], year=d["year"], month=d["month"], type=d["type"],
            number=d["number"], question=d["question"], options=opts,
            answer=a, answer_index=ai, answer_text=at, tags=d.get("tags", []),
            source=d.get("source", ""), verified=d.get("verified", {}).get("status", ""),
        ))

    # assign each choice/composition question to a pattern (first match wins)
    for it in CATALOG:
        it["_ans"] = {norm(x) for x in it["ans"]}
        it["exams"] = []
    for q in qs:
        if q["type"] == "passage":
            continue
        a = norm(q["answer_text"])
        stem = q["question"]
        assigned = None
        for it in CATALOG:
            if a and a in it["_ans"]:
                assigned = it
                break
            if any(s in stem for s in it["stems"]):
                assigned = it
                break
        if assigned:
            assigned["exams"].append(q)
            q["_item_id"] = assigned["id"]

    # attach Nadeshiko clips parsed from the bank's analysis/*.md (session × Q#)
    qid2item = {}
    for it in CATALOG:
        for q in it["exams"]:
            qid2item[q["id"]] = it
    nade_by_item = {}
    for sess_md in sorted((BANK / "analysis").glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-grammar-choice.md")):
        sess = sess_md.name[:7]
        year, month = int(sess[:4]), int(sess[5:7])
        for kind, md in (("choice", sess_md),
                         ("composition", BANK / "analysis" / f"{sess}-composition.md")):
            for num, clips in parse_nadeshiko(md).items():
                qid = f"{year}-{month:02d}-grammar-{kind}-{num:02d}"
                it = qid2item.get(qid)
                if it:
                    nade_by_item.setdefault(it["id"], []).extend(clips)

    # build items
    items = []
    for it in CATALOG:
        exams = it.pop("exams")
        it.pop("_ans")
        exams.sort(key=lambda q: (q["year"], q["month"], q["number"]))
        # examples = filled choice stems (for TTS / 例文)
        examples = []
        for q in exams:
            if q["type"] != "choice":
                continue
            filled = q["question"]
            # replace the blank parentheses with the answer
            for bl in ["（　）", "（　　　）", "(　)", "（ ）", "（    ）", "（  ）"]:
                if bl in filled:
                    filled = filled.replace(bl, f"【{q['answer_text']}】", 1)
                    break
            examples.append(dict(jp=filled, cn=f"真题 {q['year']}-{q['month']:02d} 問題5 Q{q['number']}",
                                 src="jlpt", qid=q["id"], year=q["year"], month=q["month"],
                                 number=q["number"], answer=q["answer_text"]))
        it["examples"] = examples
        # nadeshiko clips (dedupe by url, cap 3)
        seen_url = set()
        nade = []
        for c in nade_by_item.get(it["id"], []):
            if c["url"] in seen_url:
                continue
            seen_url.add(c["url"])
            nade.append(c)
        it["nadeshiko"] = nade[:2]
        it["exam_count"] = len(exams)
        it["exam_ids"] = [q["id"] for q in exams]
        items.append(it)

    # exhaustive exam list (choice + composition + passage)
    exams_all = []
    for q in sorted(qs, key=lambda q: (q["year"], q["month"], q["number"])):
        it = qid2item.get(q["id"])
        exams_all.append(dict(
            id=q["id"], year=q["year"], month=q["month"], type=q["type"],
            section=SECTION_NAME[q["type"]], number=q["number"],
            stem=q["question"], options=q["options"], answer=q["answer"],
            answer_index=q["answer_index"], answer_text=q["answer_text"],
            source=q["source"], verified=q["verified"],
            pattern=(it["word"] if it else ""),
            group=(it["group"] if it else "other"),
            item_id=(it["id"] if it else ""),
        ))

    groups = GROUP_DEFS
    meta = dict(
        title="呼応・搭配 完全体系", titleCn="JLPT N1 真题呼応・搭配総覧（2010-07 ～ 2025-07）",
        jp="こおう", tags=["N1", "文法", "呼応", "搭配", "過去問", "2010-2025"],
        voiceFemale="ja-JP-NanamiNeural", voiceMale="ja-JP-KeitaNeural",
        quizBanks=[["exam", "🎯 真题填空"], ["recog", "📘 意味認識"],
                   ["engine", "🔧 呼応拆解"], ["listen", "🎧 聴解判別"],
                   ["judge", "⭕ 判断正誤"]],
    )

    data = dict(meta=meta, groups=groups, items=items, exams=exams_all)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # report
    print(f"wrote {OUT} ({OUT.stat().st_size//1024} KB)")
    print(f"items={len(items)}  exams_all={len(exams_all)}")
    from collections import Counter
    c = Counter(it["group"] for it in items)
    for g in groups:
        n = c.get(g["id"], 0)
        ne = sum(it["exam_count"] for it in items if it["group"] == g["id"])
        print(f"  {g['id']:11} items={n:3d} attached_exams={ne:3d}")
    attached = sum(it["exam_count"] for it in items)
    print(f"attached exams (choice+composition) = {attached}")
    # unmatched choice/composition
    matched_ids = {q["id"] for it in items for q in []}
    all_cc = [q for q in exams_all if q["type"] in ("choice", "composition")]
    print(f"choice+composition total = {len(all_cc)}")


if __name__ == "__main__":
    main()
