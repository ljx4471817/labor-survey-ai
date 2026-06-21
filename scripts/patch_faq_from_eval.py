"""docx 自测发现的 4 处知识库缺口补丁。

1. 修 qa 255：F37 答案错（说"按年月填"，实际是选时长选项）
2. 加 294：F26.2 从业人数完整定义（家庭餐馆案例 6 人）
3. 加 295：F7 户口迁出 → 没有
4. 加 296：F34 有生活保障 vs 退休
5. 加 297：H2 居住人口（合租每户各 1 人）

跑完后用 build_kb.py + build_bm25.py 重建索引。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

FAQ = Path("knowledge-base/qa/faq.json")


def main() -> int:
    faq = json.loads(FAQ.read_text(encoding="utf-8"))
    print(f"原始条数: {len(faq)}")

    for qa in faq:
        if qa["id"] == "255":
            qa["question"] = "失业人口F37上一份工作结束时间怎么算？填具体年月还是选时长？"
            qa["answer"] = (
                "F37询问的是上一份工作距今「多长时间」，不是填写具体年月。"
                "选项：①不满1个月、②1个月以上不满3个月、③3个月以上不满半年、"
                "④半年以上不满1年、⑤1年以上不满3年、⑥3年以上。"
                "例如：3个月前工厂倒闭→选③；2个月前辞职→选②；2年前辞职→选⑤。"
                "判断起点：从工作实际结束之日算起（被解聘从最后一天上班日、合同到期从到期日、自己辞职从最后工作日算）。"
            )
            qa["keywords"] = ["F37", "上一份工作", "结束时间", "时长选项", "距今"]
            print(f"  [修] {qa['id']}: F37 答案改对")

    faq.append({
        "id": "294",
        "category": "填报规范",
        "question": "F26.2从业人数包括哪些人？家庭餐馆夫妻+2厨师+1服务员+1个帮工儿子，应该填多少人？",
        "answer": (
            "F26.2从业人数 = 所有实际参与经营的人员，包括：①业主（雇主/自营者）+ "
            "②雇员（领工资的）+ ③无酬家庭帮工（家庭成员帮忙无报酬的）。"
            "不区分是否领工资，也不看户籍关系，全部计入。"
            "所以上述案例：周大哥（业主）+ 周嫂（业主）+ 2厨师（雇员）+ 1服务员（雇员）+ 儿子小周（无酬家庭帮工）= 6人。"
            "常见错误：只算自己和配偶，漏算雇员和无酬家庭帮工，导致数据偏低。"
        ),
        "source": "劳动力调查及指标讲解·F26.2指标讲解；劳动力调查制度（2026版）第三部分·问题26.2",
        "keywords": ["F26.2", "从业人数", "雇员", "无酬家庭帮工", "业主"],
    })
    print("  [+] 294: F26.2 从业人数")

    faq.append({
        "id": "295",
        "category": "填报规范",
        "question": "户口已经从农村迁到城市，但老家农村家里还有承包地，F7农村土地承包经营权怎么填？",
        "answer": (
            "F7以「自己的户口本」为标志判断，不是看本人是否实际经营，也不是看老家的地还在不在。"
            "两种情况：①户口仍在农村（含未迁出的家庭成员），家庭有承包地→圈填「有」；"
            "②户口已迁出城市、本人是城镇户口，老家承包地登记在父母名下、自己不独立成户→圈填「没有」。"
            "关键点：看自己的户口本。"
            "例如：张大哥原户口在四川农村、后迁到浙江城镇，老家承包地由父母继续经营，张大哥本人F7应圈填「没有」。"
        ),
        "source": "劳动力调查及指标讲解·F7指标讲解；劳动力调查制度（2026版）第三部分·问题7",
        "keywords": ["F7", "土地承包", "户口本", "户口迁出", "城镇户口"],
    })
    print("  [+] 295: F7 户口迁出场景")

    faq.append({
        "id": "296",
        "category": "失业原因",
        "question": "F34不找工作的主要原因——退休和有生活保障怎么区分？",
        "answer": (
            "F34选项①退休 vs ④有生活保障：①退休特指办理了正式退休手续、按月领取养老金的人员；"
            "④有生活保障更广义，包括有稳定生活来源（养老金、退休金、积蓄、家庭供养、领取失地农民保险、有房租/股息收入等）"
            "不需要找工作的人。"
            "例如：老两口每月有退休金不打算工作→选④有生活保障；若明确是退休职工可以选①退休。"
            "判断要点：被访者主观上经济上有保障不需要工作，就选④。"
            "注意：怀孕/哺乳期在家照看孩子的，应选③照顾家庭而不是④。"
        ),
        "source": "劳动力调查及指标讲解·F34指标讲解；劳动力调查制度（2026版）第三部分·问题34",
        "keywords": ["F34", "不找工作", "退休", "有生活保障", "养老金"],
    })
    print("  [+] 296: F34 退休 vs 有生活保障")

    faq.append({
        "id": "297",
        "category": "调查对象",
        "question": "多人合租一套住房每户各住一间，H2居住人口怎么填？",
        "answer": (
            "合租的每户作为独立户登记时，H2居住人口填该户实际居住的人数（通常就是本人 1 人）。"
            "例如：三人合租三居室各住一间，每户的H2居住人口填「1人」。"
            "常见错误：①填总人数（3人）；②每户都填一样的（都填3人）；③空着不填。"
            "正确做法：按户分别登记，每户填本户实际居住的人数。"
        ),
        "source": "劳动力调查及指标讲解·H2指标讲解；劳动力调查制度（2026版）第三部分·问题2",
        "keywords": ["H2", "居住人口", "合租", "独立户", "每户"],
    })
    print("  [+] 297: H2 居住人口（合租）")

    FAQ.write_text(
        json.dumps(faq, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n写入 {len(faq)} 条 → {FAQ}")

    print("\n跑 validate_faq.py ...")
    r = subprocess.run(
        ["python", "scripts/validate_faq.py"],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(r.stdout)
    if r.returncode != 0:
        print("VALIDATE 失败！", r.stderr)
        return 1

    print("重建向量索引 build_kb.py ...")
    r = subprocess.run(
        ["python", "scripts/build_kb.py"],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(r.stdout[-500:])
    if r.returncode != 0:
        print("BUILD_KB 失败！", r.stderr)
        return 1

    print("重建 BM25 索引 build_bm25.py --full ...")
    r = subprocess.run(
        ["python", "scripts/build_bm25.py", "--full"],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(r.stdout[-300:])
    if r.returncode != 0:
        print("BUILD_BM25 失败！", r.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())