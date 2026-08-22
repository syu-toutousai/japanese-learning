# 「次第」交互式学习课件

基于 MOJi辞書 数据与原声发音的单文件课件（`index.html`，离线可用）。

- 打开：浏览器直接打开 `index.html`，或访问 http服务（如 `python3 -m http.server 8642`）
- 内容：四种用法讲解、例句库（18 段内嵌发音）、クイズ双题库（基礎 15 問 / 挑戦 10 問 / 混合 25 問）

## 重新构建

修改内容后运行：

```bash
python3 build.py
```

以后想扩充题库，往 `build.py` 的 `QUESTIONS`（基础）或 `QUESTIONS2`（挑战）里加题再跑 `python3 build.py` 即可。

## 来源

释义、例句、译文与发音音频均取自 **MOJi辞書**（mojidict.com，经其 Web 端非公开接口以个人学习目的少量获取），著作权归 MOJi辞書 所有，此处仅作非商业性个人复习引用。
