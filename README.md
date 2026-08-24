# japanese-learning

个人日语学习资料库。记录学习过程中制作的交互课件与练习材料，便于日后复习。

## 目录

| 内容 | 说明 |
|---|---|
| [`index.html`](./index.html) | 🏠 课件集索引页：两套课件入口（本地 serve 后即为主页） |
| [`keishiku-courseware/`](./keishiku-courseware/) | 形式名词・十三杰完全图鉴：13 词按功能分 5 组 + 核心意象 + 最小对立句対比 + 四层题库与错题本，53 段 TTS 发音，单文件 HTML 离线可用 |
| [`shidai-courseware/`](./shidai-courseware/) | 「次第」完全掌握：四种用法讲解 + 例句原声 + クイズ双题库（基礎15問 / 挑戦10問），单文件 HTML 离线可用 |

## 来源与版权说明（重要）

- **次第课件**中的**词条释义、例句及中文译文、单词/例句发音音频**均取自 **MOJi辞書**（[mojidict.com](https://www.mojidict.com)），
  系通过其 Web 端使用的非公开接口以个人学习目的少量获取。
  著作权归 MOJi辞書 所有，本仓库仅作个人复习之用的非商业性引用。
- **形式名词课件**中的例句为自写，释义经 MOJi辞書 查证校对；
  发音由本地 edge-tts（微软 Azure 神经语音 ja-JP-Nanami/Keita）合成生成，非录音素材。
- 如有版权方面的问题需要调整或删除相关内容，请提 issue 联系，我会及时处理。

## 工具链

数据抓取与发音下载使用自建 CLI 工具完成（查词 / 发音 TTS 均为免登录接口），
形式名词课件的例句发音用 edge-tts 合成。
构建脚本见各课件目录内的 `build.py`。

## 本地预览

```bash
cd japanese-learning
python3 -m http.server 8642 --bind 127.0.0.1
# 打开 http://localhost:8642/
```
