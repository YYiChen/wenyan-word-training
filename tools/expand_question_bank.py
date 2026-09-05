"""Expand the playable word-meaning bank from traceable candidate sources.

This script keeps the existing 260 reviewed rows and adds candidate rows from
the local classical-text datasets used during this expansion.  Candidate rows
carry their source record and ``reviewStatus=candidate`` so a teacher can
review them before treating them as textbook-verified.

The script is intentionally deterministic: the same source snapshot produces
the same rows, IDs, article counts, and output ordering.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "questions.json"
DEFAULT_CANDIDATES = ROOT / "data" / "expanded_question_specs.json"

TARGET_COUNTS = {
    "bx1_quxue": 20, "bx1_shishuo": 22, "bx1_chibifu": 24,
    "bx1_dengtaishanji": 20, "bx2_shizuo": 18, "bx2_qihuanjinwen": 25,
    "bx2_paoding": 22, "bx2_zhuwuzhiqin": 22, "bx2_hongmenyan": 35,
    "bx2_jian_taizong": 24, "bx2_dasima": 20, "bx2_afanggongfu": 25,
    "bx2_liuguolun": 24, "xx1_lunyu12": 18, "xx1_daxue": 16,
    "xx1_buren": 18, "xx1_laozi": 18, "xx1_wushi": 18,
    "xx1_jianai": 20, "xx2_quyuan": 35, "xx2_suwu": 38,
    "xx2_guoqin": 36, "xx2_wudai": 22, "xx3_chenqing": 31,
    "xx3_xiangjixuan": 28, "xx3_lanting": 22, "xx3_guiqulaixi": 28,
    "xx3_zhongshu": 30, "xx3_shizhong": 28,
}

ARTICLE_KEYS = {
    "bx1_quxue": "quanxue", "bx1_shishuo": "shishuo",
    "bx1_chibifu": "chibifu", "bx1_dengtaishanji": "dengtaishanji",
    "bx2_paoding": "paoding", "bx2_zhuwuzhiqin": "zhuzhiwu",
    "bx2_hongmenyan": "hongmenyan", "bx2_jian_taizong": "shisishu",
    "bx2_afanggongfu": "epanggongfu", "bx2_liuguolun": "liuguolun",
    "xx1_lunyu12": "lunyu2", "xx2_quyuan": "quyuanliezhuan",
    "xx2_suwu": "suwuzhuan", "xx2_guoqin": "guoqinlun",
    "xx2_wudai": "lingguanzhuanxu", "xx3_chenqing": "chenqingbiao",
    "xx3_xiangjixuan": "xiangjixuanzhi", "xx3_lanting": "lantingjixu",
    "xx3_guiqulaixi": "guiqulai", "xx3_zhongshu": "zhongshu",
    "xx3_shizhong": "shizhongshanji",
}

SOURCE_TITLES = {
    "bx1_quxue": "《劝学》（荀子）", "bx1_shishuo": "《师说》",
    "bx1_chibifu": "《前赤壁赋》", "bx1_dengtaishanji": "《登泰山记》",
    "bx2_paoding": "《庖丁解牛》", "bx2_zhuwuzhiqin": "《烛之武退秦师》",
    "bx2_hongmenyan": "《鸿门宴》", "bx2_jian_taizong": "《谏太宗十思疏》",
    "bx2_afanggongfu": "《阿房宫赋》", "bx2_liuguolun": "《六国论》",
    "xx1_lunyu12": "《论语》十二章", "xx2_quyuan": "《屈原列传》",
    "xx2_suwu": "《苏武传》", "xx2_guoqin": "《过秦论》",
    "xx2_wudai": "《五代史伶官传序》", "xx3_chenqing": "《陈情表》",
    "xx3_xiangjixuan": "《项脊轩志》", "xx3_lanting": "《兰亭集序》",
    "xx3_guiqulaixi": "《归去来兮辞并序》", "xx3_zhongshu": "《种树郭橐驼传》",
    "xx3_shizhong": "《石钟山记》",
}

# These are grammatical particles or obvious function words.  The requested
# bank is a content-word bank; they are left for a future dedicated虚词 bank.
FUNCTION_WORDS = set(
    "之其而以于者也矣焉乎哉兮夫且则乃若为与所无非未莫或盖既因故然虽使是可何安不我尔汝吾君子人子天下一二三四五六七八九十"
)

FALLBACK_DISTRACTORS = [
    "看见、发现", "往、到", "说、谈", "本来、原来", "全部、全都",
    "使……", "通“……”，同“……”", "……的样子", "同类、类别",
]


def clean_text(value: str) -> str:
    value = re.sub(r"<footnote:N\d+>", "", value or "")
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_meaning(value: str) -> str:
    value = re.sub(r"^\s*\[[^]]*\]\s*", "", value or "")
    value = value.replace("◇", "……")
    value = re.sub(r"〔[^〕]*〕", "", value)
    value = re.split(r"[。；]", value, maxsplit=1)[0].strip(" ‘“”\"'")
    return value[:60]


def read_jsonl_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records


def load_yuwen_articles(source_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for filename in ("中学古文阅读课文.json", "中学古文阅读课文-old2.json", "中学古文阅读课文-old3.json"):
        path = source_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, item in data.items():
            if isinstance(item, dict) and (key not in result or len(str(item)) > len(str(result[key]))):
                result[key] = item
    return result


def sentence_list(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？])", clean_text(raw)) if part.strip()]


def yuwen_candidates(article_id: str, article: dict[str, Any], word_senses: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    content = article.get("content", {})
    raw = content.get("raw", "") if isinstance(content, dict) else str(content)
    text = clean_text(raw)
    sentences = sentence_list(text)
    found: list[dict[str, Any]] = []
    for word, senses in word_senses.items():
        if len(word) != 1 or word in FUNCTION_WORDS:
            continue
        for meaning, example in senses.items():
            for fragment in re.split(r"[；。]", clean_text(example)):
                fragment = fragment.strip()
                if len(fragment) < 2 or fragment not in text or word not in fragment:
                    continue
                sentence = next((s for s in sentences if fragment in s), fragment)
                found.append({
                    "articleId": article_id, "word": word, "sentence": sentence,
                    "correctMeaning": clean_meaning(meaning),
                    "explanation": f"文言实词表将“{word}”释为“{clean_meaning(meaning)}”，本题取课文原句考查语境义。",
                    "source": {"kind": "candidate_word_sense", "title": "文言实词120例句表（候选）", "record": fragment},
                })
    return found


def remark_candidates(article_id: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("content", "")
    sentences = sentence_list(raw)
    found: list[dict[str, Any]] = []
    for line in str(record.get("remark", "")).splitlines():
        if "：" not in line:
            continue
        key, meaning = line.split("：", 1)
        key = re.sub(r"^\s*\d+\s*[.．、]?\s*", "", key)
        key = re.sub(r"（[^（）]*）|\([^()]*\)", "", key).strip("“”\" ")
        run = re.match(r"[\u4e00-\u9fff]+", key or "")
        if not run:
            continue
        key_run = run.group(0)
        word = key_run[0]
        if len(word) != 1 or word in FUNCTION_WORDS:
            continue
        sentence = next((s for s in sentences if key_run in s), None)
        if sentence is None:
            sentence = next((s for s in sentences if word in s), None)
        if not sentence:
            continue
        gloss = clean_meaning(meaning)
        if not gloss:
            continue
        found.append({
            "articleId": article_id, "word": word, "sentence": sentence,
            "correctMeaning": gloss,
            "explanation": f"来源注释将“{key_run}”解释为“{gloss}”；正式使用前需按当前教材版本复核。",
            "source": {"kind": "candidate_annotation", "title": "古文原文注释数据（候选）", "record": key_run},
        })
    return found


def manual_rows() -> list[dict[str, Any]]:
    # The short excerpts without a usable annotation record are supplied here
    # from the textbook wording and are still marked candidate for review.
    raw = [
        # 侍坐
        ("bx2_shizuo", "居", "居则曰：‘不吾知也。’", "平日、平时"),
        ("bx2_shizuo", "摄", "摄乎大国之间", "夹处"),
        ("bx2_shizuo", "加", "加之以师旅，因之以饥馑", "加到、施加"),
        ("bx2_shizuo", "因", "因之以饥馑", "接着、又"),
        ("bx2_shizuo", "哂", "夫子哂之", "微笑"),
        ("bx2_shizuo", "风", "浴乎沂，风乎舞雩", "吹风"),
        ("bx2_shizuo", "比", "比及三年，可使有勇", "等到"),
        ("bx2_shizuo", "率", "子路率尔而对曰", "轻率急忙的样子"),
        ("bx2_shizuo", "撰", "异乎三子者之撰", "才能、才干"),
        ("bx2_shizuo", "与", "吾与点也", "赞成"),
        ("bx2_shizuo", "作", "舍瑟而作，对曰", "起身"),
        ("bx2_shizuo", "俟", "以俟君子", "等待"),
        ("bx2_shizuo", "希", "鼓瑟希，铿尔，舍瑟而作", "同‘稀’，稀疏"),
        # 齐桓晋文之事
        ("bx2_qihuanjinwen", "闻", "齐桓、晋文之事可得闻乎", "听说、听到"),
        ("bx2_qihuanjinwen", "无", "无以，则王乎", "不得已"),
        ("bx2_qihuanjinwen", "王", "无以，则王乎", "行王道、称王"),
        ("bx2_qihuanjinwen", "异", "何以异", "不同"),
        ("bx2_qihuanjinwen", "舍", "王见之，曰：‘舍之！’", "释放"),
        ("bx2_qihuanjinwen", "觳", "吾不忍其觳觫", "恐惧战栗"),
        ("bx2_qihuanjinwen", "若", "若无罪而就死地", "像、如同"),
        ("bx2_qihuanjinwen", "就", "若无罪而就死地", "走向、走近"),
        ("bx2_qihuanjinwen", "然", "然则废衅钟与", "这样"),
        ("bx2_qihuanjinwen", "易", "以羊易之", "交换"),
        ("bx2_qihuanjinwen", "识", "不识有诸", "知道"),
        ("bx2_qihuanjinwen", "诸", "不识有诸", "之乎，兼词"),
        ("bx2_qihuanjinwen", "爱", "百姓之以王为爱也", "吝惜"),
        ("bx2_qihuanjinwen", "保", "保民而王，莫之能御也", "安定、保护"),
        ("bx2_qihuanjinwen", "举", "举斯心加诸彼而已", "拿、把"),
        ("bx2_qihuanjinwen", "老", "老吾老以及人之老", "敬爱老人"),
        ("bx2_qihuanjinwen", "足", "百姓足，君孰与不足", "充足"),
        ("bx2_qihuanjinwen", "御", "保民而王，莫之能御也", "抵御、阻挡"),
        ("bx2_qihuanjinwen", "褊", "齐国虽褊小，吾何爱一牛", "狭小"),
        ("bx2_qihuanjinwen", "远", "是以君子远庖厨也", "远离"),
        # 答司马谏议书
        ("bx2_dasima", "蒙", "昨日蒙教，窃以为与君实游处相好之日久", "承蒙"),
        ("bx2_dasima", "游", "与君实游处相好之日久", "交往"),
        ("bx2_dasima", "强", "虽欲强聒，终必不蒙见察", "强行"),
        ("bx2_dasima", "聒", "虽欲强聒，终必不蒙见察", "嘈杂地说个不停"),
        ("bx2_dasima", "见", "终必不蒙见察", "被"),
        ("bx2_dasima", "具", "故今具道所以，冀君实或见恕也", "详细"),
        ("bx2_dasima", "冀", "冀君实或见恕也", "希望"),
        ("bx2_dasima", "尤", "盖儒者所争，尤在于名实", "尤其"),
        ("bx2_dasima", "侵", "以为侵官、生事、征利、拒谏", "侵犯"),
        ("bx2_dasima", "征", "以为侵官、生事、征利、拒谏", "求取"),
        ("bx2_dasima", "拒", "以为侵官、生事、征利、拒谏", "拒绝"),
        ("bx2_dasima", "迁", "盘庚之迁，胥怨者民也", "迁移"),
        ("bx2_dasima", "胥", "盘庚之迁，胥怨者民也", "相、都"),
        ("bx2_dasima", "膏", "以膏泽斯民", "恩惠、滋润"),
        # 大学之道
        ("xx1_daxue", "至", "在止于至善", "达到、极点"),
        ("xx1_daxue", "定", "知止而后有定", "安定"),
        ("xx1_daxue", "静", "静而后能安", "心不妄动"),
        ("xx1_daxue", "安", "静而后能安", "安稳"),
        ("xx1_daxue", "虑", "安而后能虑", "思虑周详"),
        ("xx1_daxue", "得", "虑而后能得", "有所得、收获"),
        # 人皆有不忍人之心
        ("xx1_buren", "皆", "所以谓人皆有不忍人之心者", "都"),
        ("xx1_buren", "谓", "所以谓人皆有不忍人之心者", "说、认为"),
        ("xx1_buren", "孺", "今人乍见孺子将入于井", "小孩"),
        ("xx1_buren", "将", "今人乍见孺子将入于井", "将要"),
        ("xx1_buren", "由", "由是观之", "从、根据"),
        ("xx1_buren", "观", "由是观之", "观察、看"),
        ("xx1_buren", "扩", "知皆扩而充之矣", "扩大"),
        ("xx1_buren", "誉", "非所以要誉于乡党朋友也", "名誉"),
        # 老子四章
        ("xx1_laozi", "盈", "持而盈之，不如其已", "满"),
        ("xx1_laozi", "揣", "揣而锐之，不可长保", "捶击、锤炼"),
        ("xx1_laozi", "锐", "揣而锐之，不可长保", "使……锋利"),
        ("xx1_laozi", "已", "持而盈之，不如其已", "停止"),
        ("xx1_laozi", "保", "不可长保", "保全"),
        ("xx1_laozi", "遗", "富贵而骄，自遗其咎", "留下、招致"),
        ("xx1_laozi", "咎", "富贵而骄，自遗其咎", "过错"),
        ("xx1_laozi", "遂", "功成名遂身退", "成功"),
        # 五石之瓠
        ("xx1_wushi", "瓠", "魏王贻我大瓠之种", "葫芦"),
        ("xx1_wushi", "落", "瓠落无所容", "宽大空廓的样子"),
        ("xx1_wushi", "石", "我树之成而实五石", "容量单位"),
        ("xx1_wushi", "浮", "何不虑以为大樽而浮乎江湖", "漂浮"),
        ("xx1_wushi", "洴", "世世以洴澼絖为事", "漂洗"),
        ("xx1_wushi", "澼", "世世以洴澼絖为事", "漂洗"),
        ("xx1_wushi", "絖", "世世以洴澼絖为事", "丝絮"),
        ("xx1_wushi", "樽", "何不虑以为大樽而浮乎江湖", "酒器"),
        # 《论语》十二章补充
        ("xx1_lunyu12", "仁", "人而不仁，如礼何", "仁爱"),
        ("xx1_lunyu12", "夕", "朝闻道，夕死可矣", "傍晚"),
        ("xx1_lunyu12", "弘", "士不可以不弘毅，任重而道远", "广大"),
        ("xx1_lunyu12", "毅", "士不可以不弘毅，任重而道远", "坚毅"),
        ("xx1_lunyu12", "重", "士不可以不弘毅，任重而道远", "重大"),
        ("xx1_lunyu12", "死", "士不可以不弘毅，任重而道远，仁以为己任，不亦重乎？死而后已，不亦远乎？", "死去"),
        ("xx1_lunyu12", "已", "死而后已，不亦远乎", "停止"),
        # 兼爱
        ("xx1_jianai", "何", "治乱者何独不然", "什么、怎么"),
        ("xx1_jianai", "起", "不知乱之所自起", "发生、起因"),
        ("xx1_jianai", "独", "治乱者何独不然", "难道"),
        ("xx1_jianai", "兼", "兼相爱，交相利", "同时、全面地"),
        ("xx1_jianai", "交", "兼相爱，交相利", "互相"),
        ("xx1_jianai", "利", "故亏父而自利", "使……获利"),
        ("xx1_jianai", "禁", "恶得不禁恶而劝爱", "禁止"),
        ("xx1_jianai", "治", "焉能治之", "治理"),
        ("xx1_jianai", "乱", "治乱者何独不然", "混乱"),
        ("xx1_jianai", "交", "交相利", "互相"),
        # 苏武传
        ("xx2_suwu", "任", "少以父任，兄弟并为郎", "因父亲职位而任官"),
        ("xx2_suwu", "稍", "稍迁至栘中厩监", "渐渐"),
        ("xx2_suwu", "数", "时汉连伐胡，数通使相窥观", "多次"),
        ("xx2_suwu", "当", "匈奴使来，汉亦留之以相当", "相抵、抵押"),
        ("xx2_suwu", "行", "汉天子我丈人行也", "辈分"),
        ("xx2_suwu", "归", "尽归汉使路充国等", "使……归还"),
        ("xx2_suwu", "因", "因厚赂单于，答其善意", "于是、趁机"),
        ("xx2_suwu", "厚", "因厚赂单于", "优厚地"),
        ("xx2_suwu", "赂", "因厚赂单于", "赠送财物"),
        ("xx2_suwu", "置", "置酒设乐", "摆设"),
        ("xx2_suwu", "屈", "屈节辱命，虽生，何面目以归汉", "使……屈服"),
        ("xx2_suwu", "辱", "屈节辱命", "使……受辱"),
        ("xx2_suwu", "虽", "虽生，何面目以归汉", "即使"),
        ("xx2_suwu", "候", "虞常在汉时，素与副张胜相知，私候胜曰", "拜访"),
        ("xx2_suwu", "私", "私候胜曰", "私下"),
        ("xx2_suwu", "骄", "单于益骄，非汉所望也", "骄傲"),
        ("xx2_suwu", "降", "欲因此时降武", "使……投降"),
        ("xx2_suwu", "论", "会论虞常，欲因此时降武", "判罪"),
        ("xx2_suwu", "具", "具自陈道", "详细地"),
        ("xx2_suwu", "货", "张胜许之，以货物与常", "财物"),
        ("xx2_suwu", "春", "武以始元六年春至京师", "春季"),
        ("xx2_suwu", "益", "单于益骄，非汉所望也", "更加"),
        ("xx2_suwu", "发", "恐前语发，以状语武", "泄露、被揭发"),
        ("xx2_suwu", "相", "数通使相窥观", "互相"),
        ("xx2_suwu", "窥", "数通使相窥观", "窥探"),
        ("xx2_suwu", "观", "数通使相窥观", "观察"),
        ("xx2_suwu", "使", "乃遣武以中郎将使持节送匈奴使留在汉者", "出使"),
        ("xx2_suwu", "持", "持节送匈奴使留在汉者", "拿着"),
        # 过秦论补足
        ("xx2_guoqin", "争", "于是从散约败，争割地而赂秦", "争着"),
        ("xx2_guoqin", "蹑", "蹑足行伍之间，而倔起阡陌之中", "踏、进入"),
        ("xx2_guoqin", "倔", "而倔起阡陌之中", "同‘崛’，突然兴起"),
        ("xx2_guoqin", "陌", "倔起阡陌之中", "田间小路"),
        ("xx2_guoqin", "赢", "赢粮而景从", "担负"),
        ("xx2_guoqin", "景", "赢粮而景从", "同‘影’，像影子一样"),
        ("xx2_guoqin", "抗", "非抗于九国之师也", "匹敌、相当"),
        ("xx2_guoqin", "絜", "试使山东之国与陈涉度长絜大", "衡量"),
        ("xx2_guoqin", "隳", "一夫作难而七庙隳", "毁坏"),
        ("xx2_guoqin", "弱", "且夫天下非小弱也", "削弱"),
    ]
    rows = []
    for article_id, word, sentence, meaning in raw:
        rows.append({
            "articleId": article_id, "word": word, "sentence": sentence,
            "correctMeaning": meaning,
            "explanation": f"“{word}”在该句中解释为“{meaning}”，正式使用前按教材注释复核。",
            "source": {"kind": "candidate_manual", "title": "教材原句候选整理（待复核）", "record": sentence},
        })
    return rows


def build_candidates(source_root: Path, bank: dict[str, Any], gushiwen_root: Path | None = None) -> list[dict[str, Any]]:
    yuwen_dir = source_root / "src" / "中学"
    guwen_dir = (gushiwen_root or source_root) / "guwen"
    yuwen = load_yuwen_articles(yuwen_dir)
    senses = json.loads((yuwen_dir / "文言实词120.json").read_text(encoding="utf-8"))
    records = read_jsonl_records(guwen_dir)
    record_by_title: dict[str, dict[str, Any]] = {}
    aliases = {
        "劝学": "劝学", "师说": "师说", "前赤壁赋": "前赤壁赋", "登泰山记": "登泰山记",
        "庖丁解牛": "庖丁解牛", "烛之武退秦师": "烛之武退秦师", "谏太宗十思疏": "谏太宗十思疏",
        "阿房宫赋": "阿房宫赋", "六国论": "六国论", "论语十二章": "论语十二章",
        "屈原列传": "屈原列传", "五代史伶官传序": "五代史伶官传序", "陈情表": "陈情表",
        "项脊轩志": "项脊轩志", "兰亭集序": "兰亭集序 / 兰亭序", "归去来兮辞并序": "归去来兮辞·并序",
        "种树郭橐驼传": "种树郭橐驼传", "石钟山记": "石钟山记",
        "兰亭集序 / 兰亭序": "兰亭集序 / 兰亭序", "归去来兮辞·并序": "归去来兮辞·并序",
    }
    for record in records:
        title = str(record.get("title", ""))
        if title in aliases and record.get("remark"):
            current = record_by_title.get(title)
            if current is None or len(record.get("content", "")) > len(current.get("content", "")):
                record_by_title[title] = record

    candidates: list[dict[str, Any]] = []
    for article_id, key in ARTICLE_KEYS.items():
        if key in yuwen:
            candidates.extend(yuwen_candidates(article_id, yuwen[key], senses))
        article_title = SOURCE_TITLES.get(article_id, "").strip("《》")
        for title, record in record_by_title.items():
            normalized_article_title = article_title.replace("·", "")
            normalized_title = title.replace("·", "")
            if normalized_article_title and (normalized_article_title in normalized_title or normalized_title in normalized_article_title):
                candidates.extend(remark_candidates(article_id, record))
    candidates.extend(manual_rows())

    existing_keys = {(q["articleId"], q["word"], q["sentence"]) for q in bank["questions"]}
    seen = set(existing_keys)
    unique: list[dict[str, Any]] = []
    for row in candidates:
        if row["source"]["kind"] == "candidate_annotation":
            gloss = row["correctMeaning"]
            if any(marker in gloss for marker in ("人名", "地名", "山名", "河名", "年号", "朝代", "姓氏", "二水名", "载：")):
                continue
        if row["word"] not in row["sentence"]:
            raise ValueError(f"target word {row['word']} is absent from sentence: {row['sentence']}")
        key = (row["articleId"], row["word"], row["sentence"])
        if key in seen or not row.get("correctMeaning"):
            continue
        seen.add(key)
        unique.append(row)
    return unique


def build_options(row: dict[str, Any], by_word: dict[str, list[str]], word_senses: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    correct = row["correctMeaning"]
    pool: list[str] = []
    for item in by_word.get(row["word"], []):
        if item != correct and item not in pool:
            pool.append(item)
    for item in word_senses.get(row["word"], {}):
        item = clean_meaning(item)
        if item and item != correct and item not in pool:
            pool.append(item)
    for item in FALLBACK_DISTRACTORS:
        if item != correct and item not in pool:
            pool.append(item)
    return [{"key": key, "text": value} for key, value in zip("ABCD", [correct, *pool[:3]], strict=True)]


def expand(source_root: Path, input_path: Path, output_path: Path, candidate_path: Path, gushiwen_root: Path | None = None) -> dict[str, Any]:
    bank = json.loads(input_path.read_text(encoding="utf-8"))
    yuwen_dir = source_root / "src" / "中学"
    word_senses = json.loads((yuwen_dir / "文言实词120.json").read_text(encoding="utf-8"))
    candidates = build_candidates(source_root, bank, gushiwen_root)

    existing_counts = defaultdict(int)
    existing_keys = set()
    by_word: dict[str, list[str]] = defaultdict(list)
    for question in bank["questions"]:
        existing_counts[question["articleId"]] += 1
        existing_keys.add((question["articleId"], question["word"], question["sentence"]))
        if question.get("correctMeaning"):
            by_word[question["word"]].append(question["correctMeaning"])
        else:
            answer = next((o["text"] for o in question.get("options", []) if o["key"] == question.get("answer")), "")
            by_word[question["word"]].append(answer)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[row["articleId"]].append(row)
    selected: list[dict[str, Any]] = []
    for article_id, target in TARGET_COUNTS.items():
        needed = max(0, target - existing_counts[article_id])
        pool = grouped[article_id]
        # Prefer textbook word-table candidates, then direct remarks, then the
        # manually entered fallback rows.
        priority = {"candidate_word_sense": 0, "candidate_annotation": 1, "candidate_manual": 2}
        pool = sorted(pool, key=lambda r: (priority.get(r["source"]["kind"], 9), r["word"], r["sentence"]))
        selected.extend(pool[:needed])
    if len(bank["questions"]) + len(selected) < 680:
        raise ValueError(f"candidate sources yielded only {len(bank['questions']) + len(selected)} questions")
    for row in selected:
        row["reviewStatus"] = "candidate"
        if "distractorMeanings" not in row:
            row["distractorMeanings"] = [o["text"] for o in build_options(row, by_word, word_senses)] [1:]
    candidate_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    all_questions = list(bank["questions"])
    for row in selected:
        row = dict(row)
        row["reviewStatus"] = "candidate"
        row["volume"] = next(a["volume"] for a in bank["catalog"] if a["id"] == row["articleId"])
        article = next(a for a in bank["catalog"] if a["id"] == row["articleId"])
        row["unit"] = article.get("unit", "")
        row["article"] = article["title"]
        all_questions.append(row)

    for index, question in enumerate(all_questions, start=1):
        question["id"] = f"bx-basic-{index:03d}"
        question["number"] = index
        if "options" not in question:
            question["options"] = build_options(question, by_word, word_senses)
            question["answer"] = "A"
        else:
            # Existing questions are already normalized; keep their answer and
            # option order stable in the generated artifact.
            question["answer"] = question.get("answer", "A")

    bank["questions"] = all_questions
    lexicon: dict[str, dict[str, Any]] = {}
    for question in all_questions:
        entry = lexicon.setdefault(question["word"], {"word": question["word"], "senses": []})
        for option in question.get("options", []):
            if option["text"] not in entry["senses"]:
                entry["senses"].append(option["text"])
    bank["lexicon"] = list(lexicon.values())
    bank["description"] = "课内单句语境释义题。以单字实词为主，保留少量教材固定多字词；新增候选题带有逐题来源并标记为待复核。"
    bank.setdefault("source", {})["questionCount"] = len(all_questions)
    bank["source"]["candidateQuestionCount"] = sum(q.get("reviewStatus") == "candidate" for q in all_questions)
    bank["source"]["reviewRule"] = "现有 verified 题保持原审核状态；新增 candidate 题须按当前统编教材正文、课下注释和人教社配套资料逐题复核后再改为 verified。"
    output_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = defaultdict(int)
    for q in all_questions:
        counts[q["articleId"]] += 1
    return {"questions": len(all_questions), "candidateQuestions": bank["source"]["candidateQuestionCount"], "byArticle": dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--gushiwen-root", type=Path)
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--candidate-output", type=Path, default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    result = expand(args.source_root, args.input, args.output, args.candidate_output, args.gushiwen_root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
