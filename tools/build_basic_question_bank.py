"""Build the playable bank of textbook-context word questions.

The supplied Word files are kept as source material.  This file is the
reviewed, normalized source for the first demo bank: one sentence, one target
word, one correct gloss, and three fixed distractors.  The generated JSON is
what the static frontend reads.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from optional_question_bank import OPTIONAL_CATALOG, OPTIONAL_SPECS


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "questions.json"
EXPANDED_SPECS = ROOT / "data" / "expanded_question_specs.json"


BASE_CATALOG = [
    {"id": "bx1_quxue", "volume": "必修上册", "unit": "第三单元", "title": "劝学", "author": "荀子"},
    {"id": "bx1_shishuo", "volume": "必修上册", "unit": "第六单元", "title": "师说", "author": "韩愈"},
    {"id": "bx1_chibifu", "volume": "必修上册", "unit": "第七单元", "title": "赤壁赋", "author": "苏轼"},
    {"id": "bx1_dengtaishanji", "volume": "必修上册", "unit": "第七单元", "title": "登泰山记", "author": "姚鼐"},
    {"id": "bx2_shizuo", "volume": "必修下册", "unit": "第一单元", "title": "子路、曾皙、冉有、公西华侍坐", "author": "《论语》"},
    {"id": "bx2_qihuanjinwen", "volume": "必修下册", "unit": "第一单元", "title": "齐桓晋文之事", "author": "《孟子》"},
    {"id": "bx2_paoding", "volume": "必修下册", "unit": "第二单元", "title": "庖丁解牛", "author": "《庄子》"},
    {"id": "bx2_zhuwuzhiqin", "volume": "必修下册", "unit": "第三单元", "title": "烛之武退秦师", "author": "《左传》"},
    {"id": "bx2_hongmenyan", "volume": "必修下册", "unit": "第三单元", "title": "鸿门宴", "author": "司马迁"},
    {"id": "bx2_jian_taizong", "volume": "必修下册", "unit": "第四单元", "title": "谏太宗十思疏", "author": "魏征"},
    {"id": "bx2_dasima", "volume": "必修下册", "unit": "第八单元", "title": "答司马谏议书", "author": "王安石"},
    {"id": "bx2_afanggongfu", "volume": "必修下册", "unit": "第八单元", "title": "阿房宫赋", "author": "杜牧"},
    {"id": "bx2_liuguolun", "volume": "必修下册", "unit": "第八单元", "title": "六国论", "author": "苏洵"},
]

CATALOG = BASE_CATALOG + OPTIONAL_CATALOG

ARTICLE_BY_ID = {item["id"]: item for item in CATALOG}


def q(article_id: str, word: str, sentence: str, correct: str, distractors: list[str], explanation: str) -> dict:
    if not word.strip() or any(char.isspace() for char in word):
        raise ValueError(f"{article_id}/{word}: target must be a lexical unit without whitespace")
    if len(distractors) != 3:
        raise ValueError(f"{article_id}/{word}: exactly three distractors are required")
    target_start = sentence.find(word)
    if target_start < 0:
        raise ValueError(f"{article_id}/{word}: target does not occur in sentence")
    article = ARTICLE_BY_ID[article_id]
    return {
        "id": "",
        "type": "context_meaning",
        "articleId": article_id,
        "volume": article["volume"],
        "unit": article["unit"],
        "article": article["title"],
        "word": word,
        "sentence": sentence,
        "targetStart": target_start,
        "targetOccurrence": 1,
        "correctMeaning": correct,
        "distractorMeanings": distractors,
        "explanation": explanation,
        "reviewStatus": "verified",
        "source": {
            "kind": "textbook_word_bank_reviewed",
            "title": "统编版课内文章与教材释义资料（经人工复核）",
        },
    }


# The original 100 questions are followed by the 160 selective-compulsory
# questions from optional_question_bank.py.  The four options are intentionally
# stored as meanings, not
# as pre-positioned A/B/C/D answers.  The browser shuffles their positions.
ROWS = [
    # 必修上册《劝学》 8
    q("bx1_quxue", "利", "金就砺则利。", "变锋利", ["有利的条件", "利益、好处", "使……获利"], "“利”是形容词作动词，指变得锋利。"),
    q("bx1_quxue", "利", "非利足也，而致千里。", "使……走得快", ["锋利", "有利于", "利益、好处"], "“利足”意为使脚走得快。"),
    q("bx1_quxue", "假", "善假于物也。", "借助", ["假装", "借给", "虚假的"], "“假”是借助、利用的意思。"),
    q("bx1_quxue", "疾", "声非加疾也。", "强、洪亮", ["疾病", "快速", "痛苦"], "这里说声音并没有变得更强，疾为强、洪亮。"),
    q("bx1_quxue", "绝", "而绝江河。", "横渡", ["断绝", "与世隔绝的地方", "极、非常"], "“绝江河”是横渡江河。"),
    q("bx1_quxue", "强", "筋骨之强。", "强壮", ["有余", "勉强", "强迫"], "“强”形容筋骨强壮。"),
    q("bx1_quxue", "日", "君子博学而日参省乎己，则知明而行无过矣。", "每天", ["太阳", "日期", "一天天地增长"], "“日”作状语，表示每天。"),
    q("bx1_quxue", "假", "以是人多以书假余。", "借", ["借助", "假装", "给予"], "“假”是借用、借来的意思。"),

    # 必修上册《师说》 8
    q("bx1_shishuo", "传", "所以传道、受业、解惑也。", "传授", ["流传", "传记", "传达命令"], "这里是传授道理、教授学业。"),
    q("bx1_shishuo", "传", "师道之不传也久矣。", "流传", ["传授", "传记", "传达命令"], "这里指从师学习的风尚没有流传。"),
    q("bx1_shishuo", "惑", "所以传道受业解惑也。", "疑难问题", ["糊涂", "迷惑别人", "疑问的语气"], "“惑”作名词，指疑难问题。"),
    q("bx1_shishuo", "惑", "于其身也，则耻师焉，惑矣。", "糊涂", ["疑难问题", "迷惑别人", "疑问的语气"], "“惑”作形容词，指糊涂。"),
    q("bx1_shishuo", "道", "吾师道也。", "道理", ["风尚", "道路", "谈论"], "“道”指道理、学问。"),
    q("bx1_shishuo", "道", "师道之不传也久矣。", "风尚", ["道理", "道路", "谈论"], "“师道”指从师学习的风尚。"),
    q("bx1_shishuo", "师", "古之学者必有师。", "老师", ["学习", "以……为老师", "从师"], "“师”是老师的意思。"),
    q("bx1_shishuo", "师", "吾从而师之。", "以……为老师", ["老师", "学习", "从师"], "“师”是意动用法，以……为老师。"),

    # 必修上册《赤壁赋》 8
    q("bx1_chibifu", "望", "七月既望。", "农历每月十五日", ["远望、眺望", "盼望", "名望、声望"], "“望”指农历每月十五日。"),
    q("bx1_chibifu", "望", "望美人兮天一方。", "眺望、向远处看", ["农历十五日", "盼望得到宠幸", "名望、声望"], "这里是向远处眺望。"),
    q("bx1_chibifu", "歌", "扣舷而歌之。", "唱歌", ["歌词", "歌声", "诗歌体裁"], "“歌”作动词，指唱歌。"),
    q("bx1_chibifu", "歌", "歌曰：‘桂棹兮兰桨。’", "歌词", ["唱歌", "歌声", "诗歌体裁"], "“歌”作名词，指歌词。"),
    q("bx1_chibifu", "如", "纵一苇之所如。", "往、到", ["像、如同", "比得上", "及、赶得上"], "“如”是往、到的意思。"),
    q("bx1_chibifu", "如", "浩浩乎如冯虚御风。", "像、如同", ["往、到", "比得上", "及、赶得上"], "“如”表示像、如同。"),
    q("bx1_chibifu", "然", "其声呜呜然。", "……的样子", ["这样", "然而", "正确、对"], "“然”是形容词词尾，表示……的样子。"),
    q("bx1_chibifu", "长", "而卒莫消长也。", "增长", ["永远", "长大、成长", "擅长"], "“长”作动词，指增长。"),

    # 必修上册《登泰山记》 6
    q("bx1_dengtaishanji", "当", "当其南北分者。", "在、在……的地方", ["挡住", "面对", "应当"], "“当”是介词，在、在……的地方。"),
    q("bx1_dengtaishanji", "当", "崖限当道者。", "挡住", ["在、在……的地方", "面对", "应当"], "“当道”是挡住道路。"),
    q("bx1_dengtaishanji", "限", "越长城之限。", "界限", ["门槛", "限制、限定", "险要的地方"], "“限”指界限。"),
    q("bx1_dengtaishanji", "限", "崖限当道者。", "门槛", ["界限", "限制、限定", "险要的地方"], "这里把山崖比作门槛。"),
    q("bx1_dengtaishanji", "及", "今所经中岭及山巅。", "和、以及", ["等到", "赶得上", "涉及、到达"], "“及”是连词，和、以及。"),
    q("bx1_dengtaishanji", "及", "及既上。", "等到", ["和、以及", "赶得上", "涉及、到达"], "“及”是介词，等到。"),

    # 必修下册《侍坐》 5
    q("bx2_shizuo", "方", "方六七十，如五六十。", "方圆，纵横", ["道义、准则", "正当、正在", "方才"], "“方六七十”指纵横六七十里。"),
    q("bx2_shizuo", "方", "且知方也。", "道义、准则", ["方圆，纵横", "正当、正在", "方才"], "“方”指道义、准则。"),
    q("bx2_shizuo", "尔", "以吾一日长乎尔。", "你们", ["……的样子", "这样", "你的"], "“尔”是第二人称代词，你们。"),
    q("bx2_shizuo", "尔", "子路率尔而对曰。", "……的样子", ["你们", "这样", "你的"], "“尔”作词尾，表示……的样子。"),
    q("bx2_shizuo", "言", "夫三子者之言何如。", "话、言论", ["说、谈", "诺言、誓言", "语言文字"], "“言”作名词，指话、言论。"),

    # 必修下册《齐桓晋文之事》 5
    q("bx2_qihuanjinwen", "道", "仲尼之徒无道桓文之事者。", "谈论", ["道理", "道路", "风尚"], "“道”作动词，指谈论。"),
    q("bx2_qihuanjinwen", "若", "若无罪而就死地。", "如果", ["像、如同", "你、你们", "比得上"], "“若”表示假设，如果。"),
    q("bx2_qihuanjinwen", "明", "明足以察秋毫之末。", "视力", ["明亮", "明白、清楚", "英明、明智"], "“明”指视力。"),
    q("bx2_qihuanjinwen", "许", "则王许之乎。", "赞许、同意", ["答应请求", "表示约数", "处所"], "“许”是赞许、认可。"),
    q("bx2_qihuanjinwen", "及", "老吾老以及人之老。", "推及、推广到", ["赶得上", "等到", "和、以及"], "“及”是推及、推广到。"),

    # 必修下册《庖丁解牛》 5
    q("bx2_paoding", "族", "族庖月更刀。", "一般的、普通的", ["筋骨交错的地方", "类、同类", "灭族"], "“族庖”指一般的厨工。"),
    q("bx2_paoding", "族", "每至于族。", "筋骨交错的地方", ["一般的、普通的", "类、同类", "灭族"], "“族”指筋骨交错聚结的地方。"),
    q("bx2_paoding", "道", "臣之所好者道也。", "规律、道理", ["道路", "谈论", "风尚"], "这里的“道”指事物的规律。"),
    q("bx2_paoding", "理", "依乎天理。", "天然的结构", ["道理、规律", "治理、管理", "纹理、花纹"], "“天理”指牛体天然的结构。"),
    q("bx2_paoding", "善", "善刀而藏之。", "通“缮”，擦拭、揩拭", ["善良、善于", "友好、亲善", "认为好"], "“善”通“缮”，指擦拭刀。"),

    # 必修下册《烛之武退秦师》 6
    q("bx2_zhuwuzhiqin", "贰", "以其无礼于晋，且贰于楚也。", "从属二主", ["两次、再三", "背叛、离心", "疑惑、怀疑"], "“贰”指对晋国有二心，从属二主。"),
    q("bx2_zhuwuzhiqin", "鄙", "越国以鄙远。", "把……当作边邑", ["边远的地方", "浅陋、庸俗", "轻视、看不起"], "“鄙”是名词的意动用法，把远方的土地当作边邑。"),
    q("bx2_zhuwuzhiqin", "阙", "若不阙秦，将焉取之。", "使……削减、侵损", ["宫殿", "缺少、不足", "挖掘、开凿"], "“阙”是使动用法，使秦国的土地削减。"),
    q("bx2_zhuwuzhiqin", "微", "微夫人之力不及此。", "如果没有", ["微小、细小", "隐蔽、不显露", "稍微"], "“微”表示假设，如果没有。"),
    q("bx2_zhuwuzhiqin", "敝", "因人之力而敝之。", "损害", ["破旧、破败", "疲惫", "衰败、衰落"], "“敝”是使动意义，损害别人。"),
    q("bx2_zhuwuzhiqin", "许", "许君焦、瑕。", "答应、听从", ["赞许、同意", "表示约数", "处所"], "“许”是答应。"),

    # 必修下册《鸿门宴》 15
    q("bx2_hongmenyan", "如", "杀人如不能举，刑人如恐不胜。", "好像、如同", ["往、到", "比得上", "及、赶得上"], "两个“如”都是好像、如同。"),
    q("bx2_hongmenyan", "如", "沛公起如厕。", "往、到……去", ["好像、如同", "比得上", "及、赶得上"], "“如厕”是到厕所去。"),
    q("bx2_hongmenyan", "如", "固不如也。", "比得上", ["好像、如同", "往、到……去", "及、赶得上"], "“不如”是比不上。"),
    q("bx2_hongmenyan", "意", "其意常在沛公也。", "意图、意愿", ["料想、猜测", "心情、情绪", "意思、意义"], "“意”指意图、意愿。"),
    q("bx2_hongmenyan", "意", "然不自意能先入关破秦。", "料想、料定", ["意图、意愿", "心情、情绪", "意思、意义"], "“意”是料想、料定。"),
    q("bx2_hongmenyan", "举", "举所佩玉玦以示之者三。", "举起", ["全、尽", "推荐、选拔", "发动、举行"], "“举”是举起。"),
    q("bx2_hongmenyan", "举", "杀人如不能举。", "全、尽", ["举起", "推荐、选拔", "发动、举行"], "“举”指全部、尽。"),
    q("bx2_hongmenyan", "谢", "旦日不可不蚤自来谢项王。", "谢罪、道歉", ["感谢", "告辞、告别", "推辞、拒绝"], "“谢”是道歉、谢罪。"),
    q("bx2_hongmenyan", "谢", "哙拜谢，起，立而饮之。", "感谢", ["谢罪、道歉", "告辞、告别", "推辞、拒绝"], "“谢”是感谢。"),
    q("bx2_hongmenyan", "军", "沛公军霸上。", "驻军、驻扎", ["军队", "军营", "军官"], "“军”作动词，指驻军。"),
    q("bx2_hongmenyan", "军", "从此道至吾军。", "军营", ["驻军、驻扎", "军队", "军官"], "“军”指军营。"),
    q("bx2_hongmenyan", "幸", "妇女无所幸。", "宠爱、宠幸", ["幸亏", "有幸、幸运", "希望"], "“幸”指帝王对女子的宠爱。"),
    q("bx2_hongmenyan", "故", "故遣将守关者。", "特意", ["交情、缘故", "所以", "旧的、原来的"], "“故”是特意。"),
    q("bx2_hongmenyan", "坐", "因击沛公于坐。", "座位", ["坐下", "犯罪、获罪", "因为"], "“坐”通“座”，指座位。"),
    q("bx2_hongmenyan", "从", "沛公旦日从百余骑来见项王。", "使……跟随、带领", ["跟随", "由、自", "向……学习"], "“从”是使动用法，使百余名骑兵跟随。"),

    # 必修下册《谏太宗十思疏》 10
    q("bx2_jian_taizong", "固", "必固其根本。", "使……牢固", ["本来、原来", "坚固的地势", "坚持、坚决"], "“固”是使动用法，使根本牢固。"),
    q("bx2_jian_taizong", "安", "思国之安者，必积其德义。", "安定", ["怎么", "哪里", "使……安宁"], "“安”指国家安定。"),
    q("bx2_jian_taizong", "诚", "必竭诚以待下。", "诚心、真心", ["的确、确实", "如果、果真", "诚实、信用"], "“诚”指诚心、真心。"),
    q("bx2_jian_taizong", "下", "虑壅蔽，则思虚心以纳下。", "臣下的意见", ["向下、低处", "智力低下", "居于……之下"], "“下”指臣下的意见。"),
    q("bx2_jian_taizong", "信", "信者效其忠。", "诚实、诚信", ["信任", "相信", "书信、消息"], "“信”指诚实守信。"),
    q("bx2_jian_taizong", "求", "求木之长者，必固其根本。", "追求", ["探求、探寻", "请求", "寻找、搜寻"], "“求”是追求。"),
    q("bx2_jian_taizong", "治", "文武并用，垂拱而治。", "治理得好、天下太平", ["医治疾病", "惩治、治罪", "研究、学习"], "“治”指治理得好，天下太平。"),
    q("bx2_jian_taizong", "明", "而况于明哲乎。", "英明、明智", ["视力", "明亮", "明白、清楚"], "“明”是英明、明智。"),
    q("bx2_jian_taizong", "从", "择善而从之。", "采纳、听从", ["跟随", "使……跟随", "由、自"], "“从”是采纳、听从。"),
    q("bx2_jian_taizong", "危", "居安思危。", "危险", ["高处", "忧虑、担心", "危害、伤害"], "“危”是危险。"),

    # 必修下册《答司马谏议书》 6
    q("bx2_dasima", "受", "受命于人主。", "接受", ["遭受", "忍受", "受到教育"], "“受命”是接受皇帝的命令。"),
    q("bx2_dasima", "修", "议法度而修之于朝廷。", "修改", ["修建", "修养自身", "长、高"], "“修”是修改。"),
    q("bx2_dasima", "举", "举先王之政。", "施行、推行", ["举起", "推荐、选拔", "全、尽"], "“举”是施行先王的政策。"),
    q("bx2_dasima", "为", "不为侵官。", "是、算作", ["成为", "替、给", "做、担任"], "“为”与“是”相近，表示算作。"),
    q("bx2_dasima", "固", "则固前知其如此也。", "本来、原来", ["使……牢固", "坚决坚持", "坚固的地势"], "“固”是本来、原来。"),
    q("bx2_dasima", "怨", "至于怨诽之多，则固前知其如此也。", "怨恨、责怪", ["忧愁、忧虑", "报仇、复仇", "过错、罪过"], "“怨”指怨恨、责怪。"),

    # 必修下册《阿房宫赋》 8
    q("bx2_afanggongfu", "爱", "秦爱纷奢，人亦念其家。", "喜爱、喜欢", ["爱护", "吝惜、舍不得", "爱慕、仰慕"], "“爱”是喜爱、喜欢。"),
    q("bx2_afanggongfu", "爱", "使秦复爱六国之人。", "爱护", ["喜爱、喜欢", "吝惜、舍不得", "爱慕、仰慕"], "“爱”是爱护。"),
    q("bx2_afanggongfu", "取", "奈何取之尽锱铢，用之如泥沙。", "夺取、掠夺", ["提取", "选取、选择", "取得、获得"], "“取”是夺取、掠夺。"),
    q("bx2_afanggongfu", "族", "族秦者秦也，非天下也。", "灭族、使……灭族", ["类、同类", "一般的、普通的", "筋骨交错的地方"], "“族”是使动用法，使秦国灭族。"),
    q("bx2_afanggongfu", "尽", "一肌一容，尽态极妍。", "极尽、达到顶点", ["全部、全都", "取尽、用尽", "完毕、结束"], "“尽”是副词，达到极点。"),
    q("bx2_afanggongfu", "使", "使秦复爱六国之人。", "假使", ["让、使得", "使者", "出使"], "这里的“使”是假使、如果。"),
    q("bx2_afanggongfu", "为", "朝歌夜弦，为秦宫人。", "成为", ["做、担任", "是、算作", "替、给"], "“为”是成为。"),
    q("bx2_afanggongfu", "直", "直栏横槛，多于九土之城郭。", "与横相对，竖直", ["正直、公正", "价值、价钱", "径直、直接"], "“直”与“横”相对，指竖直。"),

    # 必修下册《六国论》 10
    q("bx2_liuguolun", "兵", "非兵不利。", "兵器、武器", ["军队", "战争", "士兵"], "“兵”指兵器。"),
    q("bx2_liuguolun", "兵", "而秦兵又至矣。", "军队", ["兵器、武器", "战争", "士兵"], "“兵”指军队。"),
    q("bx2_liuguolun", "事", "以地事秦。", "侍奉", ["事情", "从事、办理", "事件、事故"], "“事”作动词，指侍奉。"),
    q("bx2_liuguolun", "故事", "而从六国破亡之故事。", "旧事、前例", ["事情、事件", "讲故事", "原因、缘故"], "“故事”指旧事、前例。"),
    q("bx2_liuguolun", "犹", "犹抱薪救火。", "像、如同", ["仍然、还", "尚且", "犹豫不决"], "“犹”是像、如同。"),
    q("bx2_liuguolun", "终", "惜其用武而不终也。", "坚持到最后", ["终于", "最终、结果", "结束、死亡"], "“终”作动词，指坚持到最后。"),
    q("bx2_liuguolun", "始", "始有远略。", "起初、开始", ["才", "一直、始终", "最初的地方"], "“始”是起初、开始。"),
    q("bx2_liuguolun", "向", "向使三国各爱其地。", "假使、如果", ["朝着、面对", "从前、过去", "向来、一直"], "“向使”是假使、如果。"),
    q("bx2_liuguolun", "势", "有如此之势。", "形势、情况", ["势力、力量", "姿态、姿势", "权势、地位"], "这里指形势、情况。"),
    q("bx2_liuguolun", "亡", "是故燕虽小国而后亡。", "灭亡", ["失去", "逃跑", "死亡"], "“亡”指国家灭亡。"),
]

ROWS += [q(*spec) for spec in OPTIONAL_SPECS]


def load_expanded_rows() -> list[dict]:
    """Load deterministic candidate rows produced by expand_question_bank.py."""

    if not EXPANDED_SPECS.exists():
        return []
    data = json.loads(EXPANDED_SPECS.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("expanded_question_specs.json must contain a list")
    for row in data:
        if "distractorMeanings" not in row:
            raise ValueError("expanded question is missing distractorMeanings; regenerate with tools/expand_question_bank.py")
    return data


ROWS += load_expanded_rows()


def build() -> dict:
    if len(ROWS) < 260:
        raise ValueError(f"expected at least 260 base questions, got {len(ROWS)}")

    questions = []
    lexicon: OrderedDict[str, dict] = OrderedDict()
    for index, row in enumerate(ROWS, start=1):
        row = dict(row)
        article = ARTICLE_BY_ID[row["articleId"]]
        row.setdefault("type", "context_meaning")
        row.setdefault("volume", article["volume"])
        row.setdefault("unit", article["unit"])
        row.setdefault("article", article["title"])
        row.setdefault("reviewStatus", "candidate")
        row.setdefault("targetOccurrence", 1)
        if "targetStart" not in row:
            row["targetStart"] = row["sentence"].find(row["word"])
        if row["targetStart"] < 0:
            raise ValueError(f"{row['articleId']}/{row['word']}: target does not occur in sentence")
        row["id"] = f"bx-basic-{index:03d}"
        row["number"] = index
        meanings = [row["correctMeaning"], *row["distractorMeanings"]]
        row["options"] = [
            {"key": key, "text": meaning}
            for key, meaning in zip(("A", "B", "C", "D"), meanings, strict=True)
        ]
        row["answer"] = "A"
        del row["correctMeaning"]
        del row["distractorMeanings"]
        questions.append(row)

        entry = lexicon.setdefault(row["word"], {"word": row["word"], "senses": []})
        for meaning in meanings:
            if meaning not in entry["senses"]:
                entry["senses"].append(meaning)

    return {
        "schemaVersion": "3.0",
        "title": "高中语文文言实词基础训练",
        "description": "课内单句语境释义题。以单字实词为主，保留少量教材固定多字词；新增题目带有逐题来源并标记为待复核。",
        "questionTypes": [
            {
                "id": "context_meaning",
                "label": "语境释义题",
                "description": "根据原句判断实词在语境中的意思。",
            },
            {
                "id": "single_choice",
                "label": "普通单选题",
                "description": "使用题干和四个选项完成单项选择。",
            },
            {
                "id": "select_correct",
                "label": "选择正确项",
                "description": "从四个选项中选择正确的释义。",
            },
            {
                "id": "select_incorrect",
                "label": "选择错误项",
                "description": "从四个选项中选择错误的释义。",
            },
        ],
        "books": [
            {"id": "bx1", "label": "必修上册", "order": 1},
            {"id": "bx2", "label": "必修下册", "order": 2},
            {"id": "xxbs", "label": "选择性必修上册", "order": 3},
            {"id": "xxbz", "label": "选择性必修中册", "order": 4},
            {"id": "xxbx", "label": "选择性必修下册", "order": 5},
        ],
        "quizDefaults": {
            "durationSeconds": 120,
            "correctScore": 1,
            "wrongScore": -1,
            "scoring": {
                "mode": "fixed",
                "baseCorrect": 1,
                "baseWrongPenalty": 1,
                "correctStreakAfter": 2,
                "correctStreakScore": 2,
                "wrongStreakAfter": 2,
                "wrongStreakPenalty": 2,
            },
        },
        "catalog": CATALOG,
        "lexicon": list(lexicon.values()),
        "source": {
            "kind": "reviewed_compilation",
            "providedFiles": [
                "【8.11维叶语文】教材157个文言实词.docx",
                "高考文言实词与课内教材例句结合练习100题.docx",
                "【维叶语文10.15】高考文言文真题高频实词汇编（二）.doc",
                "【维叶语文8.15】高考文言文阅读高频实词汇编（一）(1).doc",
            ],
            "referenceSources": [
                {
                    "title": "人民教育出版社《高中语文学习任务导引 选择性必修上册 参考答案》",
                    "url": "https://stkw.pep.com.cn/xzzq/ckda/gyxxrwdy/202309/P020250822429065401952.pdf",
                },
                {
                    "title": "人民教育出版社《高中语文学习任务导引 选择性必修中册 参考答案》",
                    "url": "https://stkw.pep.com.cn/xzzq/ckda/gyxxrwdy/202309/P020250822429204884142.pdf",
                },
                {
                    "title": "人民教育出版社《高中语文学习任务导引 选择性必修下册 参考答案》",
                    "url": "https://stkw.pep.com.cn/xzzq/ckda/gyxxrwdy/202309/P020250822429338327062.pdf",
                },
                {
                    "title": "人民教育出版社统编高中教材篇目目录",
                    "url": "https://www.pep.com.cn/xw/zt/rjwy/gzkb2020/202205/P020220517522412911080.pdf",
                },
            ],
            "candidateSourceSnapshots": [
                {
                    "title": "yuwen：中学古文课文、注释与文言实词例句数据",
                    "url": "https://github.com/abdulle-sabaf/yuwen",
                    "usage": "仅用于生成 candidate 候选题，不替代当前教材复核。",
                },
                {
                    "title": "chinese-gushiwen：古文原文与注释数据",
                    "url": "https://github.com/aopao/chinese-gushiwen",
                    "usage": "仅用于生成 candidate 候选题，不替代当前教材复核。",
                },
            ],
            "reviewRule": "现有 verified 题保持原审核状态；新增 candidate 题须按当前统编教材正文、课下注释和人教社配套资料逐题复核后再改为 verified。",
            "questionCount": len(questions),
            "optionalQuestionCount": 160,
            "candidateQuestionCount": sum(q.get("reviewStatus") == "candidate" for q in questions),
            "expandedSpecsFile": "data/expanded_question_specs.json",
        },
        "questions": questions,
    }


def main() -> None:
    result = build()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_article: dict[str, int] = {}
    for question in result["questions"]:
        by_article[question["article"]] = by_article.get(question["article"], 0) + 1
    print(json.dumps({"output": str(OUTPUT), "questions": len(result["questions"]), "byArticle": by_article}, ensure_ascii=False))


if __name__ == "__main__":
    main()
