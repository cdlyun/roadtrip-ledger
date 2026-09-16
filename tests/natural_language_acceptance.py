"""中文一句话记账独立验收矩阵。

本文件故意不以 test_ 命名，不阻断常规 unittest discover。
需要评估识别完整度时单独运行：

    python3 tests/natural_language_acceptance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


CASES = (
    ("餐饮-中文数字", "在广元午餐吃米粉三十元",
     {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"}),
    ("餐饮-口语标点", "我刚在兰州晚餐吃牛肉面，付了四十五块",
     {"category": "meal", "amount": 45, "location": "兰州", "item": "牛肉面"}),
    ("衣物-购买句式", "在兰州买了一件冲锋衣，花了六百八十元",
     {"category": "clothing", "amount": 680, "location": "兰州", "item": "一件冲锋衣"}),
    ("住宿-自然句式", "在敦煌住民宿，住宿费三百二十元",
     {"category": "lodging", "amount": 320, "location": "敦煌", "item": "民宿"}),
    ("其他交通-目的地含酒店", "在西宁打车去酒店，花了四十五块",
     {"category": "transport", "amount": 45, "location": "西宁", "item": "打车去酒店"}),
    ("购物特产-中文数字", "在张掖买了当地特产，二百元",
     {"category": "shopping", "amount": 200, "location": "张掖", "item": "当地特产"}),
    ("ETC-自然句式", "在武威收费站过路费一百二十六元",
     {"category": "toll", "amount": 126, "location": "武威收费站", "item": "过路费"}),
    ("停车-逗号与数字", "在鸣沙山停车，20块",
     {"category": "parking", "amount": 20, "location": "鸣沙山", "item": "停车费"}),
    ("门票-数量与共计", "在莫高窟买了门票，两张共三百元",
     {"category": "ticket", "amount": 300, "location": "莫高窟", "item": "门票"}),
    ("旅行日用-结构化倒序", "旅行日用 地点大柴旦 买了矿泉水 花费十八元",
     {"category": "daily", "amount": 18, "location": "大柴旦", "item": "矿泉水"}),
    ("车辆费用-自然句式", "在敦煌补胎，支付80元",
     {"category": "vehicle", "amount": 80, "location": "敦煌", "item": "补胎"}),
    ("旅行服务-保险", "在兰州买了旅游保险，金额一百二十元",
     {"category": "service", "amount": 120, "location": "兰州", "item": "旅游保险"}),
    ("加油-98号完整字段", "大柴旦中国石化加98号汽油，支付金额460，升数45，当前里程33500",
     {"category": "fuel", "amount": 460, "location": "大柴旦中国石化", "fuel_grade": 98,
      "fuel_liters": 45, "odometer": 33500, "full_tank": None}),
    ("加油-默认95与中文数字", "格尔木加油四百六十元，四十五升，表显里程三万三千五百",
     {"category": "fuel", "amount": 460, "location": "格尔木", "fuel_grade": 95,
      "fuel_liters": 45, "odometer": 33500, "full_tank": None}),
    ("加油-字段倒序", "地点大柴旦中国石化 当前里程33500 升数45 支付金额460 加98号汽油",
     {"category": "fuel", "amount": 460, "location": "大柴旦中国石化", "fuel_grade": 98,
      "fuel_liters": 45, "odometer": 33500, "full_tank": None}),
    ("加油-口语在地点与未加满", "刚刚在张掖中国石油加95号油，四百元，五十升，里程三万四千，没加满",
     {"category": "fuel", "amount": 400, "location": "张掖中国石油", "fuel_grade": 95,
      "fuel_liters": 50, "odometer": 34000, "full_tank": False}),
    ("购物-分号与实付", "在兰州买了纪念品；实付88元",
     {"category": "shopping", "amount": 88, "location": "兰州", "item": "纪念品"}),
    ("停车-冒号与地点后置", "停车费：十五元，地点：张掖丹霞",
     {"category": "parking", "amount": 15, "location": "张掖丹霞", "item": "停车费"}),
    ("住宿-全字段标签", "住宿 消费内容青旅床位 金额80元 地点敦煌",
     {"category": "lodging", "amount": 80, "location": "敦煌", "item": "青旅床位"}),
    ("ETC-全字段标签", "ETC 金额126 地点武威收费站 消费内容高速通行费",
     {"category": "toll", "amount": 126, "location": "武威收费站", "item": "高速通行费"}),
)


def equal_value(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return actual is not None and abs(float(actual) - float(expected)) < 1e-9
    return actual == expected


def main():
    failures = []
    for name, phrase, expected in CASES:
        result = app.parse_text(phrase)
        actual = result["recognized"]
        mismatches = {
            field: {"expected": value, "actual": actual.get(field)}
            for field, value in expected.items()
            if not equal_value(actual.get(field), value)
        }
        if mismatches:
            failures.append((name, phrase, mismatches))
            fields = ", ".join(
                f"{field}={values['actual']!r}→{values['expected']!r}"
                for field, values in mismatches.items()
            )
            print(f"FAIL\t{name}\t{fields}\t{phrase}")
        else:
            print(f"PASS\t{name}\t{phrase}")
    print(f"\n总计：{len(CASES)} 条，通过 {len(CASES)-len(failures)} 条，失败 {len(failures)} 条")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
