"""微调 qa 294/296 的措辞，让它们能被用户实际问法召回。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

FAQ = Path("knowledge-base/qa/faq.json")


def main() -> int:
    faq = json.loads(FAQ.read_text(encoding="utf-8"))

    for qa in faq:
        if qa["id"] == "294":
            qa["question"] = (
                "餐馆/小店/夫妻店的从业人数怎么算？是不是只算自己和配偶？"
            )
            qa["answer"] = (
                "不是只算自己和配偶。F26.2从业人数 = 业主（雇主/自营者）+ 雇员（领工资的）+ "
                "无酬家庭帮工（家庭成员帮忙无报酬的），三者全部计入，不区分是否领工资也不看户籍。"
                "具体案例：夫妻两人共同经营小餐馆 + 雇了2个厨师 + 雇了1个服务员 + "
                "儿子暑假每天帮忙3小时无报酬 → 从业人数 = 2（业主）+ 2（厨师雇员）+ "
                "1（服务员雇员）+ 1（儿子无酬家庭帮工）= **6人**。"
                "常见错误：只算自己和配偶，漏算雇员和无酬家庭帮工。"
            )
            qa["keywords"] = ["F26.2", "从业人数", "餐馆", "夫妻店", "雇员", "无酬家庭帮工", "业主"]
            print(f"  [改] 294: F26.2 措辞改为更通用问法")

        if qa["id"] == "296":
            qa["question"] = (
                "老人每月有退休金，不打算再找工作，F34不找工作的主要原因选什么？"
            )
            qa["answer"] = (
                "选 **④有生活保障**。原因：①退休特指办理了正式退休手续领取养老金的人；"
                "④有生活保障更广义，包括有稳定生活来源（养老金、退休金、积蓄、家庭供养、"
                "领取失地农民保险、有房租/股息收入等）不需要找工作的人。"
                "老两口每月有退休金不打算工作→选④有生活保障。若被访者明确是办理退休手续的职工，"
                "也可以选①退休。怀孕/哺乳期在家照看孩子的选③照顾家庭，不选④。"
            )
            qa["keywords"] = ["F34", "退休金", "不找工作", "有生活保障", "退休", "养老金", "老人"]
            print(f"  [改] 296: F34 措辞改为具体场景问法")

    FAQ.write_text(
        json.dumps(faq, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    subprocess.run(["python", "scripts/validate_faq.py"], check=True)
    print("\n重建索引 ...")
    subprocess.run(["python", "scripts/build_kb.py"], check=True)
    subprocess.run(["python", "scripts/build_bm25.py", "--full"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())