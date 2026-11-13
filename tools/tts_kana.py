#!/usr/bin/env python3
"""Sanitize Japanese text for edge-tts pronunciation.

edge-tts does not accept a user dictionary or phoneme SSML. Ruby/furigana is
display-only, so TTS still receives the raw kanji text. This module rewrites
known high-risk kanji words to hiragana before synthesis.

Use only for TTS input; never mutate the display text or quiz data.
"""

EDGE_TTS_KANA_MAP = {
    "心許なかった": "こころもとなかった",
    "心許なく": "こころもとなく",
    "心許ない": "こころもとない",
    "心許な": "こころもとな",
    "不束か": "ふつつか",
    "一入": "ひとしお",
    "相応しい": "ふさわしい",
    "相応し": "ふさわし",
    "清々しい": "すがすがしい",
    "清々し": "すがすがし",
    "煩わしい": "わずらわしい",
    "煩わし": "わずらわし",
    "健気": "けなげ",
    "無骨": "ぶこつ",
    "強ち": "あながち",
    "流石に": "さすがに",
    "流石": "さすが",
    "生憎": "あいにく",
    "如何に": "いかに",
    "兎に角": "とにかく",
    "中々": "なかなか",
    "疎か": "おろそか",
    "捗る": "はかどる",
    "捗っ": "はかどっ",
    "労わる": "いたわる",
    "労わっ": "いたわっ",
    "補う": "おぎなう",
    "補っ": "おぎなっ",
    "五月雨": "さみだれ",
    "今日は": "きょうは",
    "今年の": "ことしの",
    "その年の": "そのとしの",
    "割合": "わりあい",
    "大人でも": "おとなでも",
    "彼の": "かれの",
    "彼女": "かのじょ",
    "電話": "でんわ",
    "9千円": "きゅうせんえん",
    "1万円": "いちまんえん",
    "3校合わせて200人": "さんこうあわせてにひゃくにん",
    "一人では": "ひとりでは",
    "二人": "ふたり",
    "全て": "すべて",
    "現に": "げんに",
    "別に": "べつに",
    "行方": "ゆくえ",
    "15世紀": "じゅうごせいき",
}

_KEYS = sorted(EDGE_TTS_KANA_MAP, key=len, reverse=True)


def sanitize_for_edge_tts(text: str) -> str:
    for kanji in _KEYS:
        if kanji in text:
            text = text.replace(kanji, EDGE_TTS_KANA_MAP[kanji])
    return text


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(sanitize_for_edge_tts(arg))
