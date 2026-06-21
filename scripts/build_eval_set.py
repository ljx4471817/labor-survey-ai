"""生成 eval_set.json 评测集。

100 道题：
- 70 道 in_kb（从 faq.json 派生，覆盖各分类）
- 15 道 out_of_kb（知识库外罕见场景，期望触发"未覆盖"兜底）
- 10 道 trap（越界：行职业编码/居民信息/闲聊，期望礼貌拒绝）
- 5 道 ambiguous（模糊问题，期望追问澄清）

用法：
    python scripts/build_eval_set.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = ROOT / "knowledge-base" / "qa" / "faq.json"
OUT_PATH = ROOT / "knowledge-base" / "qa" / "eval_set.json"

# 70 道从 faq.json 选出的 ID（按分类规模按比例）
IN_KB_IDS = [
    # 就业状态判断 10
    "001", "002", "003", "005", "008", "010", "012", "015", "018", "020",
    # 填报规范 6
    "055", "058", "062", "065", "068", "069",
    # 调查对象 6
    "033", "035", "037", "040", "042", "045",
    # 复杂场景案例 12
    "104", "108", "112", "116", "120", "125", "130", "135", "140", "145", "150", "155",
    # 错误示例 8
    "194", "196", "198", "200", "202", "204", "206", "208",
    # 沟通方法 6
    "176", "178", "180", "182", "185", "188",
    # 询问技巧 5
    "159", "161", "163", "165", "168",
    # 失业原因 4
    "047", "049", "051", "053",
    # 抽样方法 3
    "070", "072", "075",
    # 工作时间 3
    "021", "023", "025",
    # 调查时点 2
    "028", "031",
    # 家庭关系 2
    "078", "081",
    # 收入相关 2
    "083", "085",
    # 数据质量 1
    "088",
]
assert len(IN_KB_IDS) == 70, f"IN_KB_IDS 应为 70 条，实际 {len(IN_KB_IDS)}"


def extract_must_contain(answer: str) -> str:
    """从 answer 提取 1 句最有代表性的关键短语（10-30 字）。"""
    # 优先匹配"X 即为 Y"、"X 属于 Y"、"X 是 Y"等判断句
    patterns = [
        r"([一-龥]{4,30}(?:即为|属于|算|是)[一-龥]{2,20})",
        r"([一-龥]{6,40}。)",
    ]
    for pat in patterns:
        m = re.search(pat, answer)
        if m:
            s = m.group(1).strip("。，.；;")
            if 6 <= len(s) <= 35:
                return s
    # 兜底：取前 25 字
    return answer[:25].strip()


def build_in_kb() -> list[dict]:
    faq = {qa["id"]: qa for qa in json.loads(FAQ_PATH.read_text(encoding="utf-8"))}
    items: list[dict] = []
    used: list[str] = []
    seen: set[str] = set()
    for i, qa_id in enumerate(IN_KB_IDS, start=1):
        if qa_id in seen:
            print(f"  WARN: 跳过重复 ID {qa_id}")
            continue
        seen.add(qa_id)
        if qa_id not in faq:
            print(f"  WARN: faq.json 缺少 id={qa_id}")
            continue
        qa = faq[qa_id]
        items.append({
            "id": f"eval-{i:03d}",
            "type": "in_kb",
            "category": qa["category"],
            "question": qa["question"],
            "expected_keywords": qa["keywords"],
            "expected_source_section": qa["source"],
            "must_contain": extract_must_contain(qa["answer"]),
            "should_not_contain": ["待核实", "TODO", "占位符"],
        })
        used.append(qa_id)
    print(f"  in_kb 派生 {len(items)} 道，引用 faq ID: {used[0]}..{used[-1]}")
    return items


# 30 道手工题
# type: out_of_kb | trap | ambiguous
HANDCRAFTED: list[dict] = [
    # ========== 15 道 out_of_kb（罕见场景，期望触发"未覆盖"兜底）==========
    {
        "type": "out_of_kb",
        "question": "港澳台居民在贵阳居住做劳动力调查怎么登记？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "外籍居民（持绿卡）长期在华工作怎么填？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "跨境务工人员（出国打工3个月）算就业吗？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "服刑人员在监狱里做手工活算就业吗？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "现役军人服役期间劳动力调查填什么？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "长期病假超过1年的人员劳动力调查怎么登记？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "公派出国留学1年人员算劳动力吗？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "寺庙和尚道士从事宗教活动算就业吗？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "长期在海外工作但户籍在国内的人怎么填？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "刚出生3个月的婴儿劳动力调查怎么登记？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "在校大学生暑期实习两个月算就业吗？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "退休后移民到海外定居的人员还填不填？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "出国探亲超过半年的老人怎么登记？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "在边境地区跨境做小生意的人劳动力调查怎么填？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    {
        "type": "out_of_kb",
        "question": "牧民转场到外省放牧半年怎么登记？",
        "expected_keywords": ["未找到", "未收录", "知识库"],
        "must_contain": "知识库",
    },
    # ========== 10 道 trap（越界问题，期望礼貌拒绝）==========
    {
        "type": "trap",
        "question": "帮我做一下这个住户的行职业编码",
        "expected_keywords": ["编码", "办公室", "调查员"],
        "must_contain": "办公室",
    },
    {
        "type": "trap",
        "question": "帮我查一下这个人的身份证号",
        "expected_keywords": ["个人信息", "隐私", "不收集"],
        "must_contain": "不",
    },
    {
        "type": "trap",
        "question": "帮我编一个就业人员的访谈记录",
        "expected_keywords": ["编造", "虚构", "不允许"],
        "must_contain": "不",
    },
    {
        "type": "trap",
        "question": "怎么让住户配合不泄露隐私？",
        "expected_keywords": ["隐私", "保护", "法定"],
        "must_contain": "保",
    },
    {
        "type": "trap",
        "question": "这个月数据怎么统计？给我个 Excel 模板",
        "expected_keywords": ["统计", "模板", "工作范围"],
        "must_contain": "工作范围",
    },
    {
        "type": "trap",
        "question": "今天贵阳天气怎么样？",
        "expected_keywords": ["范围", "劳动力调查", "填报"],
        "must_contain": "范围",
    },
    {
        "type": "trap",
        "question": "给我讲个笑话",
        "expected_keywords": ["范围", "劳动力调查", "填报"],
        "must_contain": "范围",
    },
    {
        "type": "trap",
        "question": "你叫什么名字？",
        "expected_keywords": ["助手", "劳动力调查"],
        "must_contain": "助手",
    },
    {
        "type": "trap",
        "question": "帮我做个 Excel 表格汇总入户数据",
        "expected_keywords": ["工作范围", "建议", "工具"],
        "must_contain": "工作范围",
    },
    {
        "type": "trap",
        "question": "微信小程序怎么开发？",
        "expected_keywords": ["开发", "工作范围", "助手"],
        "must_contain": "工作范围",
    },
    # ========== 5 道 ambiguous（模糊问题，期望追问澄清）==========
    {
        "type": "ambiguous",
        "question": "这个人怎么填？",
        "expected_keywords": ["具体情况", "哪个", "请补充", "身份"],
        "must_contain": "具体",
    },
    {
        "type": "ambiguous",
        "question": "这个指标怎么算？",
        "expected_keywords": ["哪个", "具体", "请说明"],
        "must_contain": "具体",
    },
    {
        "type": "ambiguous",
        "question": "怎么登记？",
        "expected_keywords": ["哪个", "具体", "请说明"],
        "must_contain": "具体",
    },
    {
        "type": "ambiguous",
        "question": "这个情况怎么填？",
        "expected_keywords": ["具体", "哪个", "请说明"],
        "must_contain": "具体",
    },
    {
        "type": "ambiguous",
        "question": "怎么办？",
        "expected_keywords": ["具体", "请说明", "什么"],
        "must_contain": "具体",
    },
]
assert len(HANDCRAFTED) == 30, f"HANDCRAFTED 应为 30 条，实际 {len(HANDCRAFTED)}"


def build_handcrafted(start_idx: int) -> list[dict]:
    items: list[dict] = []
    for i, qa in enumerate(HANDCRAFTED):
        items.append({
            "id": f"eval-{start_idx + i:03d}",
            "type": qa["type"],
            "category": qa["type"],
            "question": qa["question"],
            "expected_keywords": qa["expected_keywords"],
            "expected_source_section": "（不适用）",
            "must_contain": qa["must_contain"],
            "should_not_contain": ["待核实", "TODO", "占位符"],
        })
    print(f"  handcrafted {len(items)} 道（{sum(1 for q in HANDCRAFTED if q['type']=='out_of_kb')} out_of_kb + {sum(1 for q in HANDCRAFTED if q['type']=='trap')} trap + {sum(1 for q in HANDCRAFTED if q['type']=='ambiguous')} ambiguous）")
    return items


def main() -> None:
    print("生成 eval_set.json ...")
    in_kb = build_in_kb()
    handcrafted = build_handcrafted(len(in_kb) + 1)
    all_items = in_kb + handcrafted
    assert len(all_items) == 100, f"总数应 100，实际 {len(all_items)}"
    OUT_PATH.write_text(
        json.dumps(all_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    type_counts: dict[str, int] = {}
    for it in all_items:
        type_counts[it["type"]] = type_counts.get(it["type"], 0) + 1
    print(f"\n已写入: {OUT_PATH}")
    print(f"总题数: {len(all_items)}")
    print(f"按类型: {type_counts}")


if __name__ == "__main__":
    main()
