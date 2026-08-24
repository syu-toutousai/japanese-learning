#!/usr/bin/env python3
"""Build the self-contained 「形式名词」 interactive courseware HTML.

音频：edge-tts 日语神经网络语音，首次构建时生成并缓存到 audio/，
之后重复构建直接复用缓存（删除某个 mp3 即可重新生成该条）。
"""

import base64
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

FEMALE = "ja-JP-NanamiNeural"
MALE = "ja-JP-KeitaNeural"

# ---------------------------------------------------------------- sentences
# id -> {jp, cn, v(oice, 可省略默认女声)}
SENTS = {
    # ---- こと ----
    "koto1": {"jp": "毎朝ジョギングすることにしています。", "cn": "我坚持每天早晨慢跑。"},
    "koto2": {"jp": "来月から大阪支社に行くことになりました。", "cn": "（上面）定下来下个月去大阪分公司。"},
    "koto3": {"jp": "大事なのは、最後まであきらめないことだ。", "cn": "重要的是坚持到最后、不放弃。"},
    "koto4": {"jp": "忘れ物がないように、もう一度確認すること。", "cn": "以防遗漏，请再确认一遍。（备忘录口吻）"},
    # ---- の ----
    "no1": {"jp": "子供たちが公園で遊んでいるのを見て、昔を思い出した。", "cn": "看到孩子们在公园玩耍，想起了从前。"},
    "no2": {"jp": "隣の部屋から、誰か話しているのが聞こえる。", "cn": "听得见隔壁房间有人在说话。"},
    "no3": {"jp": "荷物が重そうだから、持つのを手伝おうか。", "cn": "行李看着挺重，我来帮你拿吧。"},
    # ---- もの ----
    "mono1": {"jp": "冷蔵庫に古いものがあるけど、捨ててもいい？", "cn": "冰箱里有旧东西，可以扔掉吗？"},
    "mono2": {"jp": "学生時代はよくこの喫茶店に通ったものだ。", "cn": "学生时代常来这家咖啡店啊。（怀念）"},
    "mono3": {"jp": "失敗は誰にでもあるものだ。", "cn": "失败谁都会有。（道理）"},
    # ---- はず ----
    "hazu1": {"jp": "10時の新幹線に乗ったと言っていたから、もう東京に着いているはずだ。", "cn": "他说坐上了10点的新干线，这会儿按理已经到东京了。"},
    "hazu2": {"jp": "彼は昨日から出張中だから、今日は会社にいるはずがない。", "cn": "他从昨天起就在出差，今天绝不可能在公司。"},
    "hazu3": {"jp": "昨夜メールを送ったはずなのに、届いていないらしい。", "cn": "明明昨晚应该发了邮件，却好像没送到。"},
    # ---- わけ ----
    "wake1": {"jp": "十年も東京に住んでいたんだ。道路に詳しいわけだ。", "cn": "在东京住了十年，难怪对道路这么熟。"},
    "wake2": {"jp": "英語が話せないわけではなく、使う機会がないだけです。", "cn": "并不是不会说英语，只是没有使用的机会。"},
    "wake3": {"jp": "徹夜で仕事をしたんだから、眠いわけだ。", "cn": "通宵干了活，当然困啦。"},
    # ---- ところ ----
    "tokoro1": {"jp": "今から出かけるところです。何かありますか。", "cn": "我现在正要出门，有事吗？"},
    "tokoro2": {"jp": "ちょうど昼ご飯を食べているところです。", "cn": "正在吃午饭呢。"},
    "tokoro3": {"jp": "先生に質問したところ、とても丁寧に教えてくれた。", "cn": "问了老师，（没想到）他讲得非常耐心。"},
    "tokoro4": {"jp": "寝ようとしたところに、急に電話が鳴りました。", "cn": "正要睡觉的时候，电话突然响了。"},
    # ---- うち ----
    "uchi1": {"jp": "若いうちに、いろいろな国を旅したい。", "cn": "想趁年轻游历各个国家。"},
    "uchi2": {"jp": "忘れないうちに、メモしておこう。", "cn": "趁还没忘记，先记下来吧。"},
    "uchi3": {"jp": "雨が降らないうちに、洗濯物を取り込みましょう。", "cn": "趁雨还没下，把晾的衣服收进来吧。"},
    # ---- たび ----
    "tabi1": {"jp": "会うたびに、背が伸びているね。", "cn": "每次见面你都长高了呀。"},
    "tabi2": {"jp": "この曲を聴くたびに、ふるさとを思い出す。", "cn": "每当听到这首歌，就会想起故乡。"},
    # ---- よう ----
    "you1": {"jp": "一年練習して、日本語が話せるようになりました。", "cn": "练习了一年，变得会说日语了。"},
    "you2": {"jp": "忘れないように、カレンダーに書いておきます。", "cn": "为了不忘记，先写在日历上。"},
    "you3": {"jp": "どうやら雨が降り出すようだね。", "cn": "看样子要下雨了。"},
    "you4": {"jp": "彼女はまるで人形のような目をしている。", "cn": "她有着宛如人偶般的眼睛。"},
    # ---- まま ----
    "mama1": {"jp": "窓を開けたまま寝てしまって、風邪をひいた。", "cn": "开着窗就睡着了，结果感冒了。"},
    "mama2": {"jp": "靴を履いたまま部屋に入らないでください。", "cn": "请不要穿着鞋进屋。"},
    "mama3": {"jp": "そのまま少々お待ちください。", "cn": "请就这样稍等片刻。"},
    # ---- とおり ----
    "toori1": {"jp": "説明書のとおりに組み立てれば、簡単にできますよ。", "cn": "按说明书组装的话，很简单就能装好哦。"},
    "toori2": {"jp": "思ったとおり、試験にこの問題が出ました。", "cn": "和预想的一样，考试出了这道题。"},
    "toori3": {"jp": "私の言うとおりに繰り返してください。", "cn": "请跟着我说的重复一遍。"},
    # ---- ため・おかげ・せい ----
    "tame1": {"jp": "健康のために、毎晩11時に寝ることにしました。", "cn": "为了健康，决定每晚十一点睡。"},
    "tame2": {"jp": "大雨のため、電車が1時間遅れています。", "cn": "因大雨，电车晚点一小时。"},
    "okage1": {"jp": "先生のおかげで、合格できました。", "cn": "托老师的福，考上了。"},
    "sei1": {"jp": "寝不足のせいで、頭が痛い。", "cn": "都怪睡眠不足，头疼。"},
    # ---- つもり ----
    "tsumori1": {"jp": "来年、日本へ留学するつもりです。", "cn": "我打算明年去日本留学。"},
    "tsumori2": {"jp": "冗談のつもりでしたが、彼を怒らせてしまった。", "cn": "本来只是想开个玩笑，却惹他生气了。"},
    "tsumori3": {"jp": "全部書き終えたつもりだったが、1ページ忘れていた。", "cn": "以为全写完了，其实漏了一页。"},
    # ---- 对比专用句 ----
    "c_no1": {"jp": "彼が泳いでいるのを見た。", "cn": "看见了他游泳的场面。（感知对象→の）"},
    "c_koto2": {"jp": "漢字を読むことができます。", "cn": "会读汉字。（固定句型→こと）"},
    "c_hazu": {"jp": "10時の電車に乗ったと言っていたから、今ごろ東京に着いているはずだ。", "cn": "事前推算：按理这会儿该到东京了。"},
    "c_wake": {"jp": "毎日10時間勉強しているから、上達するわけだ。", "cn": "事后恍然：每天学十小时，难怪进步。"},
    "c_tame": {"jp": "風邪を治すために薬を飲む。", "cn": "为了治好感冒而吃药。（治す＝意志动词）", "v": MALE},
    "c_you": {"jp": "風邪が早く治るように薬を飲む。", "cn": "为了感冒能快点好而吃药。（治る＝非意志）", "v": MALE},
    "c_okage": {"jp": "毎日練習したおかげで、試合に勝った。", "cn": "多亏每天练习，比赛赢了。（归功）", "v": MALE},
    "c_sei": {"jp": "寝坊したせいで、始発に乗り遅れた。", "cn": "都怪睡懒觉，没赶上首班车。（甩锅）", "v": MALE},
    "c_mama": {"jp": "音楽をかけたまま、眠ってしまった。", "cn": "音乐放着没关，就这么睡着了。（状态冻结）", "v": MALE},
    "c_nagara": {"jp": "音楽をかけながら、宿題をする。", "cn": "一边放音乐一边做作业。（同时并行）", "v": MALE},
    "c_trio3": {"jp": "今、昼ご飯を食べたところです。", "cn": "刚吃完午饭。"},
}

# ---------------------------------------------------------------- groups & nouns
GROUPS = [
    {"id": "pack",  "name": "名词化三兄弟", "desc": "把动作、场面「打包」成名词，句子才能当零件用",
     "color": "#4f6ef7", "nouns": ["koto", "no", "mono"]},
    {"id": "judge", "name": "判断双雄",     "desc": "给事实加一层态度：「按理应该」「难怪如此」",
     "color": "#e8590c", "nouns": ["hazu", "wake"]},
    {"id": "time",  "name": "时间定位器",   "desc": "在时间轴上钉针、开窗、设按钮",
     "color": "#188a52", "nouns": ["tokoro", "uchi", "tabi"]},
    {"id": "state", "name": "状态的样子",   "desc": "相似・冻结・重合：三种状态关系",
     "color": "#9c36b5", "nouns": ["you", "mama", "toori"]},
    {"id": "mind",  "name": "因果与心意",   "desc": "解释为什么，说出心里打算什么",
     "color": "#b8860b", "nouns": ["tame", "tsumori"]},
]

NOUNS = {
    "koto": {
        "group": "pack", "name": "こと", "jlpt": "N5→N3", "emoji": "📦",
        "img": "文件袋：把一个动作装进袋子，它立刻变成可以谈论的一件「事」。",
        "attach": ["動辞書形／ない形＋こと"],
        "patterns": [
            ["〜ことができる", "会…、能…（能力·可能）"],
            ["〜ことにする", "（自己拍板）决定要…"],
            ["〜ことになる", "（别人/组织拍板）定下来要…"],
            ["〜ことにしている", "坚持做…（个人习惯·规矩）"],
            ["大事なのは〜ことだ", "重要的是…（收束、提醒）"],
        ],
        "sents": ["koto1", "koto2", "koto3", "koto4"],
        "note": "固定搭配只能用こと：✗のができる。「決める」类抽象动词接こと；备忘录结尾的「〜すること」＝要求做某事。",
    },
    "no": {
        "group": "pack", "name": "の", "jlpt": "N4", "emoji": "📷",
        "img": "一台摄像机：直接拍下眼前发生的具体场面，原样递给动词。",
        "attach": ["動普通形＋の"],
        "patterns": [
            ["〜のを見る／見える", "直接看到…的场面"],
            ["〜のが聞こえる", "听得见…"],
            ["〜のを待つ／手伝う", "等待/帮助的对象正是那件事本身"],
            ["〜のだ", "强调说明语气（是这么回事）"],
        ],
        "sents": ["no1", "no2", "no3"],
        "note": "见到「見る・聞こえる・待つ・手伝う・撮る」这类直接感知动词 → 用の不用こと。对比页有判定练习。",
    },
    "mono": {
        "group": "pack", "name": "もの", "jlpt": "N4→N3", "emoji": "🧸",
        "img": "摸得到的实物；引申为「人人都如此」的道理，或带温度的回忆。",
        "attach": ["動・い形普通形＋ものだ"],
        "patterns": [
            ["もの（实物）", "东西、物品"],
            ["〜たものだ", "当年常…（怀念语气）"],
            ["〜ものだ（感叹·道理）", "真…啊／本来就是…的"],
            ["というものだ", "这才叫…、所谓…"],
        ],
        "sents": ["mono1", "mono2", "mono3"],
        "note": "「ものだ」降调收尾＝道理/感叹/回忆；注意区分表借口的「もので・ものだから」。",
    },
    "hazu": {
        "group": "judge", "name": "はず", "jlpt": "N3", "emoji": "🔗",
        "img": "推理链：从已知线索一环环推出结论——「按理说应该～」（还没亲眼确认）。",
        "attach": ["名＋の＋はずだ／動・い形普通形＋はずだ"],
        "patterns": [
            ["〜はずだ", "按理应该…（有依据的推断）"],
            ["〜はずがない", "绝不可能…（强否定推断）"],
            ["〜はずだった", "本来应该…（却落空了）"],
        ],
        "sents": ["hazu1", "hazu2", "hazu3"],
        "note": "はず＝「事前预测」，亲眼确认过的事实不能用はず。「〜はずなのに」自带落差感：明明应该…却…。",
    },
    "wake": {
        "group": "judge", "name": "わけ", "jlpt": "N3", "emoji": "🌉",
        "img": "逻辑桥：在前因与后果之间搭桥——结果已在眼前，回头讲通道理：「难怪～」。",
        "attach": ["動・い形普通形＋わけだ"],
        "patterns": [
            ["〜わけだ", "难怪…、也就是说…（解释得通）"],
            ["〜わけではない", "并不是…（部分否定）"],
            ["〜わけがない", "不可能…"],
            ["〜わけにはいかない", "（情理上）不能…"],
            ["どういうわけか", "不知为什么"],
        ],
        "sents": ["wake1", "wake2", "wake3"],
        "note": "はず＝预测未来「按理会」；わけ＝解释现状「所以难怪」。一个是望远镜，一个是回马枪。",
    },
    "tokoro": {
        "group": "time", "name": "ところ", "jlpt": "N4→N3", "emoji": "📍",
        "img": "时间轴上的一枚针：正要扎下・正在停留・刚刚拔出——给动作定坐标。",
        "attach": ["Ｖるところ／Ｖているところ／Ｖたところ"],
        "patterns": [
            ["Ｖる＋ところだ", "正要做…"],
            ["Ｖている＋ところだ", "正在做…"],
            ["Ｖた＋ところだ", "刚做完…"],
            ["〜たところ", "一试才发现（带来结果）"],
            ["〜ところに／へ", "正当…时候（来了外部事件）"],
        ],
        "sents": ["tokoro1", "tokoro2", "tokoro3", "tokoro4"],
        "note": "电话应答标准句：「今、そちらに向かっているところです」。与たとたん（瞬间突发）相比、たところ侧重得到的结果。",
    },
    "uchi": {
        "group": "time", "name": "うち", "jlpt": "N4", "emoji": "⏳",
        "img": "一扇还没关上的门：窗口期有限，趁现在赶紧做。",
        "attach": ["Ｖる／Ｖている・Ｖない＋うちに", "い形＋うちに", "名＋の＋うちに"],
        "patterns": [
            ["〜うちに", "趁着…期间（做完）"],
            ["〜ないうちに", "趁还没…"],
            ["〜間（に）", "在…期间（单纯时间范围）"],
        ],
        "sents": ["uchi1", "uchi2", "uchi3"],
        "note": "餐厅高频句「熱いうちに召し上がってください」＝趁热吃。うちに暗含「过期不候」的紧迫感。",
    },
    "tabi": {
        "group": "time", "name": "たび", "jlpt": "N3", "emoji": "🔁",
        "img": "一枚每次按下都会触发的按钮：前项每发生一次，后项必然跟着发生。",
        "attach": ["Ｖ辞書形＋たびに"],
        "patterns": [["〜たびに", "每次…都（必然伴随）"]],
        "sents": ["tabi1", "tabi2"],
        "note": "たびに前后都必须是可以反复发生的事；一次性事件不能用。",
    },
    "you": {
        "group": "state", "name": "よう", "jlpt": "N4→N3", "emoji": "🪞",
        "img": "一面万花筒：照出相似的比喻，也照出感官里的推测，还能照出能力的变化。",
        "attach": ["Ｖる／Ｖない＋ようだ", "名＋の＋ようだ"],
        "patterns": [
            ["まるで〜のようだ", "恰似…一样（比喻）"],
            ["〜ようだ", "看样子…（凭感官推测）"],
            ["〜ようになる", "变得会…（能力·习惯的变化）"],
            ["〜ように（目的）", "为了能…（无意志目标）"],
        ],
        "sents": ["you1", "you2", "you3", "you4"],
        "note": "目的的分水岭：ために接意志动词（勉強するために）；ように接无意志动词（できる／わかる／遅れない）。详见对比页。",
    },
    "mama": {
        "group": "state", "name": "まま", "jlpt": "N4", "emoji": "🧊",
        "img": "按下暂停键：画面被冻结，状态原样保持不动（常含「该变却没变」的遗憾）。",
        "attach": ["Ｖた＋ままだ", "Ｖない＋まま", "名＋の＋まま"],
        "patterns": [
            ["〜たままで／〜たまま", "保持…状态（放置不管）"],
            ["〜ないまま", "没…就…"],
            ["そのままで", "就那样、维持原状"],
        ],
        "sents": ["mama1", "mama2", "mama3"],
        "note": "ながら＝两个动作主动并行；まま＝一个状态被动冻结。对比页有一组最小对立句。",
    },
    "toori": {
        "group": "state", "name": "とおり", "jlpt": "N3", "emoji": "🛤️",
        "img": "两条完全重合的铁轨：照着模板走，分毫不差。",
        "attach": ["Ｖる／Ｖた＋とおり（に）", "名＋の＋とおり（に）"],
        "patterns": [
            ["〜とおりに", "按照…那样做"],
            ["思ったとおり", "和预想的一样"],
            ["ご覧のとおり", "如您所见"],
        ],
        "sents": ["toori1", "toori2", "toori3"],
        "note": "とおりに＝照着「模板」执行；まま＝维持「现状」。一个有参照物，一个只是冻结。",
    },
    "tame": {
        "group": "mind", "name": "ため", "jlpt": "N4→N3", "emoji": "🎯",
        "img": "一支双向箭头：向前指目标是「为了」，向后指来源是「因为」。旁边站着功臣おかげ和背锅侠せい。",
        "attach": ["Ｖる／Ｖない＋ため（に）", "名＋の＋ため（に）"],
        "patterns": [
            ["〜ため（に）①", "为了…（目的，书面）"],
            ["〜ため（に）②", "因为…（原因，多消极）"],
            ["〜おかげで", "托…的福（好事归功）"],
            ["〜せいで", "都怪…（坏事归罪）"],
        ],
        "sents": ["tame1", "tame2", "okage1", "sei1"],
        "note": "同一件事换词换立场：「彼のせいで遅れた」怪他，「彼のおかげで間に合った」谢他。",
    },
    "tsumori": {
        "group": "mind", "name": "つもり", "jlpt": "N4", "emoji": "📝",
        "img": "画在心里的计划路线图；路线若与现实不符，就成了「自以为」。",
        "attach": ["Ｖる／Ｖない＋つもりだ", "Ｖた＋つもり"],
        "patterns": [
            ["〜つもりだ", "打算…（主观计划）"],
            ["〜たつもり", "自以为…（实际未必）"],
            ["〜つもりはなかった", "并没有…的意思"],
        ],
        "sents": ["tsumori1", "tsumori2", "tsumori3"],
        "note": "つもり＝主观打算；予定＝客观安排。「たつもり」常带现实打脸的反差感。",
    },
}

# ---------------------------------------------------------------- contrasts
CONTRASTS = [
    {
        "title": "こと vs の", "sub": "最经典的分辨题",
        "rule": "固定句型・抽象叙述 → こと　｜　直接感知的具体场面 → の",
        "rows": [
            {"mark": "○", "aid": "c_no1", "jp": "彼が泳いでいる<b>の</b>を見た。", "cn": "眼睛直接看到的场面 → 只能の"},
            {"mark": "✗", "aid": None, "jp": "彼が泳いでいる<b>こと</b>を見た。", "cn": "✗ 見る的对象要具体，こと太抽象"},
            {"mark": "○", "aid": "c_koto2", "jp": "漢字を読む<b>こと</b>ができます。", "cn": "ことができる 是固定句型 → 只能こと"},
            {"mark": "△", "aid": None, "jp": "泳ぐ<b>の／こと</b>が好きだ。", "cn": "爱好类两可，口语偏爱の"},
        ],
    },
    {
        "title": "はず vs わけ", "sub": "望远镜 vs 回马枪",
        "rule": "还没发生、靠推理 → はず「按理」　｜　结果已在眼前、回头讲道理 → わけ「难怪」",
        "rows": [
            {"mark": "○", "aid": "c_hazu", "jp": "今ごろ東京に着いている<b>はず</b>だ。", "cn": "事前推算：他此刻「应该」到了（我没看见）"},
            {"mark": "○", "aid": "c_wake", "jp": "毎日10時間勉強しているから、上達する<b>わけ</b>だ。", "cn": "事后恍然：进步摆在眼前，原因也摆着呢"},
        ],
    },
    {
        "title": "ため(に) vs ように", "sub": "意志的分水岭",
        "rule": "主语能控制的动作 → ために　｜　自然变化・他人能否实现 → ように",
        "rows": [
            {"mark": "○", "aid": "c_tame", "jp": "風邪を治す<b>ために</b>薬を飲む。", "cn": "治す＝我能控制的意志动词"},
            {"mark": "○", "aid": "c_you", "jp": "風邪が早く治る<b>ように</b>薬を飲む。", "cn": "治る＝身体自己变好，管不了 → ように"},
        ],
    },
    {
        "title": "おかげ vs せい", "sub": "功劳簿 vs 黑锅",
        "rule": "同一件事，好事记 おかげ 账上，坏事记 せい 头上",
        "rows": [
            {"mark": "○", "aid": "c_okage", "jp": "毎日練習した<b>おかげ</b>で、試合に勝った。", "cn": "赢了 → 归功于练习"},
            {"mark": "○", "aid": "c_sei", "jp": "寝坊した<b>せい</b>で、始発に乗り遅れた。", "cn": "迟了 → 怪睡懒觉"},
        ],
    },
    {
        "title": "まま vs ながら", "sub": "冻结 vs 并行",
        "rule": "状态放着不动 → まま　｜　两个动作同时进行 → ながら",
        "rows": [
            {"mark": "○", "aid": "c_mama", "jp": "音楽をかけた<b>まま</b>、眠ってしまった。", "cn": "音乐没人管地继续响 → 冻结"},
            {"mark": "○", "aid": "c_nagara", "jp": "音楽をかけ<b>ながら</b>、宿題をする。", "cn": "有意边听边写 → 并行"},
        ],
    },
    {
        "title": "ところ三兄弟", "sub": "同一枚针的三个位置",
        "rule": "Ｖる＝针还没落下　Ｖている＝停在半空　Ｖた＝刚刚落地",
        "rows": [
            {"mark": "①", "aid": "tokoro1", "jp": "今から出かける<b>ところ</b>です。", "cn": "正要出门（还没出发）"},
            {"mark": "②", "aid": "tokoro2", "jp": "今、昼ご飯を食べている<b>ところ</b>です。", "cn": "正在吃"},
            {"mark": "③", "aid": "c_trio3", "jp": "今、昼ご飯を食べた<b>ところ</b>です。", "cn": "刚吃完"},
        ],
    },
]

# ---------------------------------------------------------------- quiz banks
QA = [
    {"type": "choice", "q": "「〜ことにする」的意思是？", "opts": ["自己拍板决定要…", "组织定下来要…", "长期坚持的习惯"], "ans": 0,
     "exp": "する＝自己决定；「ことになる」才是别人/组织拍板。"},
    {"type": "choice", "q": "「〜ことになる」的意思是？", "opts": ["自己拍板决定要…", "（公司等）定下来要…", "个人坚持的习惯"], "ans": 1,
     "exp": "なる＝变化的结果，决定来自外部。商务通知高频。"},
    {"type": "choice", "q": "「〜ことにしている」的意思是？", "opts": ["一时兴起想做", "长期坚持的个人习惯", "被迫接受的决定"], "ans": 1,
     "exp": "している＝持续状态：把…当成自己的规矩天天做。"},
    {"type": "judge", "q": "先生に質問したところ、丁寧に教えてくれた。<br>（一试才发现老师讲得很耐心）用法正确？", "ans": True,
     "exp": "✓ Ｖた＋ところ＝做了前项，得到了后项结果。"},
    {"type": "judge", "q": "若いうちに、いろいろ挑戦したい。（趁年轻多挑战）用法正确？", "ans": True,
     "exp": "✓ い形＋うちに＝趁着年轻这段窗口期。"},
    {"type": "choice", "q": "「窓を開けたまま寝てしまった。」是一幅怎样的画面？", "opts": ["边开窗边入睡（同时进行）", "窗一直开着的状态下睡着了", "睡前开了又马上关上"], "ans": 1,
     "exp": "まま＝状态被冻结放置；若是同时做两件事才用ながら。"},
    {"type": "choice", "q": "「説明書のとおりに組み立てる。」中とおり表达：", "opts": ["照着说明书的模板做", "说明书之外自由发挥", "和说明书一样多"], "ans": 0,
     "exp": "とおり＝两条轨道完全重合，照模板执行。"},
    {"type": "choice", "q": "「〜たびに」的意思是？", "opts": ["每次…都必然…", "偶尔有时…", "刚刚做完…"], "ans": 0,
     "exp": "たび＝每次触发的按钮，前后都是反复事件。"},
    {"type": "choice", "q": "「健康のため、早寝する。」中的ため表示：", "opts": ["目的：为了健康", "原因：因为健康", "结果：因此很健康"], "ans": 0,
     "exp": "前向箭头＝目标。若是「大雨のため」则是后向的原因用法。"},
    {"type": "choice", "q": "「大雨のため、電車が遅れた。」中的ため表示：", "opts": ["目的", "原因", "比喻"], "ans": 1,
     "exp": "后向箭头＝原因，书面感强，多用于消极事态。"},
    {"type": "judge", "q": "彼は今日休みだ。だから会社にいる<b>はずだ</b>。<br>这个推理正确吗？", "ans": False,
     "exp": "✗ 逻辑自相矛盾：既然休息，推出的应是「いないはずだ」。はず必须顺着线索推。"},
    {"type": "judge", "q": "十年住んでいた。道路に詳しい<b>わけだ</b>。<br>（难怪对道路熟）用法正确？", "ans": True,
     "exp": "✓ 结果在眼前（确实熟），わけだ回头把道理讲通。"},
    {"type": "choice", "q": "「〜わけではない」的否定力度是：", "opts": ["全盘否定：绝对不是", "部分否定：并非那么绝对", "强烈肯定"], "ans": 1,
     "exp": "「話せないわけではない」＝不是不会说（只是…）。留有余地的委婉。"},
    {"type": "choice", "q": "「〜はずがない」的语气是：", "opts": ["不太可能，但也许吧", "根据确凿，绝不可能", "说不定还在"], "ans": 1,
     "exp": "はずがない＝推理链断裂，斩钉截铁的强否定。"},
    {"type": "listen", "aid": "mono2", "q": "听音频，说话人的语气是？",
     "opts": ["怀念过去常来的日子", "命令对方快来店里", "推测将来会常来"], "ans": 0,
     "exp": "「〜たものだ」＝带着温度回忆当年。"},
    {"type": "choice", "q": "「〜ようになる」表示：", "opts": ["能力或习惯发生了变化", "像…一样的比喻", "为了实现某目标"], "ans": 0,
     "exp": "「話せるようになった」＝从不会到会的转变节点。"},
]

QB = [
    {"type": "choice", "q": "彼が倒れる（　　）を見た。", "opts": ["の", "こと", "もの"], "ans": 0,
     "exp": "見た的直接对象是具体场面 → の。✗こと。"},
    {"type": "choice", "q": "漢字を読む（　　）ができます。", "opts": ["こと", "の", "ところ"], "ans": 0,
     "exp": "ことができる 是铁打的固定句型。"},
    {"type": "choice", "q": "風邪が早く治る（　　）薬を飲む。", "opts": ["ように", "ために", "まま"], "ans": 0,
     "exp": "治る＝非意志变化 → ように。ために要配意志动词「治す」。"},
    {"type": "choice", "q": "風邪を治す（　　）薬を飲む。", "opts": ["ために", "ように", "とおりに"], "ans": 0,
     "exp": "治す＝我能控制的意志动作 → ために。"},
    {"type": "choice", "q": "電車に乗り遅れたのは全部君の（　　）だ！<br>（全怪你才没赶上电车！）", "opts": ["せい", "おかげ", "つもり"], "ans": 0,
     "exp": "坏结果甩锅 → せい。夸人就换おかげ。"},
    {"type": "choice", "q": "エアコンをつけた（　　）出かけてしまった。", "opts": ["まま", "とおり", "たび"], "ans": 0,
     "exp": "空调开着没人管 → 状态冻结まま。"},
    {"type": "choice", "q": "彼はもう出発したと言っていたから、もうすぐ着く（　　）だ。", "opts": ["はず", "わけ", "もの"], "ans": 0,
     "exp": "人还没到，靠线索预测 → はず。"},
    {"type": "choice", "q": "毎日ジムに通っていて、体力が明らかについた。よく鍛える（　　）だ。", "opts": ["わけ", "まま", "ため"], "ans": 0,
     "exp": "结果已在眼前，回头讲通道理 → わけだ「难怪」。"},
    {"type": "choice", "q": "思った（　　）の結果になった。", "opts": ["とおり", "まま", "うち"], "ans": 0,
     "exp": "和心中模板重合 → 思ったとおり。"},
    {"type": "choice", "q": "熱い（　　）に召し上がってください。", "opts": ["うち", "よう", "ところ"], "ans": 0,
     "exp": "固定说法 熱いうちに＝趁热。窗口期一旦关闭就凉了。"},
    {"type": "choice", "q": "冗談の（　　）でしたが、怒られてしまいました。", "opts": ["つもり", "ため", "とおり"], "ans": 0,
     "exp": "「たつもり」＝自以为是玩笑，实际效果翻车。"},
    {"type": "choice", "q": "今、昼ご飯を食べている（　　）です。<br>（电话里回答对方：正在吃饭）", "opts": ["ところ", "とき", "うち"], "ans": 0,
     "exp": "「〜ているところです」＝电话应答的标准句型。"},
    {"type": "choice", "q": "学生時代、よく川で泳いだ（　　）だ。", "opts": ["もの", "こと", "わけ"], "ans": 0,
     "exp": "「〜たものだ」＝怀旧滤镜下的当年日常。"},
]

QC = [
    {"type": "listen", "aid": "tokoro1", "q": "听音频：说话人现在处于哪个阶段？",
     "opts": ["正要出门，还没出发", "已经在路上", "刚从外面回来"], "ans": 0,
     "exp": "出ける＋ところ＝针还没落下，正要…"},
    {"type": "listen", "aid": "tokoro3", "q": "听音频：发生了什么？",
     "opts": ["问了老师，得到耐心讲解", "被老师提问，答不上来", "正准备去找老师"], "ans": 0,
     "exp": "Ｖた＋ところ＝一试，得到了后面的结果。"},
    {"type": "listen", "aid": "wake1", "q": "听音频：为什么他对道路很熟？",
     "opts": ["在东京住了十年", "刚搬到东京", "每天都开车上班"], "ans": 0,
     "exp": "住了十年是原因，「詳しいわけだ」是把因果讲通。"},
    {"type": "listen", "aid": "hazu2", "q": "听音频：他今天在公司吗？",
     "opts": ["在", "不可能在", "不知道，没线索"], "ans": 1,
     "exp": "はずがない＝强否定：出差中，绝不可能在。"},
    {"type": "listen", "aid": "uchi2", "q": "听音频：说话人接下来做什么？",
     "opts": ["趁没忘先记下来", "故意不去记", "已经忘记了"], "ans": 0,
     "exp": "忘れないうちに＝趁「还没忘」的窗口期行动。"},
    {"type": "listen", "aid": "you2", "q": "听音频：写在日历上的目的是？",
     "opts": ["为了不忘记", "为了装饰房间", "为了取消约定"], "ans": 0,
     "exp": "〜ない＋ように＝无意志目标「不发生遗忘这件事」。"},
    {"type": "listen", "aid": "sei1", "q": "听音频：头疼的原因是什么？",
     "opts": ["睡眠不足", "感冒发烧", "工作压力大"], "ans": 0,
     "exp": "せいで＝坏结果归罪于睡眠不足。"},
    {"type": "listen", "aid": "toori2", "q": "听音频：考试情况如何？",
     "opts": ["和预想的一样考到了", "一道都没考", "题目难得超出想象"], "ans": 0,
     "exp": "思ったとおり＝现实与脑中模板完全重合。"},
]

QD = [
    {"type": "choice", "q": "私は朝、コーヒーを飲んでから散歩する（　　）にしている。", "opts": ["こと", "もの", "わけ"], "ans": 0,
     "exp": "长期坚持的自我习惯 → ことにしている。"},
    {"type": "choice", "q": "夏休みに海外旅行へ行く（　　）だが、お金が足りるか心配だ。", "opts": ["つもり", "はず", "とおり"], "ans": 0,
     "exp": "心里的计划 → つもりだ。はず需要客观依据。"},
    {"type": "choice", "q": "彼から返事が来ない。メールが届いていない（　　）。", "opts": ["はずだ", "わけだ", "ままだ"], "ans": 0,
     "exp": "还没验证，靠线索推断 → はずだ。わけだ需要结果已出现。"},
    {"type": "choice", "q": "全部あなたが遅刻した（　　）でしょう！", "opts": ["せい", "おかげ", "つもり"], "ans": 0,
     "exp": "愤怒甩锅 → せい。おかげ反着用在讽刺里也可，但此处直白责怪。"},
    {"type": "choice", "q": "この店が人気なのは、値段が安い（　　）だろう。", "opts": ["おかげ", "せい", "つもり"], "ans": 0,
     "exp": "人气是好事 → 归功おかげ。"},
    {"type": "choice", "q": "テレビをつけた（　　）、居間で眠ってしまった。", "opts": ["まま", "ながら", "うち"], "ans": 0,
     "exp": "电视开着没人看 → 冻结状态まま。ながら须两个动作都有意识地进行。"},
    {"type": "choice", "q": "祖母の家に行く（　　）にお菓子をもらう。", "opts": ["たび", "まま", "とおり"], "ans": 0,
     "exp": "每次去都触发 → たびに。"},
    {"type": "choice", "q": "体を鍛える（　　）、ジムに入会した。", "opts": ["ために", "ように", "ままで"], "ans": 0,
     "exp": "鍛える＝意志动词 → ために。"},
    {"type": "choice", "q": "最近、朝早起きできる（　　）になってきた。", "opts": ["よう", "もの", "はず"], "ans": 0,
     "exp": "能力变化的完成 → ようになる。"},
    {"type": "choice", "q": "雲がどんどん増えてきた。午後から降る（　　）だ。", "opts": ["よう", "はず", "わけ"], "ans": 0,
     "exp": "凭眼前景象的直觉判断 → ようだ。はずだ需要逻辑依据（如天气预报）。"},
]

BANKS = {"basic": ("📘 基礎認識", QA), "cmp": ("⚖️ 対比辨析", QB), "listen": ("🎧 聴解", QC), "prod": ("✍️ 运用填空", QD)}

# ---------------------------------------------------------------- audio generation


def gen_one(item):
    sid, s = item
    path = AUDIO_DIR / f"{sid}.mp3"
    if path.exists() and path.stat().st_size > 500:
        return sid, True
    voice = s.get("v", FEMALE)
    cmd = ["edge-tts", "--voice", voice, "--rate=-6%", "--text", s["jp"],
           "--write-media", str(path)]
    for attempt in (1, 2):
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > 500:
            return sid, True
    return sid, False


def gen_audio():
    AUDIO_DIR.mkdir(exist_ok=True)
    todo = [(sid, s) for sid, s in SENTS.items()]
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(gen_one, todo))
    failed = [sid for sid, ok in results if not ok]
    if failed:
        print("AUDIO FAILED:", failed)
        sys.exit(1)


def audio_map():
    m = {}
    for sid in SENTS:
        p = AUDIO_DIR / f"{sid}.mp3"
        b64 = base64.b64encode(p.read_bytes()).decode()
        m[sid] = f"data:audio/mpeg;base64,{b64}"
    return m


# ---------------------------------------------------------------- html template
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>形式名词 · 十三杰完全图鉴</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#4f6ef7;--acc2:#eef1ff;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;
--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#33418f,#7a4ff7);color:#fff;padding:26px 20px 20px}
header h1{font-size:25px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);
border-radius:99px;padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 16px;position:relative;z-index:2}
nav button{flex:1;border:none;border-radius:12px;padding:12px 2px;font-size:14px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:1.7;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
/* map */
.intro{background:linear-gradient(135deg,#eef1ff,#fdf3ff)}
.intro h2{font-size:17px;margin-bottom:8px;color:var(--acc)}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:var(--acc)}
.grp{margin-bottom:16px}
.grp-h{color:#fff;border-radius:14px;padding:12px 16px;display:flex;justify-content:space-between;align-items:baseline}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.grp-d{font-size:12.5px;color:var(--sub);padding:8px 4px 0}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 0 2px}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--g,#4f6ef7)}
.mini .em{font-size:26px}
.mini .nm{font-weight:800;font-size:16px;margin:4px 0 2px}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
/* study */
.noun{scroll-margin-top:70px;border-left:5px solid var(--g,#4f6ef7)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:var(--acc2);color:var(--acc);border-radius:99px;padding:2px 10px;font-weight:600}
.imgbox{background:var(--g-bg,#eef1ff);border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.7;margin:10px 0}
.attach{margin:8px 0}
.attach code{display:inline-block;background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:3px 9px;font-size:12.5px;margin:2px 4px 2px 0}
.ptab{width:100%;border-collapse:collapse;margin:8px 0;font-size:13.5px}
.ptab td{border-top:1px solid var(--line);padding:7px 4px;vertical-align:top}
.ptab td:first-child{white-space:nowrap;font-family:"Hiragino Mincho ProN",serif;color:var(--acc);font-weight:700}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
/* compare */
.ctitle{font-size:17px;color:var(--ink)} .ctitle small{color:var(--sub);font-weight:400;margin-left:8px}
.crule{background:#f2f4fa;border-radius:10px;padding:8px 12px;font-size:13px;margin:10px 0;line-height:1.6}
.crow{display:flex;gap:10px;padding:10px 4px;border-bottom:1px dashed var(--line);align-items:flex-start}
.crow:last-child{border-bottom:none}
.mark{flex:none;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-weight:800;font-size:15px}
.mk-o{background:var(--okbg);color:var(--ok)} .mk-x{background:var(--ngbg);color:var(--ng)}
.mk-t{background:var(--acc2);color:var(--acc)}
.crow .tx{flex:1}
/* quiz */
.q{font-size:16.5px;line-height:1.75;margin-bottom:14px}
.opt{display:block;width:100%;text-align:left;padding:12px 14px;margin:8px 0;font-size:15.5px;
border-radius:12px;border:2px solid var(--line);background:#fff;cursor:pointer;line-height:1.5}
.opt:hover:not(:disabled){border-color:var(--acc)}
.opt.right{border-color:var(--ok);background:var(--okbg)}
.opt.wrong{border-color:var(--ng);background:var(--ngbg)}
.opt:disabled{cursor:default;opacity:.92}
.exp{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:14px;line-height:1.65}
.exp.ok{background:var(--okbg);color:var(--ok)} .exp.ng{background:var(--ngbg);color:var(--ng)}
.judgebtns{display:flex;gap:12px}
.judgebtns .opt{flex:1;text-align:center;font-size:18px}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px;z-index:5}
.bar .wrap{display:flex;justify-content:space-between;align-items:center}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
.linky{color:var(--acc);cursor:pointer;text-decoration:underline}
</style>
</head>
<body>
<header><div class="wrap">
<h1>形式名詞 ・ 十三杰完全图鉴</h1>
<div class="kana">13 个「空心名词」＝把句子打包成名词的语法胶水</div>
<div class="tags"><span>N4–N3</span><span>文法体系</span><span>13词×53音声例句</span><span>TTS×MOJi</span></div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">离线可用 · 点击🔊播放发音</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const SENTS=__SENTS__;
const GROUPS=__GROUPS__;
const NOUNS=__NOUNS__;
const CONTRASTS=__CONTRASTS__;
const BANKS=__BANKS__;
const $=s=>document.querySelector(s);
let curAudio=null,curBtn=null;
function play(id,btn){
  if(curAudio){curAudio.pause();curAudio.currentTime=0;}
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('playing'));
  curAudio=new Audio(AUDIO[id]);curBtn=btn||null;
  if(curBtn){curBtn.classList.add('playing');curAudio.onended=()=>curBtn.classList.remove('playing');}
  curAudio.play();
}
function rowHTML(id){
  const s=SENTS[id];
  return `<div class="row"><button class="btn" onclick="play('${id}',this)">▶</button>
  <div><div class="jp">${s.jp}</div><div class="cn">${s.cn}</div></div></div>`;
}

/* ---------- tabs ---------- */
const TABS=[["map","🗺️ 地図"],["study","📖 詳解"],["cmp","⚖️ 対比"],["sent","🗂️ 例句"],["quiz","🎯 クイズ"]];
let tab="map";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goStudy(nid){goTab('study');setTimeout(()=>{const el=document.getElementById('n-'+nid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- map ---------- */
function renderMap(){
  let h=`<div class="card intro"><h2>什么是「形式名词」？</h2>
  <p>它们是<b>失去了实质意义的空心名词</b>：自己不装内容，专门给前面的从句当「外壳」——
  把动作打包成名词、给事实贴上判断、给时间钉上坐标。中文没有完全对应的词类，
  所以<b>不要背中文释义，要背「接续框架」</b>：看见框架 → 想起它的功能。</p>
  <div class="steps">
    <div><b>STEP 1 认意象</b><br>每个词有一个核心画面，先让右脑记住它的「形状」。</div>
    <div><b>STEP 2 记框架</b><br>背句型接续而不是单词本身——大脑存的是整块结构。</div>
    <div><b>STEP 3 做对比</b><br>近义最小对立句反复听读，边界自然清晰。</div>
    <div><b>STEP 4 过四关</b><br>クイズ按「认识→辨析→听解→运用」分层检索，错题自动进错题本。</div>
  </div></div>`;
  h+=GROUPS.map(g=>{
    const c=g.color;
    return `<div class="grp"><div class="grp-h" style="background:${c}"><h3>${g.name}</h3><span>${g.nouns.length} 词</span></div>
    <div class="grp-d">${g.desc}</div>
    <div class="mini-wrap">${g.nouns.map(nid=>{
      const n=NOUNS[nid];
      return `<button class="mini" style="--g:${c}" onclick="goStudy('${nid}')">
        <div class="em">${n.emoji}</div><div class="nm">${n.name} <small style="color:${c};font-size:10.5px">${n.jlpt}</small></div>
        <div class="im">${n.img.split("：")[0]}</div></button>`;
    }).join("")}</div></div>`;
  }).join("");
  $("#main").innerHTML=h;
}

/* ---------- study ---------- */
function renderStudy(){
  let h="";
  GROUPS.forEach(g=>{
    h+=`<h3 class="sec" style="border-left:4px solid ${g.color};padding-left:8px;color:${g.color}">${g.name} — ${g.desc}</h3>`;
    g.nouns.forEach(nid=>{
      const n=NOUNS[nid],c=g.color;
      const bg=n.group+"Bg";
      h+=`<div class="card noun" id="n-${nid}" style="--g:${c}">
        <h2>${n.emoji} ${n.name}<span class="jl">${n.jlpt}</span></h2>
        <div class="imgbox" style="--g-bg:${c}14">💡 <b>核心意象</b>　${n.img}</div>
        <div class="attach">${n.attach.map(a=>`<code>${a}</code>`).join("")}</div>
        <table class="ptab">${n.patterns.map(p=>`<tr><td>${p[0]}</td><td>${p[1]}</td></tr>`).join("")}</table>
        ${n.sents.map(rowHTML).join("")}
        <div class="note">💡 ${n.note}</div>
      </div>`;
    });
  });
  h+=`<div class="card noun" style="--g:#666"><h2>番外・次第</h2>
  <div class="imgbox">📦 你已经学过第14个成员：<span class="linky" onclick="alert('打开同系列的「次第」课件复习：\\nscratch/shidai-courseware/index.html')">次第</span>
  （ます形＋次第＝一…就；名词＋次第だ＝全凭…）——它也是形式名词家族的一员。</div></div>`;
  $("#main").innerHTML=h;
}

/* ---------- compare ---------- */
function renderCompare(){
  $("#main").innerHTML=`<div class="card intro"><h2>为什么对比？</h2>
  <p>大脑靠「差异」划清概念边界。下面每组都是<b>最小对立句</b>——只差一个词，意思就翻面。
  先遮住解释，自己判断 ○✗，再点🔊对照。</p></div>`+
  CONTRASTS.map(c=>`<div class="card">
    <div class="ctitle">${c.title}<small>${c.sub}</small></div>
    <div class="crule">${c.rule}</div>
    ${c.rows.map(r=>`<div class="crow">
      <div class="mark mk-${r.mark==='○'?'o':r.mark==='✗'?'x':'t'}">${r.mark}</div>
      <div class="tx"><div class="jp">${r.jp}</div><div class="cn">${r.cn}</div></div>
      ${r.aid?`<button class="btn" onclick="play('${r.aid}',this)">▶</button>`:""}
    </div>`).join("")}
  </div>`).join("");
}

/* ---------- sentences ---------- */
function renderSent(){
  let h="";
  GROUPS.forEach(g=>{
    g.nouns.forEach(nid=>{
      const n=NOUNS[nid];
      h+=`<h3 class="sec">${n.emoji} ${n.name} — ${n.img.split("：")[0]}</h3>
      <div class="card">${n.sents.map(rowHTML).join("")}</div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
let mode=null,order=[],qi=0,correct=0,answered=false,bankArr=[];
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("keishiku-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("keishiku-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("keishiku-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);

function bankListHTML(){
  const rows=[["basic"],["cmp"],["listen"],["prod"]].map(([k])=>{
    const[label,arr]=BANKS[k];
    return `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${arr.length}問</button>`;
  }).join("");
  const wc=wrongCount();
  const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
    `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
  return `<div class="card" style="text-align:center;padding:28px 16px">
    <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
    <div class="hint" style="margin-bottom:18px">四层难度对应认知阶梯 · 全部随机打乱 · 即时判分讲解</div>
    ${rows}${wrongRow}
    <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机<br><span style="font-size:12px;color:var(--sub)">跨词交错练习，记忆更牢固</span></button>
    ${wc?`<button class="opt" style="max-width:200px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('keishiku-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
  </div>`;
}
function buildMix(){
  const all=[];let i=0;
  for(const k of ["basic","cmp","listen","prod"])for(let j=0;j<BANKS[k][1].length;j++)all.push({bank:k,i:j});
  return all;
}
function renderQuizTab(){ if(!mode){showNext(false);$("#score").textContent="";$("#main").innerHTML=bankListHTML();return;} startQuiz(mode); }
function startQuiz(m){
  mode=m;
  if(m==="mix")bankArr=buildMix();
  else if(m==="wrong"){bankArr=Object.keys(wrongBook()).map(ref=>{const[b,i]=ref.split(":");return{bank:b,i:+i}});}
  else bankArr=BANKS[m][1].map((_,i)=>({bank:m,i}));
  order=shuffle(bankArr.map((_,x)=>x));
  qi=0;correct=0;answered=false;renderQuiz();
}
function curQ(){return BANKS[bankArr[order[qi]].bank][1][bankArr[order[qi]].i];}
function curRef(){return `${bankArr[order[qi]].bank}:${bankArr[order[qi]].i}`;}
function renderQuiz(){
  const q=curQ();updateScore();
  let body="";
  if(q.type==="listen"){
    body=`<div style="text-align:center;margin:6px 0 14px">
      <button class="btn" style="width:56px;height:56px;font-size:24px;margin:auto" onclick="play('${q.aid}',this)">🔊</button>
      <div class="hint">可反复点击重听</div></div>`;
  }
  let optHTML="";
  if(q.opts){
    const opts=shuffle(q.opts.map((t,i)=>({t,ok:i===q.ans})));
    optHTML=opts.map(o=>`<button class="opt" data-ok="${o.ok?1:0}" onclick="pick(this)">${o.t}</button>`).join("");
  }else{
    const truthy=q.ans===true;
    optHTML=`<div class="judgebtns">
      <button class="opt" data-ok="${truthy?1:0}" onclick="pick(this)">⭕ 正しい</button>
      <button class="opt" data-ok="${truthy?0:1}" onclick="pick(this)">❌ 間違い</button></div>`;
  }
  showNext(false);
  $("#main").innerHTML=`<div class="card">
    <div class="hint">第 ${qi+1} 题 / 共 ${order.length} 题 · ${mode==="mix"?"混合":mode==="wrong"?"错题本":""}</div>
    <div class="q">${q.q}</div>${body}${optHTML}
    <div id="fb"></div></div>`;
}
function showNext(v){const b=$("#next");b.style.display=v?"inline-block":"none";b.disabled=!v;}
function pick(btn){
  if(answered)return;answered=true;
  const q=curQ(),ref=curRef();
  const ok=btn.dataset.ok==="1";
  if(ok)correct++;else addWrong(ref);
  if(ok&&mode==="wrong")delWrong(ref);
  document.querySelectorAll(".opt").forEach(b=>{b.disabled=true;if(b.dataset.ok==="1")b.classList.add("right");});
  if(!ok)btn.classList.add("wrong");
  $("#fb").innerHTML=`<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！"}${q.exp}</div>`;
  showNext(true);updateScore();
  window.scrollTo(0,document.body.scrollHeight);
}
function nextQ(){
  answered=false;qi++;
  if(qi>=order.length)finish();else renderQuiz();
}
function finish(){
  showNext(false);
  const total=order.length,pct=Math.round(correct/total*100);
  const msg=pct===100?"🏆 完璧！形式名詞マスター！":pct>=70?"👍 かなりいい！錯題を潰そう":"📖 詳解タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">别的关卡选一选</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正确率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){mode=null;render();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="map")renderMap();else if(tab==="study")renderStudy();
  else if(tab==="cmp")renderCompare();else if(tab==="sent")renderSent();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    print(f"[1/3] ensuring audio ({len(SENTS)} clips, cached ones skipped)...")
    gen_audio()
    print("[2/3] embedding audio as base64...")
    html = (TEMPLATE
            .replace("__AUDIO__", json.dumps(audio_map()))
            .replace("__SENTS__", json.dumps(SENTS, ensure_ascii=False))
            .replace("__GROUPS__", json.dumps(GROUPS, ensure_ascii=False))
            .replace("__NOUNS__", json.dumps(NOUNS, ensure_ascii=False))
            .replace("__CONTRASTS__", json.dumps(CONTRASTS, ensure_ascii=False))
            .replace("__BANKS__", json.dumps(
                {k: (label, qs) for k, (label, qs) in BANKS.items()}, ensure_ascii=False)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[3/3] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
