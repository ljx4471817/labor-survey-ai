import importlib.util
import json
import sys
from pathlib import Path


def load_validate_faq():
    path = Path(__file__).resolve().parents[2] / "scripts" / "validate_faq.py"
    spec = importlib.util.spec_from_file_location("validate_faq", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bad_id_format_is_reported_once(tmp_path):
    module = load_validate_faq()
    qa = {
        "id": "bad-id",
        "category": "测试",
        "question": "这条测试数据是否会重复报错？",
        "answer": "这是一条足够长的测试答案，用来通过最小答案长度校验并验证 ID 格式只被检查一次。",
        "source": "测试依据",
        "keywords": ["测试", "ID", "校验"],
    }
    path = tmp_path / "faq.json"
    path.write_text(json.dumps([qa], ensure_ascii=False), encoding="utf-8")

    report = module.validate(path)

    assert report.by_code()["bad_id_format"] == 1
