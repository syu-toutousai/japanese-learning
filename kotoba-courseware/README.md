# 言葉・新詞 記憶の種（kotoba-courseware）

一个新的词，第一次碰上时随手就忘——所以这里给**每个刚碰到的新词**铺一条
**人脑记得住**的学习路径，做成单文件课件：

```
初见 → 语义网络 → 辨析 → 提取（闯关） → 间隔复习
              └ 台词·画面（Nadeshiko 番剧原声+原画）
```

当前词条：**風情（ふぜい）** ・ **開く・あく（🕳️）** ・ **はしゃぐ（🎉）** ・ **逞しい・たくましい（💪）** —— 顶部可切换任一新词，各自独立初见→语义网络→辨析→提取→间隔复习。

成品是单文件 `index.html`（约 4.1MB，内嵌全部发音 + 番剧台词原声与画面，离线可用），数据在 `kotoba.json`。

## 打开

```bash
python3 -m http.server 8642   # 然后访问 http://localhost:8642/kotoba-courseware/
```

或浏览器直接双击 `index.html`。

## 认知设计（每一层对应一个人脑机制）

| 模块 | 对应认知规律 |
|---|---|
| 🧊 **初遇场景卡** | **情景记忆**（episodic memory）——把生词钉在「你在哪、听见什么、对方什么反应」上，是最强的记忆钩子；先给“我当时遇见的原句”，再给核心意象 |
| 🌬️ **核心意象（一个原型+拆字）** | **原型义 / core-image**——一句话讲透多义：風情＝「风土把情捎给你，心头一动」。四个义项都由它长出来，不死记硬背 |
| 🌐 **语义网络（义项星图）** | **语义网络 / concept map + 双重编码**——义项从核心辐射、共用读音字形，视觉+文字双通道 |
| 🎬 **台词·画面** | **真实语料锚定（authentic contexts）**——Nadeshiko 番剧原声台词一句 + 当时画面一帧，真实语速・真实语气，直接听、看着画面记；每片段都用义项标签（点它跳回语义网络）**和单词义项挂钩** |
| 🔤 **字源・连浊卡** | **精细加工（elaboration）**——風＋情、ふ＋せい→ふぜい（连浊）、《方丈記》用例：用“为什么这样读/写”加深编码 |
| 🧂 **近义辨析场** | **对比学习 / discrimination**——風情↔趣↔情緒↔風流↔風味 + 対義「殺風景」成组呈现，近义词“成对”才记得牢 |
| 🎯 **五关提取** | **提取练习 engineering**：📘認識（识别）→ ✍️**産出填空**（自己敲出读音/汉字，**生成效应**，最强）→ 🎧听解（音形绑定）→ 🧩辨析 → ⭐综合；全部随机打乱+**交错** |
| 📕 **错题本** | **错误驱动学习**（error-driven）——答错自动收进，做对即移出 |
| 🔁 **间隔复习** | **间隔效应 / spaced repetition**——锚定初见日，+1/+3/+7/+14/+30 天定点提取，到点先回想再看卡 |

## 内容（当前词条）

- **風情（ふぜい）**：初见原句「風情がないな。」（桜・「まあ 春だから。」的吐槽现场）
  - 核心意象 1 个 ＋ 义项 4 个（情趣・雅趣 / 模样・样子 / 款待（古典谦辞）/ 接尾・……之流）
  - 语源・连浊卡 1 张 ＋ 近义辨析 5 词 ＋ 记忆锚 1 条
- **開く・あく（🕳️）**：初见原句「穴あくまでパンツはくなよ。」（磨出洞）
  - 核心意象「封闭的东西裂出一道口」＋ 义项 4 个（出现洞口 / 门・嘴打开 / 开业 / 开幕）
  - 强调 あく（自己裂开）↔ ひらく（人为打开）的对立，辨析 空く・破ける 等
- **はしゃぐ（🎉）**：初见原句「子どものはしゃぐ声。」（欢闹嬉闹）
  - 核心意象「得意忘形地闹」＋ 义项 2 个（欢闹嬉闹 / 风干・干燥〈本义〉）
  - 拆「燥」字源（本义风干→引申亢奋），辨析 騒ぐ・浮かれる 等
- **逞しい・たくましい（💪）**：初见原句「たくましいですね～。」（真是太健壮了）
  - 核心意象「从身体里透出来的强韧」＋ 义项 3 个（健壮结实 / 旺盛茁壮 / 顽强坚韧）
  - 拆「逞」字源（奔跑舒展→气力全撑开），辨析 強い・屈強・頑丈 等
- **发音 55 段**：单词・例句原声（MOJi/edge-tts：Nanami 女生 / Keita 男生）＋
  **Nadeshiko 台词・画面 15 段** 原声 MP3＋画面帧，见「🎬 台词·画面」页（風情 6 段 / あく 2 段 / はしゃぐ 3 段 /たくましい 4 段）
- **题库**：詞義認識 16 ・産出填空 8・聴解判別 4・辨析判別 11・自作 12 ＝ 51 問 ＋ 混合交错 ＋ 错题本
- **间隔复习**：每词独立锚点 + 6 个复习日清单（localStorage 按词分存）

## 数据字段（kotoba.json）

每个词条一条 `words[]`；课件支持**多词共存**，顶部可切换，各词进独立进度（localStorage 按 `koto-<id>-…` 分存）：

```json
{
  "id": "fuzei", "word": "風情", "read": "ふぜい", "accent": "①",
  "altRead": "ふうじょう（古典・音读）", "emoji": "🍂", "level": "N2・多義語",
  "firstSeen": "2026-08-29",
  "encounter": { "scene": "...", "line": "風情がないな。", "context": "...", "cn": "...", "take": "..." },
  "core":    { "title": "...", "def": "...", "split": "...", "en": "...", "image": "..." },
  "senses":  [ { "n": 1, "label": "...", "def": "...", "colloc": "...",
                 "examples": [{"jp": "...", "cn": "...", "moji": "s1"} ] } ],
  "etymology": { "title": "...", "text": "...", "key": "..." },
  "contrast": [ { "word": "趣　おもむき", "d": "...", "ex": "...", "cn": "..." } ],
  "javaKnife": "辨析场「一句话切分的刀」的整段 HTML 文案（每词可自写）",
  "anchor": "...",
  "nadeshiko": [ { "sid": "yPTUz7SbyTR7", "sense": 4, "media": "乙女ゲー…/Trapped…", "ep": 6, "at": "7:49",
                   "jp": "勘違いしないでよね 平民風情が!",
                   "en": "...", "cn": "..." } ],
  "quizzes": [ { "bank": "custom|recog|generate|listen|discrim", "type": "choice|judge|listen|type",
                 "q": "...", "opts": [...], "ans": 0, "ansTxt": [...] , "aid": "fuzei:s2", "exp": "..." } ]
}
```

- `examples[].moji` 指向 `audio-moji/` 里的 MOJi 原声（`"s1"`→`fuzei:s1`）；留 `null` 则自动用 edge-tts 合成
- `javaKnife` 是「🧂 辨析场」底部"一句话切分的刀"卡片的 HTML（无则留空）；原始的風情专属文案已迁到 風情 词条的 javaKnife
- `type: "type"` 的题要靠 `ansTxt` 接受答案（build.py 会自动给“输入读音”类的题生成假名/片假名/罗马音全套答案池）
- `nadeshiko[]`：把真实番剧台词语料放进课件——`jp` 写纯日文原文（无注音，靠原声音频记忆），`sense` 挂钩某个义项（页内标签点它跳语义网络），`media/ep/at` 标注出处时间；音频与画面由 **Nadeshiko CLI** 取回后放进 `scenes/`：`scenes/xxx.mp3`＋`scenes/xxx.webp`（`sid` 为 Nadeshiko 片段 id）
- 启动自动生成：每词自动长「詞義認識 1 問・産出填空 2 問・聴解判別 1 問」，其余靠上面 `quizzes[]` 手写补足

## 增长闭环（新增一个词）

```bash
nadeshiko search 風情 --once     # Nadeshiko 技能：找真实番剧台词（带 Ruby・EN・媒体/EP/时间）
nadeshiko segment <sid> --json   # 取片段，确认 CDN 音频/画面地址（若 q 有图）
curl -sO https://cdn.nadeshiko.co/media/<mediaId>/<ep>/<hash>.mp3      # → scenes/<sid>.mp3
curl -sO https://cdn.nadeshiko.co/media/<mediaId>/<ep>/<hash>.webp     # → scenes/<sid>.webp
moji 新词 --once            # 查释义与例句（MOJi 技能）
moji 新词 -a --out kotoba-courseware/audio-moji   # 把该词原声拉进 audio-moji/
```

- 把截图下来的释义、例句抄成一条 `words[]` 条目（core / senses / contrast / anchor / quizzes）
- 选了哪几句真实台词 → 填进 `nadeshiko[]`，音频/画面放进 `scenes/`（CDN 直链不受 API 配额限制）
- MOJi 原声文件名以「想用的逻辑id」写进 build.py 的 `MOJi` 表，或让例句 `moji` 字段留 `null` 全用 edge-tts
- `python3 build.py` → 刷新 —— 卡片・语义网络・台词·画面・题库・间隔复习自动长出来

同系列姊妹篇：`../keishiku-courseware/`（形式名詞）・`../shidai-courseware/`（次第）
・`../keiji-courseware/`（接辞口袋）・`../houi-courseware/`（方角・认知罗盘）。

## 来源与版权

- **風情**词条释义、例句及中文译文取自 **MOJi辞書**（mojidict.com），系通过其
  免登录接口以个人学习目的少量获取，著作权归 MOJi辞書 所有，仅供个人复习引用；
- 自写例句与初遇场景发音由本地 edge-tts（ja-JP-Nanami / ja-JP-Keita）合成；
- 「🎬 台词·画面」片段来自 **Nadeshiko**（nadeshiko.co）语料库，台词摘自相应动画（安達としまむら / 氷剣の魔術師が世界を統べる / ギルドの受付嬢… / 乙女ゲー世界はモブに厳しい世界です），原声与原画仅作个人语言学习引用，著作权归各版权方与 Nadeshiko 所有。