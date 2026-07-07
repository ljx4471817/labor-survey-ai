"""is_ambiguous 启发式判定测试。

规则：
- 含 F 编号（F27） → False（明确问指标）
- 长度 ≤6 → True（信息不够）
- 通用短问词（怎么办/是什么/有哪些/怎么算/怎么样） → True
- 以"这个/那个"开头 → True（指代不明）
- 否则 False
"""
from app.rag.pure import is_ambiguous


def test_full_question_with_f_code_is_clear():
    # 带 F 编号 → False，不追问
    assert is_ambiguous("F27 的自营职业怎么算？") is False


def test_short_question_is_ambiguous():
    # 长度 ≤6 → True
    assert is_ambiguous("怎么办") is True
    assert is_ambiguous("怎么算") is True
    assert is_ambiguous("？") is True


def test_universal_short_phrase_is_ambiguous():
    # 通用短问词列表
    assert is_ambiguous("怎么办") is True
    assert is_ambiguous("怎么算？") is True
    assert is_ambiguous("是什么") is True
    assert is_ambiguous("有哪些？") is True
    assert is_ambiguous("怎么样？") is True


def test_zhe_na_prefix_is_ambiguous():
    # "这个/那个" 开头 → 指代不明
    assert is_ambiguous("这个怎么填？") is True
    assert is_ambiguous("那个是什么？") is True


def test_normal_question_is_clear():
    # 普通完整问题 → False
    assert is_ambiguous("失业人员如何认定？") is False
    assert is_ambiguous("调查期间是多久？") is False


def test_strips_trailing_punctuation_for_length():
    # 末尾标点 .?!,， 在 strip 后才计长度
    # "怎么办呢？？？" → 6 字 + 标点 → strip 后 6 → True
    assert is_ambiguous("怎么办呢？？？") is True