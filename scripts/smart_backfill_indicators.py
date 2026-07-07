"""
对 backfill 后标记 _indicators_review: true 的条目做语义级指标推断。

策略：
  1. 先按 question + answer 文本中的关键词匹配指标编号（直接引用 F/H 的优先）
  2. 再按指标目录的 description 做语义映射（如"找工作"→F31）
  3. 对制度流程/PAD/抽样/入户沟通等无编号主题，打 topic 标签
  4. 实在无法推断的保留 _indicators_review: true

用法：
  python scripts/smart_backfill_indicators.py           # dry-run
  python scripts/smart_backfill_indicators.py --write    # apply
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = ROOT / "knowledge-base" / "qa" / "faq.json"
CATALOG_PATH = ROOT / "knowledge-base" / "indicator_catalog.json"

# ── 手动覆写（关键词匹配不了的复杂条目）───────────────────

MANUAL_OVERRIDES = {
    # 015: 劳动人口定义 → F10 就业判定核心，F4 只是提到年龄门槛
    "015": ["F10"],
    # 018: 志愿者 → F10(就业定义), F14(无酬家庭帮工)
    "018": ["F10", "F14"],
    # 029: 调查时点流程 → 无特定指标
    "029": [],  # topic
    # 032: PAD操作时间节点 → 无特定指标
    "032": [],  # topic
    # 072: 抽样对象 → 无特定指标，抽样方案章节
    "072": [],  # topic
    # 073: 失业率目标 → 无特定指标，调查目的
    "073": [],  # topic
    # 080: 儿子儿媳分家 → H2(本户人口), H3(户籍外出)
    "080": ["H2"],
    # 081: 外省务工节假日回家 → H2(常住判定)
    "081": ["H2"],
    # 087: 调查数据对外提供 → 统计法，非指标
    "087": [],  # topic
    # 088: 调查违法行为 → 统计法，非指标
    "088": [],  # topic
    # 161: 入户人多重复问 → 入户沟通技巧，非指标
    "161": [],  # topic
    # 171: 保姆代答 → 入户沟通/登记对象
    "171": [],  # topic
    # 181: 住户怀疑诈骗 → 入户沟通
    "181": [],  # topic
    # 188: PAD无网络信号 → PAD操作
    "188": [],  # topic
    # 190: 直报平台同步 → PAD操作
    "190": [],  # topic
    # 240: 修改之前回答 → PAD操作
    "240": [],  # topic
    # 242: F10-F12逻辑矛盾 → F10, F11, F12
    "242": ["F10", "F11", "F12"],
    # 249: 外国人居住 → 登记对象规则
    "249": [],  # topic
    # 252: 外国人+外籍保姆 → 登记对象规则
    "252": [],  # topic
    # 288: 数据异常核实 → 质量控制
    "288": [],  # topic
    # 304: 职工宿舍住3人 → H1(集体户)
    "304": ["H1"],
    # 305: 教师家庭常住人口 → H2
    "305": ["H2"],
    # 309: 职业编码要求 → F19
    "309": ["F19"],
    # 310: 打零工/短工 → F10, F14, F20
    "310": ["F10", "F14", "F20"],
    # 311: 水电工平时没活 → F10, F31
    "311": ["F10", "F31"],
    # 312: 公务员考试培训 → F31.1(找工作方式)
    "312": ["F31.1"],
    # 313: 照顾孩子想工作 → F34, F35, F36
    "313": ["F34", "F35", "F36"],
    # 314: 找工作多长时间判断 → F33
    "314": ["F33"],
    # 315: 农家乐经营类型 → F20
    "315": ["F20"],
    # 316: 城市户口+无承包地+职业农民 → F7, F20, F21
    "316": ["F7", "F20", "F21"],
    # 317: 盲人按摩师摔伤住院 → F11, F12
    "317": ["F11", "F12"],
    # 318: 出租车司机挂靠公司 → F20, F22
    "318": ["F20", "F22"],
    # 322: 上份工作会计→备考 → F37, F38
    "322": ["F37", "F38"],
    # 323: 县级统计机构职责 → 调查组织实施
    "323": [],  # topic
    # 324-329: 职业编码题 → F19
    "324": ["F19"],
    "325": ["F19"],
    "326": ["F19"],
    "327": ["F19"],
    "328": ["F19"],
    "329": ["F19"],
    # 330: 调查员违规行为 → 调查规范
    "330": [],  # topic
    # 331: 应届毕业生找工作 → F31, F31.3, F33
    "331": ["F31", "F31.3", "F33"],
    # 332: 兄弟打工提前回家 → F10, F11(春节放假), F12
    "332": ["F10", "F11", "F12"],
    # 333: 学历填写 → F9
    "333": ["F9"],
    # 334: 工作单位类型判断 → F20
    "334": ["F20"],
}

# 覆写 topic 标签（这些条目不属于任何 F/H 指标，但需要 topic）
MANUAL_TOPICS = {
    "029": ["调查时点/参考周"],
    "032": ["数据采集/PAD"],
    "072": ["抽样方案"],
    "073": ["调查组织实施"],
    "087": ["统计法/保密"],
    "088": ["统计法/保密"],
    "161": ["入户沟通/调查技巧"],
    "171": ["入户沟通/调查技巧", "登记对象规则"],
    "181": ["入户沟通/调查技巧"],
    "188": ["数据采集/PAD"],
    "190": ["数据采集/PAD"],
    "240": ["数据采集/PAD"],
    "249": ["登记对象规则"],
    "252": ["登记对象规则"],
    "288": ["调查组织实施"],
    "323": ["调查组织实施"],
    "330": ["调查组织实施"],
}

# 就业判定类
EMPLOYMENT_INDICATORS = {
    "F10": ["为取得报酬", "工作过1小时", "打零工", "兼职", "报酬", "1小时以上",
            "有偿劳动", "干活", "上班", "有工作", "劳动人口", "就业人口",
            "就业状态", "就业判定", "算不算就业"],
    "F11": ["有工作但没上班", "在职未上班", "没上班/干活", "有工作没上班",
            "没去上班", "歇业", "停薪留职", "放假", "暂时没活"],
    "F12": ["没上班的主要原因", "为什么没上班", "没上班原因", "病假", "事假",
            "产假", "陪产假", "节假日", "公休假", "学习培训", "经济不景气"],
    "F13": ["未上班期间有收入", "没上班有没有工资", "未上班工资", "病假工资"],
    "F13.1": ["返回原工作", "回原单位", "1个月内返回"],
    "F14": ["帮助家人", "无报酬", "家庭帮工", "无酬", "帮工", "志愿者",
            "以营利为目的", "帮家里人干活", "帮忙看店", "家庭经营"],
}

# 工作特征类
JOB_FEATURES = {
    "F15": ["几份工作", "兼职", "在职未上班", "无酬家庭帮工", "多份工作"],
    "F15.1": ["总共工作", "总工时", "多少小时", "工作了几个小时"],
    "F16": ["主要工作", "主要工作时间", "主要工作多少小时"],
    "F17": ["干了多长时间", "工作多久", "干了多久", "工龄", "工作年限"],
    "F18": ["行业", "生产或经营", "主要产品或服务", "做什么业务"],
    "F19": ["职业", "具体做什么", "职务", "工种", "职业编码", "做什么工作"],
    "F20": ["工作单位", "单位类型", "生产经营活动类型", "单位性质", "机关团体",
            "事业单位", "国有企业", "集体企业", "私营", "个体经营", "自由职业",
            "农民专业合作社", "农村家庭承包", "经营农村家庭承包地"],
    "F21": ["就业身份", "雇员", "雇主", "自营者", "无酬家庭帮工"],
    "F22": ["劳动合同", "签订合同", "签合同", "合同类型", "固定期限", "无固定期限"],
    "F22.1": ["什么类型", "合同类型"],
    "F23": ["单位缴纳", "城镇职工基本养老保险", "单位交社保", "单位缴社保"],
    "F24": ["自己缴纳", "自己交社保", "个人缴纳", "个人交养老保险"],
}

# 新就业形态类
NEW_ECONOMY = {
    "F25": ["中间商", "线上订单", "线下订单", "滴滴", "外卖", "来料加工",
            "计件生产", "平台接单", "中间商订单"],
    "F26": ["创建者", "创业者", "公司创建", "个体经营创建", "合伙创建", "创办"],
    "F26.1": ["哪年创建", "创建时间"],
    "F26.2": ["从业人员", "从业人数", "多少人", "雇佣人数", "员工数"],
    "F27": ["工作报酬", "经营净收入", "上月收入", "月收入", "月薪", "工资",
            "报酬", "经营收入", "欠薪", "工资拖欠", "不足一个月", "按合同协议"],
    "F28": ["互联网", "通过网络", "网上", "在线", "线上接单", "淘宝", "京东",
            "微信卖货", "抖音", "平台", "直播带货", "快递", "网约车", "在线教育"],
    "F28.1": ["主要从事哪一类", "承接生产订单", "商品交易", "金融服务",
              "用车服务", "物流服务", "生活服务", "网络直播", "中介服务"],
}

# 工时/增加工作
WORK_HOURS = {
    "F29": ["少于40小时", "周工作时间", "不到40小时", "不足40小时",
            "工时不足", "正常工作时间", "生意不好", "订单不足"],
    "F30": ["想增加工作时间", "想多干", "增加工时", "加班", "更长时间",
            "想换工作", "想有兼职", "想多赚钱"],
    "F30.1": ["2周内", "两周内", "能否开始", "能开始工作"],
}

# 无工作/找工作
NO_WORK = {
    "F31": ["找过工作", "找工作", "近3个月", "三个月", "求职", "寻职"],
    "F31.1": ["找工作的方式", "找工作方式", "怎么找", "委托亲戚", "招聘网站",
              "联系雇主", "就业服务", "招聘会", "为自己经营准备"],
    "F31.2": ["近1个月", "一个月内", "最近一个月"],
    "F31.3": ["未来3个月", "确定会开始", "已落实", "已确定工作"],
    "F32": ["在校期间", "非寒暑假", "在校从事", "上学期间工作"],
    "F32.1": ["寒暑假", "假期工作"],
    "F32.2": ["正处寒暑假", "寒暑假期间"],
    "F32.3": ["毕业去向", "已落实工作单位", "自主创业", "已落实升学", "未落实"],
    "F33": ["已找多长时间", "找多久", "找工作多长时间", "等待工作"],
    "F34": ["不找工作的原因", "不找工作", "为什么没找", "不想找",
            "参加学习培训", "健康原因", "身体原因", "认为找不到", "找不到合适",
            "有生活保障", "养老金", "租金收入", "照顾家庭", "在家带孩子"],
    "F35": ["想工作吗", "想不想工作", "是否想工作", "愿意工作"],
    "F36": ["能在2周内开始", "两周内开始工作", "合适的工作", "能否开始工作"],
    "F36.1": ["为什么不能", "不能开始", "不能的原因"],
    "F37": ["上一份工作结束", "上一份工作", "工作结束多久", "多长时间没工作",
            "上一份工作什么时候", "从没工作过"],
    "F38": ["结束上一份工作原因", "离职原因", "辞退", "解雇", "下岗",
            "被解雇", "辞职", "停产倒闭", "承包地被征用"],
    "F39": ["上一个工作单位", "上一份工作行业", "上一份工作经营"],
    "F40": ["上一个工作职业", "上一份做什么"],
    "F41": ["参加城镇职工", "养老保险", "领养老金", "正在缴纳养老"],
}

# 个人信息
PERSONAL_INFO = {
    "F1": ["姓名", "叫什么"],
    "F2": ["户主关系", "与户主", "什么关系", "配偶", "子女", "父母"],
    "F3": ["性别", "男女"],
    "F4": ["出生年月", "年龄", "周岁", "几岁", "出生日期"],
    "F5": ["户口登记地", "户口所在地", "户籍地", "户口在哪"],
    "F5.1": ["市辖区", "本市市辖区"],
    "F6": ["住本户", "住了多久", "住多长时间", "居住时间"],
    "F6.1": ["离开户口", "离开户籍", "离开多久"],
    "F7": ["农村土地承包", "土地承包经营权", "承包地", "有地", "农业户口"],
    "F8": ["婚姻", "未婚", "有配偶", "离婚", "丧偶", "同居", "结婚"],
    "F9": ["受教育程度", "学历", "上学", "教育", "文化程度", "小学", "初中",
            "高中", "大学", "研究生", "职业教育", "在校学生", "全日制"],
    "F9.1": ["毕业时间", "预计毕业", "哪年毕业"],
    "F9.2": ["全日制在校学生", "是否在校", "在读", "在校生"],
    "F9.3": ["全日制学习", "已毕业离校", "毕业离校"],
}

# 住户信息
HOUSEHOLD_INFO = {
    "H1": ["户别", "家庭户", "集体户", "集体宿舍", "职工宿舍", "单身居住",
            "一间房", "共同生活", "几户"],
    "H2": ["住了几个人", "人口数", "本户人口", "居住人口", "几口人",
            "家里几口", "常住人口", "登记几人"],
    "H3": ["外出不满半年", "户籍人口", "外出人口", "不在家住", "外出打工"],
}

# ── 主题标签（无对应指标的条目）───────────────────────────

TOPIC_TAGS = {
    "调查时点/参考周": [
        "参考周", "3-9日", "10日零时", "调查时点", "标准时间",
        "入户登记时间", "调查频率", "每月几次", "月度还是季度",
        "每月几号", "调查的标准时间", "哪几天", "入户登记",
        "数据采集、报送", "报送时间", "几个工作日", "每月3日",
        "每月9日", "每月10日", "每月15日", "每月23日",
    ],
    "数据采集/PAD": [
        "PAD", "数据采集", "数据报送", "直报平台", "手持电子",
        "时间节点", "怎么同步", "没有网络信号", "删除人",
    ],
    "抽样方案": [
        "抽样", "样本", "轮换", "摸底", "样本框", "样本量",
        "递补", "换户", "空户", "村级样本", "抽样方法",
    ],
    "入户沟通/调查技巧": [
        "入户", "调查员", "敲门", "沟通", "应对", "拒绝",
        "不配合", "发脾气", "有急事", "没时间", "上门",
        "宣传品", "致调查户", "公告", "一封信",
    ],
    "统计法/保密": [
        "统计法", "保密", "信息泄露", "隐私", "处罚", "法律责任",
        "不得对外提供", "不得泄露", "公民义务",
    ],
    "调查组织实施": [
        "质量控制", "督导", "核查", "回访", "电话核查", "音频核查",
        "职责", "培训", "选聘", "考核", "违法行为",
    ],
    "表尾信息": [
        "申报人", "电话", "签字", "填报日期", "调查员签字",
    ],
    "登记对象规则": [
        "应在本户登记", "登记对象", "外国人", "港澳台", "外籍",
        "保姆", "家政", "服刑", "现役军人",
    ],
    "行业职业编码": [
        "编码", "代码", "编码规则", "行业分类", "职业分类",
    ],
}

ALL_INDICATOR_MAPS = [
    EMPLOYMENT_INDICATORS, JOB_FEATURES, NEW_ECONOMY, WORK_HOURS,
    NO_WORK, PERSONAL_INFO, HOUSEHOLD_INFO
]


def load_catalog():
    with open(CATALOG_PATH, encoding='utf-8') as f:
        return json.load(f)


def match_indicators_by_keywords(text: str) -> dict[str, int]:
    """Return {code: score} based on keyword matches."""
    scores = {}
    for indicator_map in ALL_INDICATOR_MAPS:
        for code, keywords in indicator_map.items():
            for kw in keywords:
                if kw in text:
                    scores[code] = scores.get(code, 0) + 1
    return scores


def match_topic_by_keywords(text: str) -> list[str]:
    """Return list of topic tags that match."""
    tags = []
    for tag, keywords in TOPIC_TAGS.items():
        for kw in keywords:
            if kw in text:
                tags.append(tag)
                break
    return tags


def smart_backfill(dry_run: bool = True):
    catalog = load_catalog()
    all_catalog_codes = set()
    for mod in catalog['modules'].values():
        all_catalog_codes.update(mod.keys())

    with open(FAQ_PATH, encoding='utf-8') as f:
        faq = json.load(f)

    stats = {"filled": 0, "topic_only": 0, "still_review": 0, "details": []}
    topic_tags_used = {}

    for entry in faq:
        if not entry.get('_indicators_review'):
            continue

        eid = entry.get("id", "")
        combined = entry.get("question", "") + " " + entry.get("answer", "")

        # Step 0: manual override takes absolute priority
        if eid in MANUAL_OVERRIDES:
            codes = MANUAL_OVERRIDES[eid]
            if codes:
                entry["indicators"] = sorted(set(codes))
                stats["filled"] += 1
                stats["details"].append(f"  [{eid}] → {sorted(set(codes))} (manual override)")
            else:
                entry["_indicators_topic"] = MANUAL_TOPICS.get(eid, ["未分类"])
                stats["topic_only"] += 1
                stats["details"].append(f"  [{eid}] → topic: {MANUAL_TOPICS.get(eid)} (manual override)")
            if "_indicators_review" in entry:
                del entry["_indicators_review"]
            continue

        # Step 1: direct F/H matches in text
        direct_codes = set(re.findall(r'(?<![A-Za-z])[FH]\d+(?:\.\d+)?', combined))
        valid_direct = direct_codes & all_catalog_codes

        # Step 2: keyword scoring (only high-confidence, score >= 2)
        kw_scores = match_indicators_by_keywords(combined)
        high_score_codes = {c for c, s in kw_scores.items() if s >= 2}

        # Step 3: topic matching
        topics = match_topic_by_keywords(combined)

        # Combine: direct matches + high confidence keyword matches ONLY
        all_inferred = valid_direct | high_score_codes

        # Decide
        if all_inferred:
            entry["indicators"] = sorted(all_inferred)
            if "_indicators_review" in entry:
                del entry["_indicators_review"]
            stats["filled"] += 1
            stats["details"].append(f"  [{entry['id']}] → {sorted(all_inferred)} (direct={valid_direct}, kw_high={high_score_codes})")
        elif topics:
            entry["_indicators_topic"] = topics
            if "_indicators_review" in entry:
                del entry["_indicators_review"]
            stats["topic_only"] += 1
            for t in topics:
                topic_tags_used[t] = topic_tags_used.get(t, 0) + 1
            stats["details"].append(f"  [{entry['id']}] → topic: {topics}")
        else:
            stats["still_review"] += 1
            stats["details"].append(f"  [{entry['id']}] STILL NEEDS REVIEW: {entry['question'][:50]}...")

    print(f"=== Smart Backfill Report ===")
    print(f"Filled with indicators: {stats['filled']}")
    print(f"Tagged as topic (no indicator): {stats['topic_only']}")
    print(f"Still needs manual review: {stats['still_review']}")
    print()

    if topic_tags_used:
        print("Topic tags used:")
        for t, c in sorted(topic_tags_used.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")

    print("\nDetails:")
    for d in stats["details"]:
        print(d)

    if dry_run:
        print("\n[Dry run — use --write to apply]")
    else:
        with open(FAQ_PATH, 'w', encoding='utf-8') as f:
            json.dump(faq, f, ensure_ascii=False, indent=2)
        print(f"\nWritten {FAQ_PATH}")

    return stats


if __name__ == "__main__":
    dry_run = "--write" not in sys.argv
    smart_backfill(dry_run=dry_run)
