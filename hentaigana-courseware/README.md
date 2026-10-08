# 変体仮名店招帖（変体仮名・老铺招牌帖）

へんたいがな ・ てんしょうちょう ／ 暖簾上的「鬼画符」，其实是假名。

专门收集**用変体仮名（异体假名）写店名的老铺招牌**：蕎麦屋暖簾的「幾楚者」＝きそば、
「楚者゛」＝そば、甘味処的「志る古」＝しるこ、花札的「あかよろし」、砂場系老铺的
「す奈場」＝すなば、だんご屋的「団古」＝だんご、鰻屋的「宇奈岐」＝うなぎ。

成品是单文件 `index.html`（约 3.4MB，内嵌全部实拍照片/字形图与 12 段 TTS 发音，离线可用）。

## 打开

浏览器直接打开 `index.html`，或起本地服务：

```bash
python3 -m http.server 8642   # 然后访问 http://localhost:8642/hentaigana-courseware/
```

## 内容结构（四个页签）

| 页签 | 内容 |
|---|---|
| 🏮 看板帖 | 7 块招牌的逐字解码卡（幾楚者・楚者゛・志る古・あ可よろし・す奈場・団古・宇奈岐）＋读音＋参考链接＋「幾 vs 生」对照图；照片为实拍（各卡注明出处） |
| 🗺️ しくみ | 変体仮名入门（含「不是变态假名」澄清）＋使い分けの四原則（語頭/語中・特定词固定・音価/同音回避）＋23 枚字母卡（変体＝真实 Unicode 字形，現行＝Noto Serif CJK 排印的本字） |
| 📜 ものがたり | 平安女手 → 1900 小学令 → 1948 戸籍 → 2017 Unicode 的时间线，＋8 条豆知识（言海・漱石原稿・花札・角萬…） |
| 🎯 クイズ | 51 問五层题库：🏮看板・🔤字母・✍️穴埋め・🤔常識・🎧聴解（音频），错题本 localStorage（`hentaigana-wrong`） |

## 增长闭环：加招牌 → 重建

### ① 加字母（letters）

往 `hentaigana.json` 的 `letters[]` 丢一条，并把字形图放进 `assets/`：

```json
{ "letter": "支", "kana": "き", "kind": "変体", "img": "letter_ki3.png", "note": "き的另一分身。" }
```

字形图来源优先级：① Wikimedia Commons 的 Unicode 変体仮名 SVG（`Hentaigana letter XX.svg`＝
APL/BabelStone，`Hiragana XX 01.svg`＝CC0）→ 转 PNG 后用 `PIL` 白字合成到靛蓝卡；
② 現行（本字）用 Noto Serif CJK 排印；③ 必要时用 Yuji Syuku 毛笔字体补（见 `tools/`）。

### ② 加招牌（signs）

往 `signs[]` 丢一条（`img` 放 `assets/` 里的实拍照片或实字形复原图，`signText` 用原字表记、
`audio` 引用 `audio[]` 的 id）。重建即自动生成看板卡与看板/聴解题目。

### ③ 重建

```bash
python3 build.py    # 重算题目（固定 seed）+ 补录新文本 TTS + 写 index.html
```

## 工具链

- **图片**：`assets/` 直接入库（PNG 用于字形/复原图、JPEG 用于实拍照片；`build.py` 均已支持）。
  初代模拟图生成器 `tools/gen_signs.py`（PIL + Yuji Syuku）已退役，保留作字形合成参考。
- **发音**：edge-tts（ja-JP-NanamiNeural，-6%），按文本哈希缓存到 `audio/`（内容寻址、可提交）；
  改文本会自动重录，失败只跳过。
- **题目**：`build.py` 内 `build_questions()` 按固定 seed 抽干扰项，可复现。

## 来源与版权说明

本课件为个人非商业学习用途，所有素材均在卡片内或本表注明出处；如有权利人认为不妥，请提 issue 处理。

### 实拍照片

| 素材 | 出处 | 许可 |
|---|---|---|
| 幾楚者暖簾（首页大图・kisoba 卡） | 筆者撮影 | 本人拍摄，随课件公开 |
| 巴屋総本店「楚者゛」暖簾（soba 卡） | [fv1.jp 記事写真（2011）](https://fv1.jp/wp-content/uploads/2011/08/巴屋総本店.jpg) | 学习引用，注明出处 |
| 「南千住砂場」店舗（す奈場 卡） | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Minami_senju_Sunaba_IMG_8496r_20151113.jpg)・Ogiyoshisan 撮影 | CC BY-SA 4.0 |
| 「う奈ぎ」暖簾（宇奈岐 卡） | [tenki.jp「変体仮名って？」（2018）](https://tenki.jp/suppl/hiroyuki_koga/2018/03/21/27954.html) | 学习引用，注明出处 |
| 江戸後期・中期の手描き花札（あ可よろし 卡） | [Wikimedia Commons (late)](https://commons.wikimedia.org/wiki/File:Hand-painted_hanafuda_-_late_Edo_period.jpg) / [mid](https://commons.wikimedia.org/wiki/File:Hand-painted_hanafuda_-_mid-Edo_period.jpg) | Public Domain |
| 智永「真草千字文」草書「幾」（幾 vs 生 对照图右） | 古典法帖（7世紀・PD）の図版 | Public Domain（原迹） |
| 『摂津名所図会』砂場いづみや図（す奈場 卡附图） | [そば用語辞典（そばの散歩道）](https://www.eonet.ne.jp/~sobakiri/1-1.html) 掲載の1798年刊本図版 | Public Domain（原書） |

### 字形图（字母卡・复原暖簾）

| 素材 | 出处 | 许可 |
|---|---|---|
| `Hentaigana letter *.svg`（幾・楚・所・八・之・介・徒・地・盤・奈・宇 等） | Wikimedia Commons by BabelStone | Arphic Public License (APL) |
| `Hiragana SI/KO/KA/YU/HA 01.svg`（志・古・可・由・者） | Wikimedia Commons by Ocdp | CC0 |
| 現行（本字）字母 曽・波・加・己・知・川・安・奈・宇 等 | Noto Serif CJK JP 排印 | SIL OFL 1.1 |
| 団・る 等の補助字形 | Yuji Syuku 毛笔字体 | SIL OFL 1.1 |

### 史实与例证

- 日文维基百科「変体仮名」条目（1900 年小学校令施行規則・1908/1922 经緯・言海・花札
  あかよろし・しるこ 志る古 等）
- そば用語辞典「す奈バ」（大坂・砂場の暖簾とそば猪口の「す奈場／す奈バ」表記）
- 江戸そばの源流「のれん御三家」（摂津名所図会の砂場いづみや図）
- tenki.jp「街歩きの途中で見かける不思議な謎の文字──変体仮名って？」（団古・うなぎのれんの「奈」）
- 久保井インキ社員ブログ「幾楚者？」、おくる言葉「そばを漢字で書くと？」
- 各卡片均附原文链接，仅作个人学习之非商业性引用。

### 其他

- **发音**由本地 edge-tts（微软 Azure 神经语音）合成，非录音素材。
