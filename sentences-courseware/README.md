# 例文 跟読トレーナー ・ 2×2×2

一本把 N2 词汇例句磨进耳朵与嘴巴的听读课件：每句自动播放

```
慢速×2 → 跟读留白 → 中速×2 → 跟读留白 → 常速×2 → 跟读留白
```

慢速 -40% / 中速 -18% / 常速 原速；同档两遍之间短停 0.8s，每档第二遍后留出
**≈该档句长的空白**，正好够你张口跟读一遍。女声 Nanami 与男声 Keita 一键切换，
另有盲聴モード（文字先模糊、点击揭示）与全句连播。成品是单文件 `index.html`
（内嵌全部训练音轨、单速片段、Nadeshiko 原声与缩略图，离线可用）。

## 打开

浏览器直接打开 `index.html`，或起本地服务：

```bash
python3 -m http.server 8642   # 然后访问 http://localhost:8642/sentences-courseware/
```

## 内容

| 页面 | 内容 |
|---|---|
| 🎧 跟読訓練 | 每句：振假名日文 + 中文 + 重点词卡 + `▶ 跟読訓練`（2×2×2 混音）+ 单档 🐢/🚶/🏃 + Nadeshiko 场景卡（缩略图+原声） |
| 📝 例文一覧 | 全句紧凑列表，一点即播训练音轨（复习用） |
| 🎯 クイズ | 🎧 聴解判别（听常速选句子）・✍️ 語彙填空（原句挖空）・📘 語彙認識（词→释义），加 🎲 混合交錯 与 📕 错题本（`sent-wrong`） |

底部常驻：`▶ 連続再生`（全部句子顺序自动训练，可暂停/下一句）与进度。

## 数据与增长闭环

- **`sentences.md` 是句子收件箱**（一行一句，本目录的 source of truth）。
  往里丢一句新句子 → `python3 build.py` → 卡片、双声三速音声、训练音轨自动长出
  （此时尚无翻译与词汇条目，重构时会提示补录）。
- **`sentences.json` 是增强层**，按 `jp` 原文与 md 匹配，逐条补：
  - `cn` — 中文翻译；
  - `words[]` — 重点词卡：`w`（词形）/ `read`（读音）/ `pos`（词性）/ `mean`（释义）/
    `form`（在句中的实际活用形，用于挖空出题，如 `返上して`・`取り次いで`）；
  - `nadeshiko[]` — 真实台词场景卡（`moji`/`nadeshiko` CLI 采集）。

```jsonc
{
  "id": "s01",
  "jp": "食品の腐敗を防止する。",
  "cn": "防止食品腐败。",
  "words": [
    { "w": "腐敗", "read": "ふはい", "pos": "名·サ变", "mean": "腐败；腐烂；堕落", "form": "腐敗" }
  ],
  "nadeshiko": [
    { "jp": "それは腐敗(ふはい)ではなく発酵(はっこう)だ!",
      "en": "That isn't rotten, it's fermented!", "cn": "那不是腐败，是发酵！",
      "media": "Delicious in Dungeon", "ep": "EP4", "at": "16:56",
      "url": "https://nadeshiko.co/en/sentence/76jS5ceZYr6L",
      "audio": "https://cdn.nadeshiko.co/media/.../....mp3",
      "thumb": "https://cdn.nadeshiko.co/media/.../....webp" }
  ],
  "note": "💡 教学提示（可选）"
}
```

### 采集工具

```bash
moji 腐敗 --once                      # 词条释义（交互提示用 printf '0\n' 管道选择）
nadeshiko search "休日返上" --exact --once -n 4   # 真实台词（只用规范用法，避开误命中）
```

## 音频规格与缓存

- 女声 `ja-JP-NanamiNeural` / 男声 `ja-JP-KeitaNeural`；三档 `-40% / -18% / +0%`。
- 单速片段：`audio/{id}-{f|m}-{slow|med|norm}-{sha1(文+rate)[:10]}.mp3`
- 训练音轨：`audio/{id}-{f|m}-train-{sha1(文+三档rate+停顿参数)[:10]}.mp3`
  （ffmpeg `concat`，全 wav 拼接后一次编码，避免 mp3 接缝）。
  前端用构建时算好的 `medStart`/`normStart` 在播放中高亮当前档位。
- 句子一改哈希即变，自动重录；脱离引用的 mp3 在构建时清理。`audio/` 可提交。
- `nade_audio/` 为 CDN 下载缓存（已 gitignore，可重新下载）。

## 构建

```bash
python3 build.py
```

重复构建全走缓存；某条 TTS/Nadeshiko 失败只跳过该条，课件照常生成。
依赖：`edge-tts`、`ffmpeg`/`ffprobe`、`pykakasi`。

## 来源与版权

- 重点词释义经 **moji-dict 技能**（`moji` CLI）查证取自 **MOJi辞書**（mojidict.com），
  中文翻译为本仓库自写整理；
- Nadeshiko 台词/原声/缩略图来自 **Nadeshiko**（nadeshiko.co），
  仅作个人语言学习之非商业性引用；
- 全部 TTS 发音由本地 edge-tts（微软 Azure 神经语音）合成，非录音素材。
