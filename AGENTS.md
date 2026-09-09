# AGENTS.md — 日本语课件制作守则

本文件为 japanese-learning 仓库的**课件制作手册**：记录「如何做一个新课件」的规则、
约定与坑。任何未来课件（courseware）都应遵循本守则，使增补与维护简单一致。

---

## 0. 仓库定位

个人日语学习资料库。每套课件 = 一个 `*-courseware/` 目录，产出**单文件、
自包含、离线可用**的 `index.html`（内嵌所有音频 base64、图片、数据、题库）。

现有课件：

| 目录 | 内容 | 数据源 |
|---|---|---|
| `keishiku-courseware/` | 形式名词十四杰 | 硬编码 Python |
| `shidai-courseware/` | 「次第」 | 硬编码 Python |
| `keiji-courseware/` | 接头接尾词口袋图鉴 | `pocket.json` |
| `houi-courseware/` | 方位罗盘 | `houi.json` |
| `kotoba-courseware/` | 新词记忆种子 | `kotoba.json` |
| `ni-courseware/` | 断定の「に」统一引擎（**最新范式**） | `ni.json` |

`ni-courseware/` 是最新、最完整的范式：**数据外置 JSON → pykakasi 振假名 → TTS 音声 →
Nadeshiko 原声 → 题库 → 单文件 HTML**。新课件照它抄。

---

## 1. 课件标准结构

```
<name>-courseware/
├── <name>.json        # 全部数据（词条/例句/场景/quiz 字段）
├── build.py           # 构建脚本（TEMPLATE + .replace 注入）
├── audio/             # edge-tts 合成 mp3 缓存（内容寻址，可提交）
├── nade_audio/        # Nadeshiko CDN 下载缓存（.gitignore 忽略，可重新下载）
├── scenes/            # （可选）预下载的番剧截图/原声，keishiku/kotoba 用
├── index.html         # 生成的单文件课件（脚本产物，需提交）
└── README.md          # 使用说明 + 增长闭环
```

**硬性约定**
- 构建入口必须是 `python3 build.py`，输出 `index.html`（脚本需保持可重复构建）。
- `ROOT = Path(__file__).parent`，一切路径相对此解析：
  ```python
  ROOT = Path(__file__).parent
  DATA = ROOT / "<name>.json"
  AUDIO_DIR = ROOT / "audio"
  OUT = ROOT / "index.html"
  ```
- 数据优先外置 JSON（不要硬编码在 build.py 里，便于 `git diff` 与增补）。
- 根目录 `index.html`（课件集索引页）要为每个新课件加一张卡片；`README.md` 目录表加一行。

---

## 2. 数据文件（<name>.json）约定

以 `ni.json` 为范式。

```jsonc
{
  "meta": { "title": "...", "titleCn": "...", "jp": "...",
            "tags": ["N5", ..., "文法体系"], "voiceFemale": "ja-JP-NanamiNeural" },
  "groups": [ { "id": "suru", "name": "する系 · 处理/认定", "color": "#e74c3c", "emoji": "⚙️", "note": "..." } ],
  "items": [
    {
      "id": "niYotte",             // 唯一 id，同时用作音频/题目 logical id 前缀
      "word": "〜によって",          // 展示用语法/词条
      "read": "によって",           // 读音（填空挖空用，须真正出现在例句句中）
      "group": "yoru",             // 必须是 groups 里存在的 id
      "level": "N3",
      "emoji": "🔧",
      "engine": "に（参数/手段）＋ よって（依赖路径）",   // 机制拆解
      "blueprint": "锁定一个参数，声明它是驱动下游变化的核心引擎", // 一句话蓝图
      "meaning": "根据…不同而不同；通过…手段；由于…",
      "examples": [
        { "jp": "国によって文化が違う。", "cn": "不同国家文化不同。", "src": "moji" }
      ],
      "nadeshiko": [               // （可选）真实番剧/日剧台词
        { "jp": "環境によって そして出会いによって人は無限に変わってゆく。",
          "en": "People change...", "cn": "人因环境而变，因相遇而无限改变。",
          "media": "There's No Freaking Way I'll Be Your Lover!", "ep": "EP12", "at": "18:24",
          "url": "https://nadeshiko.co/en/sentence/...",
          "audio": "https://cdn.nadeshiko.co/media/.../....mp3",
          "thumb": "https://cdn.nadeshiko.co/media/.../....webp" }
      ],
      "note": "..."               //（可选）💡 教学提示
    }
  ]
}
```

**每个 item 的例句 = 4 条**（原 2 条 + 1 条按句式套写 + 每条都带 `src` 来源徽标）：
- `src: "moji"` — 教科书例句（MOJi 来源，绿色徽标）
- `src: "nadeshiko"` — 真实台词（紫色徽标，放进 `nadeshiko[]` 数组而非 examples）

**Nadeshiko 每语法点至少 2 条**，且必须挑“规范用法”的台词，避开误命中（见 §5）。

---

## 3. 构建脚本（build.py）管道

顺序固定：

```
load_data() 校验 JSON →
gen_audio()  TTS 合成（音声→audio/，见 §4）→
gen_nade_audio() 下载 Nadeshiko CDN 音频（→nade_audio/，见 §5）→
build_questions() 自动出题（固定 SEED，见 §6）→
display = copy.deepcopy(items); 对 display 套 pykakasi 振假名（见 §7）→
TEMPLATE.replace(...) 注入 → 写 index.html
```

**关键点：TTS 音频用“原文素文”合成；振假名只加在 `display` 副本上，绝不污染用于
音频/出题的原始数据**（否则 edge-tts 会读 `<ruby>…</ruby>` 标签）。

模板注入统一用 `j()` 帮助函数：

```python
def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
```

## 4. TTS 音频约定（edge-tts）

```python
VOICE  = "ja-JP-NanamiNeural"   # 女声默认
RATE   = "-6%"
MIN_MP3 = 300                   # 小于此字节数视为失败
SEED   = <固定日期，如 20260909>  # 保证题目可复现
```

- 命令：`edge-tts --voice ja-JP-NanamiNeural --rate=-6% --text <句> --write-media <path>`
- **缓存 = 内容寻址**：`audio/{logical_id}-{sha1(text)[:10]}.mp3`；句子变了哈希变，自动重录；
  脱离引用（stale）的 mp3 在构建时清理。
- 并发：`ThreadPoolExecutor(max_workers=5)`；单条 3 次重试，**失败只跳过（不 exit）**，
  课件照常生成。
- 需要男声（对话/台词）时用 `ja-JP-KeitaNeural`，在 `meta` 里声明 `voiceMale`。

## 5. Nadeshiko 原声约定

- 用本机 CLI 搜台词：`nadeshiko search "<关键词>" --once -n 5`。
  （**勿用裸 `について` 之类**——会被 `位置について` 灌满；用限定词如 `について話`。）
- **只挑该词的规范语法用法**；误命中（如动词 あう、位置について、よりによって）一律弃用。
- 下载并转 base64 内嵌（离线）：build.py 里用 `urllib.request` 抓 CDN 的
  `audio`(mp3) / `thumb`(webp)，缓存到 `nade_audio/`（**加入 .gitignore**，可重下载）。
- 每条场景卡：缩略图 + 剧名 + EP@时间戳 + JP/EN/CN + nadeshiko.co 链接 + ▶ 原声播放键。
- 场景卡源徽标：`.src-nade`（紫）。

## 6. 题库约定（build_questions）

- `rng = random.Random(SEED)`：固定种子，答案/干扰项可复现。
- 标准题库银行（BANKS）：
  - `recog` 语法认识（选择题 “表示什么？”）
  - `engine` Engine 拆解（「に」扮演什么角色）— 按 group 决定正确项
  - `fill` 运用填空（例句挖空 `（　）` 选回；用 `read` 字段先清洗 `〜` 再匹配）
  - `listen` 聴解判別（听 `{id}-e0` 音频选语法）
  - `judge` 判断正误（批准确/批误皆有）
  - `mix`（混合交错）与 `wrong`（错题重练）由前端自动提供，不需数据。
- 干扰项：从其它 item 同字段随机抽 3 个、去重洗牌。
- 题目 `ref` 格式：`"{item_id}:{bank}:{idx}"`。
- **错题本 localStorage 命名空间**：`"{courseware-prefix}-wrong"`（如 `ni-wrong`），
  JSON 形如 `{"ref":1}`，答对即移出。
- 题库自动生成逻辑放 Python 侧 `build_questions()`；自定义 quiz 可放数据 `quizzes[]`。

## 7. 振假名（furigana / ruby）

**所有例句（含 Nadeshiko）都须有 ruby 振假名**，置于汉字上方，随浏览器默认 ruby 渲染：

```html
<ruby>環境<rt>かんきょう</rt></ruby>によって
```

- 用 `pykakasi` 生成读音（系统已安装；`import pykakasi`）。
- **Nadeshiko 会自带行内注音** `漢字(かな)`（如 `天(てん)ぷら`）——需先转成 `<ruby>`，
  避免重复注音；再用 pykakasi 补其余汉字。
- **上下文读音矫正**：pykakasi 对单字语境常读错（人→にん、年→ねん、経つ→へつ），
  用 `_READING_OVERRIDES` 短语表在 pykakasi 前整句替换（见 build.py 内实现）。
- 发现 pykakasi 无法救的错读示例：**直接改例句，别硬啃**（如：午餐→昼食、不错的→頼もしい、
  1万人→一万人）。
- 仅对**展示副本**加振假名；TTS、出题、填空一律用素文。
- 样式：`.jp{line-height:2}` + `.jp ruby rt{font-size:.52em;color:var(--sub)}`。

## 8. 前端/交互约定（TEMPLATE 里的 JS）

所有课件共享同一套 JS 框架，新课件沿用而不是另起炉灶：

- `const $=s=>document.querySelector(s)` 选择器简写。
- `TABS` 数组 + `renderNav()`/`goTab(k)`/`render()` 分发：常用页签
  `🗺️ 体系図 → 📜 歴史（如适用）→ 📖 詳解 → 🔍 対比 → 📝 例文 → 🎯 クイズ`。
- `play(id, btn)`：唯一播放函数；data-URI 存于 `AUDIO`/`NADE_AUDIO`/`SCENES` 对象，
  从 `AUDIO[id] || NADE_AUDIO[id]` 取源。`.playing` 类触发 pulse 动画，互斥按停。
- `rowHTML(sid, s)`：例句行 = ▶按钮 + JP（含 chip）+ CN。
- 来源徽标：`.src` 圆角小标签，`.src-moji`（绿）/ `.src-nade`（紫）。行内嵌在 jp 后：
  ```html
  <div class="jp">…<span class="src src-moji">MOJi</span></div>
  ```
- 题库前端：`mode/pool/order/qi/correct/answered` 状态机；`shuffle()` 用
  `Math.random()`（客户端随机，与构建期 `random.Random(SEED)` 区分）；`pick/nextQ/finish`
  标准流程；`localStorage` 错题本 + 「混合交错」「清空错题本」。
- 底部 `bar` 常驻：`barinfo` + `next` 按钮 + `score`。

### 共享 CSS 设计系统（勿改变量名）

```css
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;--gold:#b8860b}
```

公共类：`.wrap .card .row .btn .jp .cn .opt .exp .bar .hint .q .fin .src*`。

## 9. 硬性坑（务必避开）

1. **JSON 字符串值里禁止裸 ASCII 双引号 `"`**。会破坏内联 JSON 导致
   SyntaxError/JSONDecodeError。中文引语一律用「」。
2. `j()` 必须 `.replace("</", "<\\/")`，否则 `<script>` 里的 `</` 会提前结束脚本。
3. 勿把 `<ruby>` 等 HTML 写进**素文数据**——它会被 edge-tts 当成台词念出来。
   展示层 HTML 一律在 build 时生成，且只作用于 display 副本。
4. 例句必须是**地道日语**，禁止混中文（如“不错的”）。写完过一遍 pykakasi 读音抽查。
5. `nade_audio/`、`scenes/` 等可再生成的缓存**加入 .gitignore**；音声缓存 `audio/`
   是可提交的（内容寻址、体积小）；生成的 `index.html` 必须提交。
6. 题型 `ans` 语法：choice/listen 为**下标**，judge 为**布尔**。
7. 新增课件后：根 `index.html` 卡片 + `README.md` 表格都要同步。

## 10. 新增课件的最小工作流

1. `mkdir <name>-courseware/`；复制 `ni-courseware/build.py` 作蓝本，清掉旧数据；
   新建 `<name>.json`；写 `README.md`。
2. 按 §3 管道跑通：加词条 → `python3 build.py` → 校验 `index.html` 出现新卡/新音/新题。
3. 校对振假名（§7）、错读例句、Nadeshiko 规范用法。
4. 根 `index.html` 加卡片、`README.md` 加一行。
5. `git add <name>-courseware/ index.html README.md && git commit && git push`。

## 11. 工具 / 技能

本机 CLI（供 build 数据采集，属烧录而非课件内功能）：
- **moji-dict 技能**：`moji <词> --once` 查释义/例句/发音（MOJi辞書），用于例句采集。
- **nadeshiko CLI**：`nadeshiko search <词> --once -n 5` 搜真实台词（含幕/ep/时间戳/原声）。
- 构建脚本乙方（系统侧）：`pykakasi`（振假名）、`edge-tts`（微软神经语音 TTS）。

> 遇到本守则未覆盖的场景：以 `ni-courseware/` 为事实标准（最新范式），先读它再动手。