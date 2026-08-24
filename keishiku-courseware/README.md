# 「形式名词」十三杰完全图鉴 · 交互式课件

基于 edge-tts 日语原声（Nanami/Keita）与 MOJi辞書 释义校对的**单文件课件**
（`index.html`，约 1.8MB，离线可用）。

## 打开

浏览器直接打开 `index.html`，或起本地服务：

```bash
python3 -m http.server 8642   # 然后访问 http://localhost:8642/index.html
```

## 内容

- **13 个常用形式名词**，按功能分 5 组（组块化）：
  - 名词化三兄弟：こと・の・もの
  - 判断双雄：はず・わけ
  - 时间定位器：ところ・うち・たび
  - 状态的样子：よう・まま・とおり
  - 因果与心意：ため(+おかげ/せい)・つもり
- **5 个页签**：🗺️地図（体系总览）→ 📖詳解 → ⚖️対比（最小对立句）→ 🗂️例句 → 🎯クイズ
- **53 段内嵌发音**例句，全部可点击播放

## 设计依据（人脑语言习得机制）

| 特性 | 对应机制 |
|---|---|
| 按功能分 5 组而非按音序排列 | 组块化 chunking |
| 每词一个「核心意象」画面 + emoji | 双重编码 dual coding |
| 教「接续框架」（构式）而非孤立释义 | 构式语法 / usage-based acquisition |
| 対比页最小对立句（こと/の、ため/ように…） | 对比分析，划清概念边界 |
| 题库四层：认识→辨析→听解→运用 | 检索练习 testing effect、认知阶梯 |
| 🎲混合交错模式 | interleaving 交错练习 |
| 📕错题本（localStorage，做对即移出） | error-driven review 错题驱动复习 |

## 重新构建

```bash
python3 build.py
```

- 音频由 edge-tts 在首次构建时生成并缓存于 `audio/*.mp3`；删除某条 mp3 即可重新生成。
- 扩充内容：改 `build.py` 里的 `SENTS / GROUPS / NOUNS / CONTRASTS / QA-QD` 后重跑即可。

同系列姊妹篇：「次第」课件见 `../shidai-courseware/index.html`。
