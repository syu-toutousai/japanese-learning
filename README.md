# japanese-learning

个人日语学习资料库。记录学习过程中制作的交互课件与练习材料，便于日后复习。

## 目录

| 内容 | 说明 |
|---|---|
| [`index.html`](./index.html) | 🏠 课件集索引页：三套课件入口（本地 serve 后即为主页） |
| [`keishiku-courseware/`](./keishiku-courseware/) | 形式名词・十三杰完全图鉴：13 词按功能分 5 组 + 核心意象 + 最小对立句対比 + 四层题库与错题本，53 段 TTS 发音，单文件 HTML 离线可用 |
| [`shidai-courseware/`](./shidai-courseware/) | 「次第」完全掌握：四种用法讲解 + 例句原声 + クイズ双题库（基礎15問 / 挑戦10問），单文件 HTML 离线可用 |
| [`keiji-courseware/`](./keiji-courseware/) | 接头接尾词・口袋图鉴：词条存 `pocket.json` 随手增删，重建即自动生成卡片、TTS 发音与三层题库（詞義認識 / 運用填空 / 聴解判別）＋错题本，单文件 HTML 离线可用 |
| [`houi-courseware/`](./houi-courseware/) | 方角・方位・认知罗盘：按人脑空间认知规律掌握「东南西北・左右上下」及派生词——视觉罗盘＋身体坐标＋对立结伴＋心理旋转，五层题库（含空間判斷）＋错题本，单文件 HTML 离线可用 |
| [`kotoba-courseware/`](./kotoba-courseware/) | 言葉・新詞・记忆的种子：新词收集器——把刚碰到的词按人脑习得规律铺成「初遇情景→核心意象→语义网络→近义辨析→提取闯关→间隔复习」；已收「風情」「開く・あく（穴あくまでパンツはくなよ）」「はしゃぐ（子どものはしゃぐ声）」，顶部可切换，后续新词照 `kotoba.json` 往集子里丢，课件自动长 |

## 来源与版权说明（重要）

- **次第课件**中的**词条释义、例句及中文译文、单词/例句发音音频**均取自 **MOJi辞書**（[mojidict.com](https://www.mojidict.com)），
  系通过其 Web 端使用的非公开接口以个人学习目的少量获取。
  著作权归 MOJi辞書 所有，本仓库仅作个人复习之用的非商业性引用。
- **形式名词课件**中的例句为自写，释义经 MOJi辞書 查证校对；
- **接辞口袋课件**中的词条与例句均为自写整理；
- **方位罗盘课件**中的部分词条释义/读音取自 MOJi辞書 查证，例句为自写整理；
- **言葉・新词记忆种子课件**中的「風情」词条释义、例句及中文译文、词条/例句原声均取自 MOJi辞書，
  初遇情景（素材字幕）与自写例句的发音由本地 edge-tts 合成；
  形式名词课件、接辞口袋课件与方位罗盘课件的发音由本地 edge-tts（微软 Azure 神经语音
  ja-JP-Nanami）合成生成，非录音素材。
- 如有版权方面的问题需要调整或删除相关内容，请提 issue 联系，我会及时处理。

## 工具链

- 数据抓取与发音下载使用自建 CLI 工具完成（查词 / 发音 TTS 均为免登录接口）；
- **「接辞口袋」的日常扩充**配合本机 **moji-dict** 技能：`moji <词条> --once`
  查释义与例句 → 抄入 `keiji-courseware/pocket.json` → `python3 build.py`
  重建即自动长出卡片、发音与题目（详见该目录内 README 的「增长闭环」一节）；
- **「方位罗盘」的增词**同理：`moji <词条> --once` 查释义 → 抄入
  `houi-courseware/houi.json` → `python3 build.py` 重建，罗盘/卡片/题目/发音自动生长；
- **「新词记忆种子」的收词**：刚碰到的生词 `moji <単語> --once` 查释义与例句 →
  抄成 `kotoba-courseware/kotoba.json` 里一条 `words[]`（核心意象/义项/近义辨析/记忆锚/题目）→
  `python3 build.py` 重建，初见卡/语义网络/题库/间隔复习自动长出（详见该目录 README 的「增长闭环」）；
- 形式名词课件与接辞口袋课件的例句发音由 edge-tts（微软 Azure 神经语音
  ja-JP-Nanami）合成生成；
- 构建脚本见各课件目录内的 `build.py`。

## 本地预览

```bash
cd japanese-learning
python3 -m http.server 8642 --bind 127.0.0.1
# 打开 http://localhost:8642/
```
