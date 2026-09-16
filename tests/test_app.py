import os
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from itertools import permutations
from datetime import datetime
from io import BytesIO
from pathlib import Path
from email.message import Message

TMP = tempfile.TemporaryDirectory()
os.environ["ROADTRIP_DB"] = str(Path(TMP.name) / "test.db")

import app


class ParserTests(unittest.TestCase):
    def setUp(self):
        if app.DB_PATH.exists():
            app.DB_PATH.unlink()
        app.init_db()

    def test_deepseek_semantic_completion_only_fills_missing_fields_and_marks_review(self):
        previous_key, previous_open = app.DEEPSEEK_API_KEY, app.urlopen
        sent = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return json.dumps({"choices": [{"message": {"content": json.dumps({
                    "category": "meal", "location": "兰州黄河边", "item": "牛肉面", "full_tank": None,
                })}}]}).encode()

        def fake_open(request, timeout):
            sent.append((request, timeout))
            return Response()

        try:
            app.DEEPSEEK_API_KEY = "test-key-not-a-real-secret"
            app.urlopen = fake_open
            parsed = app.parse_text("花了30元", ai_enhance=True)
        finally:
            app.DEEPSEEK_API_KEY, app.urlopen = previous_key, previous_open

        self.assertEqual(parsed["recognized"]["category"], "meal")
        self.assertEqual(parsed["recognized"]["location"], "兰州黄河边")
        self.assertEqual(parsed["recognized"]["item"], "牛肉面")
        self.assertEqual(parsed["recognized"]["amount"], 30)
        self.assertEqual(parsed["field_meta"]["category"]["state"], "review")
        self.assertEqual(parsed["field_meta"]["amount"]["state"], "certain")
        self.assertEqual(parsed["recognition_notice"], "已用 DeepSeek 补全口语字段，请核对标注内容")
        self.assertEqual(parsed["records"][0]["field_meta"]["item"]["state"], "review")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("test-key-not-a-real-secret", sent[0][0].data.decode())

    def test_deepseek_failure_or_invalid_json_falls_back_to_local_rules(self):
        previous_key, previous_open = app.DEEPSEEK_API_KEY, app.urlopen
        try:
            app.DEEPSEEK_API_KEY = "test-key-not-a-real-secret"

            def failed(*_, **__):
                raise TimeoutError("network unavailable")

            app.urlopen = failed
            parsed = app.parse_text("花了30元", ai_enhance=True)
        finally:
            app.DEEPSEEK_API_KEY, app.urlopen = previous_key, previous_open
        self.assertIsNone(parsed["recognized"]["category"])
        self.assertEqual(parsed["recognized"]["amount"], 30)
        self.assertNotIn("recognition_notice", parsed)

    def test_deepseek_malformed_response_and_fuel_category_keep_local_safety_rules(self):
        previous_key, previous_open = app.DEEPSEEK_API_KEY, app.urlopen

        class Response:
            def __init__(self, body):
                self.body = body
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return self.body

        try:
            app.DEEPSEEK_API_KEY = "test-key-not-a-real-secret"
            for malformed in (b"[]", b'{"choices":[{"message":null}]}',
                              b'{"choices":[{"message":{"content":"{\\\"category\\\":[]}"}}]}'):
                app.urlopen = lambda *_args, body=malformed, **_kwargs: Response(body)
                parsed = app.parse_text("花了30元", ai_enhance=True)
                self.assertIsNone(parsed["recognized"]["category"])
                self.assertNotIn("recognition_notice", parsed)

            body = json.dumps({"choices": [{"message": {"content": json.dumps({
                "category": "fuel", "location": "大柴旦", "item": None, "full_tank": None,
            })}}]}).encode()
            app.urlopen = lambda *_args, **_kwargs: Response(body)
            parsed = app.parse_text("灌了98号，460元，45升，当前里程33500", ai_enhance=True)
        finally:
            app.DEEPSEEK_API_KEY, app.urlopen = previous_key, previous_open
        fields = parsed["recognized"]
        self.assertEqual((fields["category"], fields["fuel_grade"], fields["fuel_liters"], fields["odometer"]),
                         ("fuel", 98, 45, 33500))
        self.assertFalse(any(gap["field"] == "category" for gap in parsed["missing"]))
        self.assertEqual(fields["amount"], 460)

    def test_silent_parse_never_calls_deepseek(self):
        previous_key, previous_open = app.DEEPSEEK_API_KEY, app.urlopen
        try:
            app.DEEPSEEK_API_KEY = "test-key-not-a-real-secret"
            app.urlopen = lambda *_args, **_kwargs: self.fail("silent parsing must not make an AI request")
            parsed = app.parse_text("花了30元", ai_enhance=False)
        finally:
            app.DEEPSEEK_API_KEY, app.urlopen = previous_key, previous_open
        self.assertEqual(parsed["recognized"]["amount"], 30)
        self.assertNotIn("recognition_notice", parsed)

    def test_fuel_defaults_to_95_and_lists_metric_gaps(self):
        result = app.parse_text("在广元加了500元汽油")
        self.assertEqual(result["recognized"]["fuel_grade"], 95)
        self.assertEqual(result["recognized"]["location"], "广元")
        self.assertTrue(result["can_save"])
        fields = {x["field"] for x in result["missing"]}
        self.assertEqual({"fuel_liters", "odometer"}, fields)
        self.assertIsNone(result["recognized"]["full_tank"])

    def test_fuel_simple_fields_are_all_matched(self):
        result = app.parse_text("加98号汽油，支付金额460，升数45，当前里程33500")
        got = result["recognized"]
        self.assertEqual(got["category"], "fuel")
        self.assertEqual(got["amount"], 460)
        self.assertEqual(got["fuel_grade"], 98)
        self.assertEqual(got["fuel_liters"], 45)
        self.assertEqual(got["odometer"], 33500)
        self.assertIsNone(got["full_tank"])
        self.assertIsNone(got["fuel_unit_price"])
        self.assertTrue(result["can_save"])
        self.assertEqual(
            [(gap["field"], gap["level"]) for gap in result["missing"]],
            [("location", "optional")],
        )

    def test_fuel_station_prefix_and_all_fields_are_matched(self):
        result = app.parse_text("大柴旦中国石化加98号汽油，支付金额460，升数45，当前里程33500")
        got = result["recognized"]
        self.assertEqual(got["category"], "fuel")
        self.assertEqual(got["location"], "大柴旦中国石化")
        self.assertEqual(got["amount"], 460)
        self.assertEqual(got["fuel_grade"], 98)
        self.assertEqual(got["fuel_liters"], 45)
        self.assertEqual(got["odometer"], 33500)
        self.assertIsNone(got["full_tank"])
        self.assertIsNone(got["fuel_unit_price"])
        self.assertFalse(result["missing"])

    def test_fuel_station_prefix_defaults_to_95(self):
        result = app.parse_text("大柴旦中国石化加油460元，45升，当前里程33500")
        got = result["recognized"]
        self.assertEqual(got["location"], "大柴旦中国石化")
        self.assertEqual(got["fuel_grade"], 95)
        self.assertEqual(got["amount"], 460)
        self.assertEqual(got["fuel_liters"], 45)
        self.assertEqual(got["odometer"], 33500)

    def test_relative_time_is_not_mistaken_for_fuel_location(self):
        result = app.parse_text("刚才加98号汽油，支付金额460，升数45，当前里程33500")
        self.assertIsNone(result["recognized"]["location"])

    def test_today_prefix_keeps_real_fuel_location(self):
        result = app.parse_text("今天大柴旦中国石化加98号汽油，支付金额460，升数45，当前里程33500")
        self.assertEqual(result["recognized"]["location"], "大柴旦中国石化")

    def test_fuel_chinese_liters_and_yuan_aliases(self):
        result = app.parse_text("加油四十点二升，付款五百块，表显里程三万三千五百")
        got = result["recognized"]
        self.assertEqual(got["amount"], 500)
        self.assertEqual(got["fuel_liters"], 40.2)
        self.assertEqual(got["odometer"], 33500)

    def test_saved_fuel_without_listed_price_keeps_paid_rate_ephemeral(self):
        trip = app.create_trip({"name": "自动字段", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({"trip_id": trip["id"], "raw_text": "加油500元",
            "recognized": {"category": "fuel", "amount": 500, "fuel_liters": 40, "odometer": 10300}})
        self.assertIsNone(entry["fuel_unit_price"])
        self.assertIsNone(entry["fuel_calculated_amount_cents"])
        self.assertIsNone(entry["fuel_discount_cents"])
        self.assertIsNone(entry["full_tank"])

    def test_explicit_98_keeps_unstated_listed_price_empty(self):
        result = app.parse_text("在兰州加了40升98号汽油，400元，里程表33420，加满")
        got = result["recognized"]
        self.assertEqual(got["fuel_grade"], 98)
        self.assertIsNone(got["fuel_unit_price"])
        self.assertTrue(got["full_tank"])
        self.assertFalse(any(x["level"] == "required" for x in result["missing"]))

    def test_unit_price_is_not_mistaken_for_total_or_used_to_guess_liters(self):
        result = app.parse_text("油价8元每升，加500元，里程10300，未加满")
        self.assertEqual(result["recognized"]["amount"], 500)
        self.assertEqual(result["recognized"]["fuel_unit_price"], 8)
        self.assertIsNone(result["recognized"]["fuel_liters"])
        self.assertIn("fuel_liters", {gap["field"] for gap in result["missing"]})

    def test_chinese_amount_is_supported(self):
        self.assertEqual(app.parse_text("高速费一百二十六元")["recognized"]["amount"], 126)
        self.assertEqual(app.parse_text("加油五百元")["recognized"]["amount"], 500)

    def test_conflicting_or_invalid_fuel_grade_blocks_save(self):
        for text in ("95号和98号汽油加500元", "92号汽油加500元"):
            result = app.parse_text(text)
            self.assertFalse(result["can_save"])
            self.assertIsNone(result["recognized"]["fuel_grade"])
            self.assertIn("fuel_grade", {x["field"] for x in result["missing"]})

    def test_fuel_grade_common_transcription_forms(self):
        for text in ("加98汽油500元", "加九八号汽油500元"):
            self.assertEqual(app.parse_text(text)["recognized"]["fuel_grade"], 98)
        self.assertEqual(app.parse_text("加九五号汽油500元")["recognized"]["fuel_grade"], 95)

    def test_trip_requires_start_odometer(self):
        with self.assertRaisesRegex(ValueError, "出发里程必填"):
            app.create_trip({"name": "缺里程", "origin": "成都"})

    def test_refund_and_deposit_are_explicitly_blocked(self):
        for text in ("酒店退款300元", "酒店押金500元"):
            result = app.parse_text(text)
            self.assertFalse(result["can_save"])
            self.assertIn("transaction_type", {x["field"] for x in result["missing"]})

    def test_categories_and_required_amount(self):
        self.assertEqual(app.parse_text("过路费126元")["recognized"]["category"], "toll")
        result = app.parse_text("今晚住酒店")
        self.assertFalse(result["can_save"])
        self.assertIn("amount", {x["field"] for x in result["missing"]})

    def test_eating_clothing_lodging_transport_and_shopping_categories(self):
        cases = {
            "在兰州吃午餐86元": "meal",
            "酒店住宿两晚680元": "lodging",
            "买衣服300元": "clothing",
            "麦积山门票160元": "ticket",
            "停车20元": "parking",
            "高速费126元": "toll",
            "买了特产200元": "shopping",
            "打车35元": "transport",
        }
        for text, category in cases.items():
            with self.subTest(text=text):
                self.assertEqual(app.parse_text(text)["recognized"]["category"], category)

    def test_spoken_location_people_and_nights_are_recognized(self):
        meal = app.parse_text("在兰州吃午餐，三个人，86元")
        self.assertEqual(meal["recognized"]["location"], "兰州")
        self.assertEqual(meal["recognized"]["people"], 3)
        lodging = app.parse_text("在天水住宿两晚680元")
        self.assertEqual(lodging["recognized"]["location"], "天水")
        self.assertEqual(lodging["recognized"]["nights"], 2)

    def test_structured_spoken_expense_with_item(self):
        parsed = app.parse_text("旅行日用 金额500 地点兰州 买了一瓶啤酒")
        self.assertEqual(parsed["recognized"]["category"], "daily")
        self.assertEqual(parsed["recognized"]["amount"], 500)
        self.assertEqual(parsed["recognized"]["location"], "兰州")
        self.assertEqual(parsed["recognized"]["item"], "一瓶啤酒")
        self.assertNotIn("item", {gap["field"] for gap in parsed["missing"]})

    def test_meal_location_item_and_amount_in_natural_order(self):
        parsed = app.parse_text("在广元午餐吃米粉 30 元")
        got = parsed["recognized"]
        self.assertEqual(got["category"], "meal")
        self.assertEqual(got["location"], "广元")
        self.assertEqual(got["item"], "米粉")
        self.assertEqual(got["amount"], 30)
        self.assertFalse(parsed["missing"])

    def test_meal_item_with_chinese_amount(self):
        parsed = app.parse_text("在广元午餐吃米粉三十元")
        got = parsed["recognized"]
        self.assertEqual(got["category"], "meal")
        self.assertEqual(got["location"], "广元")
        self.assertEqual(got["item"], "米粉")
        self.assertEqual(got["amount"], 30)

    def test_natural_language_category_uses_action_over_place_name(self):
        cases = {
            "在张掖酒店停车，花了20元": "parking",
            "昨天在兰州打车到酒店，35块": "transport",
            "在加油站买矿泉水，付了12元": "daily",
            "在西宁换轮胎付了400": "vehicle",
            "在莫高窟买了门票，两张共三百元": "ticket",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(app.parse_text(phrase)["recognized"]["category"], expected)

    def test_venue_words_do_not_override_the_actual_purchase(self):
        cases = {
            "酒店吃早餐30元": ("meal", "早餐"),
            "在收费站买矿泉水5元": ("daily", "矿泉水"),
            "在停车场买矿泉水5元": ("daily", "矿泉水"),
            "在中石油加油站买矿泉水5元": ("daily", "矿泉水"),
            "在饭店停车20元": ("parking", "停车费"),
            "在饭店付停车费20元": ("parking", "停车费"),
            "在停车场住宿一晚200元": ("lodging", "住宿"),
        }
        for phrase, (category, item) in cases.items():
            with self.subTest(phrase=phrase):
                got = app.parse_text(phrase)["recognized"]
                self.assertEqual(got["category"], category)
                self.assertEqual(got["item"], item)
        station = app.parse_text("在中石油加油站买矿泉水5元")["recognized"]
        self.assertEqual(station["location"], "中石油加油站")

    def test_fuel_labeled_fields_work_in_all_orders(self):
        fields = (
            "地点大柴旦中国石化", "加98号汽油", "支付金额460",
            "升数45", "当前里程33500", "记录时间2026-09-12 15:30",
        )
        for parts in permutations(fields):
            phrase = " ".join(parts)
            got = app.parse_text(phrase)["recognized"]
            self.assertEqual(got["category"], "fuel", phrase)
            self.assertEqual(got["amount"], 460, phrase)
            self.assertEqual(got["location"], "大柴旦中国石化", phrase)
            self.assertEqual(got["fuel_grade"], 98, phrase)
            self.assertEqual(got["fuel_liters"], 45, phrase)
            self.assertEqual(got["odometer"], 33500, phrase)
            self.assertEqual(got["occurred_at"], "2026-09-12T15:30:00", phrase)

    def test_spoken_unit_price_is_not_total_and_discount_is_not_conflict(self):
        stated = app.parse_text("加95号汽油340元40升，单价8块5每升")
        self.assertEqual(stated["recognized"]["amount"], 340)
        self.assertEqual(stated["recognized"]["fuel_unit_price"], 8.5)
        self.assertTrue(stated["can_save"])
        for phrase in (
            "油价8块5每升，加45升98号汽油，支付460元，当前里程33500",
            "支付460元，当前里程33500，加45升98号汽油，油价8块5每升",
        ):
            with self.subTest(phrase=phrase):
                result = app.parse_text(phrase)
                got = result["recognized"]
                self.assertEqual(got["amount"], 460)
                self.assertEqual(got["fuel_unit_price"], 8.5)
                self.assertEqual(got["fuel_liters"], 45)
                self.assertTrue(result["can_save"])
                self.assertNotIn("fuel_price_conflict", {gap["field"] for gap in result["missing"]})

        trip = app.create_trip({"name": "油价优惠", "origin": "成都", "start_odometer": 33000})
        phrase = "油价8块5每升，加45升98号汽油，支付460元，当前里程33500"
        parsed = app.parse_text(phrase)
        entry = app.save_entry({"trip_id": trip["id"], "raw_text": phrase,
                                "recognized": parsed["recognized"]})
        self.assertEqual((entry["fuel_calculated_amount_cents"], entry["fuel_discount_cents"]), (38250, -7750))

    def test_natural_language_amount_supports_labels_and_colloquial_decimals(self):
        self.assertEqual(app.parse_text("在西宁换轮胎付了400")["recognized"]["amount"], 400)
        self.assertEqual(app.parse_text("午餐米粉三十块五")["recognized"]["amount"], 30.5)
        self.assertEqual(app.parse_text("晚餐牛肉面45块5毛")["recognized"]["amount"], 45.5)

    def test_natural_language_location_supports_prefix_suffix_and_actions(self):
        cases = {
            "兰州午餐吃牛肉面30元": "兰州",
            "吃米粉30元在广元": "广元",
            "ETC在武威收费站扣了65元": "武威收费站",
            "在敦煌补胎，支付80元": "敦煌",
            "晚上7点半在西宁住青旅床位，支付一百二十元": "西宁",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(app.parse_text(phrase)["recognized"]["location"], expected)

    def test_natural_language_item_is_cleaned_for_multiple_categories(self):
        cases = {
            "在广元吃米粉30元": "米粉",
            "在西宁打车去酒店，45块": "打车去酒店",
            "在敦煌买了两顶帽子共180元": "两顶帽子",
            "在敦煌住青旅床位，支付120元": "青旅床位",
            "在鸣沙山停车，20块": "停车费",
            "在西宁换轮胎付了400": "换轮胎",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(app.parse_text(phrase)["recognized"]["item"], expected)

    def test_spoken_record_time_supports_absolute_and_relative_chinese_time(self):
        absolute = app.parse_text("2026年9月12日下午3点半，在敦煌买门238元")
        self.assertEqual(absolute["recognized"]["occurred_at"], "2026-09-12T15:30:00")
        fixed_now = datetime(2026, 9, 10, 10, 15, 20)
        self.assertEqual(
            app.parse_spoken_datetime("昨天晚上八点在兰州吃饭", fixed_now),
            "2026-09-09T20:00:00",
        )
        self.assertEqual(app.parse_spoken_datetime("没有说时间的账", fixed_now), None)

    def test_fuel_fields_support_reordered_labels_and_station_clause(self):
        phrases = (
            "地点大柴旦中国石化 当前里程33500 升数45 支付金额460 加98号汽油",
            "大柴旦中国石化，当前里程33500，加了45升九十八号汽油，实付460元",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                got = app.parse_text(phrase)["recognized"]
                self.assertEqual(
                    (got["category"], got["amount"], got["location"], got["fuel_grade"],
                     got["fuel_liters"], got["odometer"]),
                    ("fuel", 460, "大柴旦中国石化", 98, 45, 33500),
                )

    def test_fuel_station_can_appear_after_other_fields(self):
        parsed = app.parse_text("加了九十五号汽油四十点五升，在格尔木中国石油，里程表读数三万四千，付款四百二十块")
        got = parsed["recognized"]
        self.assertEqual(got["location"], "格尔木中国石油")
        self.assertEqual((got["amount"], got["fuel_grade"], got["fuel_liters"], got["odometer"]),
                         (420, 95, 40.5, 34000))

    def test_custom_occurred_at_is_saved_and_returned(self):
        trip = app.create_trip({"name": "记录时间", "origin": "成都", "start_odometer": 10000})
        occurred_at = "2026-09-10T12:34:56"
        entry = app.save_entry({
            "trip_id": trip["id"],
            "raw_text": "在广元午餐吃米粉 30 元",
            "recognized": {
                "category": "meal", "amount": 30, "location": "广元",
                "item": "米粉", "occurred_at": occurred_at,
            },
        })
        self.assertEqual(entry["occurred_at"], occurred_at)
        saved = app.dashboard(trip["id"])["entries"]
        self.assertEqual(saved[0]["occurred_at"], occurred_at)

    def test_invalid_occurred_at_is_rejected_by_api(self):
        trip = app.create_trip({"name": "非法时间", "origin": "成都", "start_odometer": 10000})
        payload = {
            "trip_id": trip["id"],
            "raw_text": "在广元吃米粉30元",
            "recognized": {
                "category": "meal", "amount": 30, "location": "广元",
                "item": "米粉", "occurred_at": "不是有效时间",
            },
        }
        responses = []
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/entries"
        handler.body = lambda: payload
        handler.json_response = lambda body, status=200: responses.append((body, int(status)))

        handler.do_POST()

        self.assertEqual(responses[0][1], 400)
        self.assertIn("记录时间", responses[0][0]["error"])

    def test_toll_is_displayed_as_etc(self):
        self.assertEqual(app.parse_text("过路费126元")["recognized"]["category_label"], "ETC")

    def test_vehicle_label_is_canonical_for_new_legacy_dashboard_and_export(self):
        trip = app.create_trip({"name": "车辆标签", "origin": "成都", "start_odometer": 10000})
        created = app.save_entry({"trip_id": trip["id"], "raw_text": "补胎80元",
            "recognized": {"category": "vehicle", "amount": 80}})
        self.assertEqual(created["category_label"], "车辆费用")
        entry_id, version, amount = created["id"], created["version"], created["amount"]
        # Simulate the label written by the earlier release without changing its
        # stable category key, amount, identity, or revision.
        with app.connect() as db:
            db.execute("UPDATE entries SET category_label='车辆异常' WHERE id=?", (entry_id,))
        report = app.dashboard(trip["id"])
        self.assertEqual(report["by_category"], {"车辆费用": 80})
        self.assertEqual(report["vehicle_cost"], 0)
        legacy = next(row for row in report["entries"] if row["id"] == entry_id)
        self.assertEqual((legacy["id"], legacy["version"], legacy["amount"]), (entry_id, version, amount))
        with zipfile.ZipFile(BytesIO(app.export_workbook(trip["id"]))) as workbook:
            detail = workbook.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("车辆费用", detail)
        deleted = app.delete_entry(entry_id, {"trip_id": trip["id"], "client_id": created["client_id"], "client_revision": version + 1})
        self.assertEqual((deleted["entry_id"], deleted["trip_id"]), (entry_id, trip["id"]))
        with app.connect() as db:
            deleted_row = db.execute("SELECT id,amount,version FROM entries WHERE id=?", (entry_id,)).fetchone()
        self.assertEqual((deleted_row["id"], deleted_row["amount"], deleted_row["version"]), (entry_id, amount, version + 1))
        with zipfile.ZipFile(BytesIO(app.export_workbook(trip["id"]))) as workbook:
            trash = workbook.read("xl/worksheets/sheet4.xml").decode()
        self.assertIn("车辆费用", trash)
        revisions = app.record_revisions("entry", entry_id)
        self.assertTrue(any("车辆异常" in row["snapshot"] for row in revisions))

    def test_all_expenses_are_in_cash_total_but_clothing_is_not_core(self):
        trip = app.create_trip({"name": "全消费", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "买衣服300元",
            "recognized": {"category": "clothing", "amount": 300}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "晚餐100元",
            "recognized": {"category": "meal", "amount": 100}})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["cash_spend"], 400)
        self.assertEqual(report["core_cash_spend"], 100)
        self.assertEqual(report["by_category"], {"衣物": 300, "餐饮": 100})

    def test_full_to_full_consumption(self):
        trip = app.create_trip({"name": "成都到兰州", "origin": "成都", "destination": "兰州", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油300元",
            "recognized": {"category": "fuel", "amount": 300, "fuel_grade": 95,
                           "fuel_liters": 30, "odometer": 10300, "full_tank": False}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油200元加满",
            "recognized": {"category": "fuel", "amount": 200, "fuel_grade": 95,
                           "fuel_liters": 20, "odometer": 10500, "full_tank": True}})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["fuel"]["status"], "insufficient_data")
        self.assertEqual(report["distance_km"], 500)
        self.assertIsNone(report["fuel"]["l_per_100km"])
        self.assertIsNone(report["fuel"]["cost_per_km"])
        self.assertEqual(report["fuel"]["cash_spend"], 500)
        self.assertEqual(report["fuel"]["consumption_cost"], 500)
        self.assertEqual(report["core_trip_cost"], 500)
        self.assertEqual(report["vehicle_cost"], 500)
        self.assertEqual(report["vehicle_cost_per_km"], 1.0)

    def test_vehicle_cost_and_per_kilometer_scope(self):
        trip = app.create_trip({"name": "用车口径", "origin": "成都", "start_odometer": 10000})
        for text, category, amount in (("高速费100元", "toll", 100), ("停车50元", "parking", 50),
                                       ("住宿300元", "lodging", 300), ("买衣服200元", "clothing", 200)):
            app.save_entry({"trip_id": trip["id"], "raw_text": text,
                            "recognized": {"category": category, "amount": amount}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "到站加油500元加满",
            "recognized": {"category": "fuel", "amount": 500, "fuel_grade": 95,
                           "fuel_liters": 50, "odometer": 10500, "full_tank": True}})
        app.finish_trip(trip["id"], {"end_odometer": 10500})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["total_spend"], 1150)
        self.assertEqual(report["vehicle_cost"], 650)
        self.assertEqual(report["vehicle_cost_per_km"], 1.3)

    def test_excel_export_contains_summary_and_all_details(self):
        trip = app.create_trip({"name": "导出测试", "origin": "成都", "destination": "兰州", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "旅行日用 金额500 地点兰州 买了一瓶啤酒",
            "recognized": {"category": "daily", "amount": 500, "location": "兰州", "item": "一瓶啤酒"}})
        payload = app.export_workbook()
        self.assertTrue(payload.startswith(b"PK"))
        with zipfile.ZipFile(BytesIO(payload)) as workbook:
            self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
            self.assertIn("xl/worksheets/sheet2.xml", workbook.namelist())
            summary = workbook.read("xl/worksheets/sheet1.xml").decode()
            detail = workbook.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("导出测试", summary)
        self.assertIn("总支出(元)", summary)
        self.assertIn("兰州", detail)
        self.assertIn("一瓶啤酒", detail)

    def test_gps_region_and_accuracy_are_saved_and_exported(self):
        trip = app.create_trip({"name": "GPS导出", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({"trip_id": trip["id"], "raw_text": "在大柴旦午餐30元",
            "recognized": {"category": "meal", "amount": 30, "location": "青海 · 海西 · 大柴旦附近",
                           "region": "青海 · 海西 · 大柴旦附近", "latitude": 37.51,
                           "longitude": 95.22, "gps_accuracy": 18, "item": "午餐"}})
        self.assertEqual(entry["region"], "青海 · 海西 · 大柴旦附近")
        self.assertEqual(entry["latitude"], 37.51)
        with zipfile.ZipFile(BytesIO(app.export_workbook())) as workbook:
            detail = workbook.read("xl/worksheets/sheet2.xml").decode()
        for value in ("中文大致地区", "纬度", "经度", "定位精度(米)", "大柴旦附近", "37.51", "95.22"):
            self.assertIn(value, detail)

    def test_offline_client_id_is_idempotent(self):
        trip = app.create_trip({"name": "离线同步", "origin": "成都", "start_odometer": 10000})
        payload = {"trip_id": trip["id"], "client_id": "phone-entry-001", "raw_text": "餐饮30元",
                   "recognized": {"category": "meal", "amount": 30, "item": "米粉"}}
        first = app.save_entry(payload)
        second = app.save_entry(payload)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["validation"]["idempotent"])
        self.assertEqual(len(app.dashboard(trip["id"])["entries"]), 1)

    def test_starting_full_tank_entry_is_excluded(self):
        trip = app.create_trip({"name": "基准油箱", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "出发前加油400元加满",
            "recognized": {"category": "fuel", "amount": 400, "fuel_grade": 95,
                           "fuel_liters": 50, "odometer": 10000, "full_tank": True}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "到站加油400元加满",
            "recognized": {"category": "fuel", "amount": 400, "fuel_grade": 95,
                           "fuel_liters": 40, "odometer": 10500, "full_tank": True}})
        fuel = app.dashboard(trip["id"])["fuel"]
        self.assertEqual(fuel["liters"], 40)
        self.assertEqual(fuel["l_per_100km"], 8)
        self.assertEqual(fuel["cost_per_km"], 1.6)
        self.assertEqual(fuel["cash_spend"], 800)
        self.assertEqual(fuel["consumption_cost"], 800)

    def test_finished_without_final_full_tank_still_estimates_realtime(self):
        trip = app.create_trip({"name": "未加满", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "中途加油300元未加满",
            "recognized": {"category": "fuel", "amount": 300, "fuel_grade": 95,
                           "fuel_liters": 30, "odometer": 10300, "full_tank": False}})
        app.finish_trip(trip["id"], {"end_odometer": 10500})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["fuel"]["status"], "insufficient_data")
        self.assertIsNone(report["fuel"]["l_per_100km"])
        self.assertEqual(report["vehicle_cost_per_km"], 0.6)

    def test_common_not_full_transcriptions(self):
        for phrase in ("没加满", "没有加满", "未加满", "不是满油", "没有满油"):
            parsed = app.parse_text(f"加油300元，里程10300，{phrase}")
            self.assertIs(parsed["recognized"]["full_tank"], False)

    def test_finished_trip_uses_end_odometer_for_realtime_estimate(self):
        trip = app.create_trip({"name": "末次未对齐", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "中途加满300元",
            "recognized": {"category": "fuel", "amount": 300, "fuel_grade": 95,
                           "fuel_liters": 30, "odometer": 10300, "full_tank": True}})
        app.finish_trip(trip["id"], {"end_odometer": 10500})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["fuel"]["status"], "reliable_full_tank")
        self.assertEqual(report["fuel"]["l_per_100km"], 10)
        self.assertEqual(report["vehicle_cost"], 300)

    def test_finish_trip_distance(self):
        trip = app.create_trip({"name": "短途", "origin": "成都", "start_odometer": 200})
        app.finish_trip(trip["id"], {"end_odometer": 320})
        self.assertEqual(app.dashboard(trip["id"])["distance_km"], 120)

    def test_finished_trip_can_be_reopened_without_losing_entries(self):
        trip = app.create_trip({"name": "防误结束", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({"trip_id": trip["id"], "raw_text": "餐饮30元",
            "recognized": {"category": "meal", "amount": 30, "item": "米粉"}})
        app.finish_trip(trip["id"], {"end_odometer": 10100})
        reopened = app.reopen_trip(trip["id"])
        self.assertEqual(reopened["status"], "active")
        self.assertIsNone(reopened["ended_at"])
        self.assertIsNone(reopened["end_odometer"])
        self.assertEqual(app.dashboard(trip["id"])["entries"][0]["id"], entry["id"])

    def test_reopen_is_blocked_when_another_trip_is_active(self):
        first = app.create_trip({"name": "第一段", "origin": "成都", "start_odometer": 10000})
        app.finish_trip(first["id"], {"end_odometer": 10100})
        app.create_trip({"name": "第二段", "origin": "兰州", "start_odometer": 10100})
        with self.assertRaisesRegex(ValueError, "已有进行中"):
            app.reopen_trip(first["id"])

    def test_finish_cannot_be_before_last_entry_odometer(self):
        trip = app.create_trip({"name": "里程校验", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油300元",
            "recognized": {"category": "fuel", "amount": 300, "fuel_grade": 95,
                           "fuel_liters": 30, "odometer": 10300, "full_tank": False}})
        with self.assertRaisesRegex(ValueError, "最后记录"):
            app.finish_trip(trip["id"], {"end_odometer": 10200})

    def test_duplicate_requires_explicit_confirmation(self):
        trip = app.create_trip({"name": "重复校验", "origin": "成都", "start_odometer": 10000})
        payload = {"trip_id": trip["id"], "raw_text": "高速费126元",
                   "recognized": {"category": "toll", "amount": 126, "location": "广元"}}
        app.save_entry(payload)
        with self.assertRaisesRegex(ValueError, "可能与最近记录重复"):
            app.save_entry(payload)
        payload["confirm_duplicate"] = True
        app.save_entry(payload)
        self.assertEqual(len(app.dashboard(trip["id"])["entries"]), 2)

    def test_update_regular_expense_persists_fields_and_recalculates_dashboard(self):
        trip = app.create_trip({"name": "修改餐饮", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"],
            "raw_text": "在广元午餐吃米粉30元",
            "recognized": {
                "category": "meal", "amount": 30, "location": "广元", "item": "米粉",
                "occurred_at": "2026-09-10T12:00:00",
            },
        })
        self.assertEqual(app.dashboard(trip["id"])["cash_spend"], 30)

        updated = app.update_entry(entry["id"], {
            "trip_id": trip["id"],
            "raw_text": "在兰州晚餐吃牛肉面45元",
            "recognized": {
                "category": "meal", "amount": 45, "location": "兰州", "item": "牛肉面",
                "occurred_at": "2026-09-10T19:30:00",
            },
        })

        self.assertEqual(updated["amount"], 45)
        self.assertEqual(updated["location"], "兰州")
        self.assertEqual(updated["note"], "牛肉面")
        self.assertEqual(updated["occurred_at"], "2026-09-10T19:30:00")
        report = app.dashboard(trip["id"])
        self.assertEqual(report["cash_spend"], 45)
        self.assertEqual(report["by_category"], {"餐饮": 45})
        self.assertEqual(len(report["entries"]), 1)
        self.assertEqual(report["entries"][0]["id"], entry["id"])

    def test_update_fuel_recalculates_consumption_and_vehicle_cost(self):
        trip = app.create_trip({"name": "修改油费", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"],
            "raw_text": "加95号汽油400元，40升，当前里程10500",
            "recognized": {
                "category": "fuel", "amount": 400, "location": "广元加油站",
                "fuel_grade": 95, "fuel_liters": 40, "odometer": 10500, "full_tank": True,
                "occurred_at": "2026-09-10T10:00:00",
            },
        })
        before = app.dashboard(trip["id"])
        self.assertEqual(before["fuel"]["l_per_100km"], 8)
        self.assertEqual(before["vehicle_cost"], 400)

        app.update_entry(entry["id"], {
            "trip_id": trip["id"],
            "raw_text": "加95号汽油500元，50升，当前里程10500",
            "recognized": {
                "category": "fuel", "amount": 500, "location": "广元加油站",
                "fuel_grade": 95, "fuel_liters": 50, "odometer": 10500, "full_tank": True,
                "occurred_at": "2026-09-10T10:00:00",
            },
        })

        report = app.dashboard(trip["id"])
        self.assertEqual(report["fuel"]["liters"], 50)
        self.assertEqual(report["fuel"]["l_per_100km"], 10)
        self.assertEqual(report["fuel"]["cost_per_km"], 1)
        self.assertEqual(report["fuel"]["cash_spend"], 500)
        self.assertEqual(report["vehicle_cost"], 500)
        self.assertEqual(report["vehicle_cost_per_km"], 1)

    def test_update_rejects_invalid_time_odometer_and_fuel_grade(self):
        trip = app.create_trip({"name": "修改校验", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"],
            "raw_text": "加95号汽油300元，30升，当前里程10300",
            "recognized": {
                "category": "fuel", "amount": 300, "fuel_grade": 95,
                "fuel_liters": 30, "odometer": 10300, "full_tank": True,
                "occurred_at": "2026-09-10T10:00:00",
            },
        })
        base = {
            "category": "fuel", "amount": 300, "fuel_grade": 95,
            "fuel_liters": 30, "odometer": 10300, "full_tank": True,
            "occurred_at": "2026-09-10T10:00:00",
        }
        cases = (
            ("invalid-time", {**base, "occurred_at": "非法时间"}, "记录时间"),
            ("odometer-before-start", {**base, "odometer": 9999}, "里程"),
            ("unsupported-grade", {**base, "fuel_grade": 92}, "油号"),
        )
        for name, recognized, error in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, error):
                    app.update_entry(entry["id"], {
                        "trip_id": trip["id"], "raw_text": "修改加油记录", "recognized": recognized,
                    })

        saved = app.dashboard(trip["id"])["entries"][0]
        self.assertEqual(saved["fuel_grade"], 95)
        self.assertEqual(saved["odometer"], 10300)
        self.assertEqual(saved["occurred_at"], "2026-09-10T10:00:00")

    def test_update_duplicate_check_excludes_self_but_blocks_other_entry(self):
        trip = app.create_trip({"name": "修改重复", "origin": "成都", "start_odometer": 10000})

        def meal_payload(occurred_at):
            return {
                "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
                "recognized": {
                    "category": "meal", "amount": 30, "location": "广元", "item": "米粉",
                    "occurred_at": occurred_at,
                },
            }

        first = app.save_entry(meal_payload("2026-09-10T10:00:00"))
        second = app.save_entry(meal_payload("2026-09-10T11:00:00"))
        unchanged = app.update_entry(first["id"], {
            "trip_id": trip["id"], "raw_text": first["raw_text"], "recognized": {
                "category": "meal", "amount": 30, "location": "广元", "item": "米粉",
                "occurred_at": "2026-09-10T10:00:00",
            },
        })
        self.assertEqual(unchanged["id"], first["id"])

        with self.assertRaisesRegex(ValueError, "重复"):
            app.update_entry(first["id"], {
                "trip_id": trip["id"], "raw_text": first["raw_text"], "recognized": {
                    "category": "meal", "amount": 30, "location": "广元", "item": "米粉",
                    "occurred_at": second["occurred_at"],
                },
            })

    def test_delete_entry_removes_it_and_recalculates_dashboard(self):
        trip = app.create_trip({"name": "删除重算", "origin": "成都", "start_odometer": 10000})
        meal = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
            "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
        })
        lodging = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在兰州住宿200元",
            "recognized": {"category": "lodging", "amount": 200, "location": "兰州", "item": "住宿"},
        })
        self.assertEqual(app.dashboard(trip["id"])["cash_spend"], 230)

        app.delete_entry(meal["id"], {"trip_id": trip["id"]})

        report = app.dashboard(trip["id"])
        self.assertEqual([entry["id"] for entry in report["entries"]], [lodging["id"]])
        self.assertEqual(report["cash_spend"], 200)
        self.assertEqual(report["by_category"], {"住宿": 200})

    def test_update_and_delete_reject_unknown_entry_id(self):
        trip = app.create_trip({"name": "不存在记录", "origin": "成都", "start_odometer": 10000})
        with self.assertRaisesRegex(ValueError, "不存在|未找到"):
            app.update_entry(999999, {
                "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
                "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
            })
        with self.assertRaisesRegex(ValueError, "不存在|未找到"):
            app.delete_entry(999999, {"trip_id": trip["id"]})
        self.assertFalse(app.dashboard(trip["id"])["entries"])

    def test_update_and_delete_require_matching_active_trip(self):
        first_trip = app.create_trip({"name": "旧行程", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": first_trip["id"], "raw_text": "在广元吃米粉30元",
            "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
        })
        update = {
            "raw_text": "在兰州吃牛肉面45元",
            "recognized": {"category": "meal", "amount": 45, "location": "兰州", "item": "牛肉面"},
        }

        with self.assertRaisesRegex(ValueError, "trip_id|行程"):
            app.update_entry(entry["id"], update)
        with self.assertRaisesRegex(ValueError, "trip_id|行程"):
            app.delete_entry(entry["id"], {})
        app.finish_trip(first_trip["id"], {"end_odometer": 10100})
        second_trip = app.create_trip({"name": "新行程", "origin": "兰州", "start_odometer": 20000})

        with self.assertRaisesRegex(ValueError, "归属|行程"):
            app.update_entry(entry["id"], {**update, "trip_id": second_trip["id"]})
        with self.assertRaisesRegex(ValueError, "结束|进行中|开始"):
            app.update_entry(entry["id"], {**update, "trip_id": first_trip["id"]})
        with self.assertRaisesRegex(ValueError, "归属|行程"):
            app.delete_entry(entry["id"], {"trip_id": second_trip["id"]})
        with self.assertRaisesRegex(ValueError, "结束|进行中|开始"):
            app.delete_entry(entry["id"], {"trip_id": first_trip["id"]})

    def test_update_fuel_odometer_checks_neighbors_in_time_order(self):
        trip = app.create_trip({"name": "里程时序", "origin": "成都", "start_odometer": 10000})

        def add_fuel(occurred_at, odometer):
            return app.save_entry({
                "trip_id": trip["id"], "raw_text": f"加油100元，10升，当前里程{odometer}",
                "recognized": {
                    "category": "fuel", "amount": 100, "fuel_grade": 95, "fuel_liters": 10,
                    "odometer": odometer, "full_tank": True, "occurred_at": occurred_at,
                },
            })

        first = add_fuel("2026-09-10T10:00:00", 10100)
        middle = add_fuel("2026-09-10T11:00:00", 10200)
        latest = add_fuel("2026-09-10T12:00:00", 10300)

        def update_fuel(entry_id, occurred_at, odometer):
            return app.update_entry(entry_id, {
                "trip_id": trip["id"], "raw_text": f"加油100元，10升，当前里程{odometer}",
                "recognized": {
                    "category": "fuel", "amount": 100, "fuel_grade": 95, "fuel_liters": 10,
                    "odometer": odometer, "full_tank": True, "occurred_at": occurred_at,
                },
            })

        self.assertEqual(update_fuel(first["id"], "2026-09-10T10:00:00", 10050)["odometer"], 10050)
        self.assertEqual(update_fuel(middle["id"], "2026-09-10T11:00:00", 10250)["odometer"], 10250)
        self.assertEqual(update_fuel(latest["id"], "2026-09-10T12:00:00", 10400)["odometer"], 10400)
        with self.assertRaisesRegex(ValueError, "里程"):
            update_fuel(middle["id"], "2026-09-10T11:00:00", 10000)
        with self.assertRaisesRegex(ValueError, "里程"):
            update_fuel(middle["id"], "2026-09-10T11:00:00", 10500)
        with self.assertRaisesRegex(ValueError, "里程"):
            update_fuel(latest["id"], "2026-09-10T10:30:00", 10400)

    def test_update_fuel_to_nonfuel_clears_fuel_fields(self):
        trip = app.create_trip({"name": "分类变更", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"], "raw_text": "加98号汽油460元，45升，当前里程10300，没加满",
            "recognized": {
                "category": "fuel", "amount": 460, "fuel_grade": 98, "fuel_liters": 45,
                "fuel_unit_price": 10.222, "odometer": 10300, "full_tank": False,
            },
        })
        updated = app.update_entry(entry["id"], {
            "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
            "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
        })
        for field in ("fuel_grade", "fuel_liters", "fuel_unit_price", "odometer", "full_tank"):
            with self.subTest(field=field):
                self.assertIsNone(updated[field])
        self.assertEqual(app.dashboard(trip["id"])["by_category"], {"餐饮": 30})

    def test_false_full_tank_survives_update(self):
        trip = app.create_trip({"name": "未加满回填", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"], "raw_text": "加95号汽油300元，30升，当前里程10300，没加满",
            "recognized": {
                "category": "fuel", "amount": 300, "fuel_grade": 95, "fuel_liters": 30,
                "odometer": 10300, "full_tank": False,
            },
        })
        self.assertEqual(entry["full_tank"], 0)
        updated = app.update_entry(entry["id"], {
            "trip_id": trip["id"], "raw_text": entry["raw_text"],
            "recognized": {
                "category": "fuel", "amount": 320, "fuel_grade": 95, "fuel_liters": 32,
                "odometer": 10300, "full_tank": entry["full_tank"], "occurred_at": entry["occurred_at"],
            },
        })
        self.assertEqual(updated["full_tank"], 0)

    def test_excel_export_reflects_update_and_delete(self):
        trip = app.create_trip({"name": "导出变更", "origin": "成都", "start_odometer": 10000})
        meal = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
            "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
        })
        lodging = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在兰州住青旅床位200元",
            "recognized": {"category": "lodging", "amount": 200, "location": "兰州", "item": "青旅床位"},
        })
        app.update_entry(meal["id"], {
            "trip_id": trip["id"], "raw_text": "在天水吃牛肉面45元",
            "recognized": {"category": "meal", "amount": 45, "location": "天水", "item": "牛肉面"},
        })
        app.delete_entry(lodging["id"], {"trip_id": trip["id"]})

        with zipfile.ZipFile(BytesIO(app.export_workbook())) as workbook:
            detail = workbook.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("天水", detail)
        self.assertIn("牛肉面", detail)
        self.assertNotIn("广元", detail)
        self.assertNotIn("米粉", detail)
        self.assertNotIn("兰州", detail)
        self.assertNotIn("青旅床位", detail)

    def test_update_rejects_refund_and_deposit_text(self):
        trip = app.create_trip({"name": "更新流水校验", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在广元住宿300元",
            "recognized": {"category": "lodging", "amount": 300, "location": "广元", "item": "住宿"},
        })
        for text_value in ("酒店退款300元", "酒店押金500元"):
            with self.subTest(text=text_value):
                with self.assertRaisesRegex(ValueError, "退款|押金"):
                    app.update_entry(entry["id"], {
                        "trip_id": trip["id"], "raw_text": text_value,
                        "recognized": {"category": "lodging", "amount": 300, "location": "广元", "item": "住宿"},
                    })

    def test_update_trip_start_odometer_recalculates_distance_and_fuel_metrics(self):
        trip = app.create_trip({"name": "修改起始里程", "origin": "成都", "start_odometer": 10000})
        app.save_entry({
            "trip_id": trip["id"], "raw_text": "加95号汽油500元，50升，当前里程10500",
            "recognized": {
                "category": "fuel", "amount": 500, "fuel_grade": 95, "fuel_liters": 50,
                "odometer": 10500, "full_tank": True,
            },
        })
        before = app.dashboard(trip["id"])
        self.assertEqual(before["distance_km"], 500)
        self.assertEqual(before["fuel"]["l_per_100km"], 10)
        self.assertEqual(before["vehicle_cost_per_km"], 1)

        updated = app.update_trip_start_odometer(trip["id"], {"start_odometer": 9500})

        self.assertEqual(updated["start_odometer"], 9500)
        report = app.dashboard(trip["id"])
        self.assertEqual(report["distance_km"], 1000)
        self.assertEqual(report["fuel"]["l_per_100km"], 5)
        self.assertEqual(report["fuel"]["cost_per_km"], 0.5)
        self.assertEqual(report["vehicle_cost"], 500)
        self.assertEqual(report["vehicle_cost_per_km"], 0.5)

    def test_update_trip_start_odometer_rejects_non_finite_and_invalid_values(self):
        trip = app.create_trip({"name": "起始里程校验", "origin": "成都", "start_odometer": 10000})
        cases = (
            ("negative", -1),
            ("not-a-number", "一万公里"),
            ("nan-number", float("nan")),
            ("nan-text", "NaN"),
            ("positive-infinity", float("inf")),
            ("negative-infinity", float("-inf")),
            ("infinity-text", "Infinity"),
        )
        for name, value in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "里程|数字|有限"):
                    app.update_trip_start_odometer(trip["id"], {"start_odometer": value})
        self.assertEqual(app.dashboard(trip["id"])["trip"]["start_odometer"], 10000)

    def test_update_trip_start_odometer_cannot_exceed_any_recorded_odometer(self):
        trip = app.create_trip({"name": "起始里程上界", "origin": "成都", "start_odometer": 10000})
        for occurred_at, odometer in (("2026-09-10T10:00:00", 10300), ("2026-09-10T11:00:00", 10500)):
            app.save_entry({
                "trip_id": trip["id"], "raw_text": f"加油300元，30升，当前里程{odometer}",
                "recognized": {
                    "category": "fuel", "amount": 300, "fuel_grade": 95, "fuel_liters": 30,
                    "odometer": odometer, "full_tank": True, "occurred_at": occurred_at,
                },
            })

        with self.assertRaisesRegex(ValueError, "里程"):
            app.update_trip_start_odometer(trip["id"], {"start_odometer": 10400})
        self.assertEqual(app.dashboard(trip["id"])["trip"]["start_odometer"], 10000)

    def test_update_trip_start_odometer_requires_active_existing_trip(self):
        trip = app.create_trip({"name": "行程状态校验", "origin": "成都", "start_odometer": 10000})
        app.finish_trip(trip["id"], {"end_odometer": 10100})
        with self.assertRaisesRegex(ValueError, "结束|进行中|开始"):
            app.update_trip_start_odometer(trip["id"], {"start_odometer": 9900})
        with self.assertRaisesRegex(ValueError, "不存在|未找到"):
            app.update_trip_start_odometer(999999, {"start_odometer": 9900})

    def test_update_trip_title_and_start_odometer_keeps_legacy_put_compatible(self):
        trip = app.create_trip({"name": "旧标题", "origin": "成都", "start_odometer": 10000})
        renamed = app.update_trip_start_odometer(trip["id"], {"name": "  青甘大环线  "})
        self.assertEqual(renamed["name"], "青甘大环线")
        self.assertEqual(renamed["start_odometer"], 10000)

        legacy = app.update_trip_start_odometer(trip["id"], {"start_odometer": 9900})
        self.assertEqual(legacy["name"], "青甘大环线")
        self.assertEqual(legacy["start_odometer"], 9900)

        together = app.update_trip_start_odometer(
            trip["id"], {"name": "  西北自驾  ", "start_odometer": 9800})
        self.assertEqual((together["name"], together["start_odometer"]), ("西北自驾", 9800))
        with self.assertRaisesRegex(ValueError, "标题必填"):
            app.update_trip_start_odometer(trip["id"], {"name": "   "})
        app.finish_trip(trip["id"], {"end_odometer": 10000})
        with self.assertRaisesRegex(ValueError, "进行中"):
            app.update_trip_start_odometer(trip["id"], {"name": "不允许修改"})

    def test_put_trip_route_does_not_conflict_with_entry_route(self):
        trip = app.create_trip({"name": "PUT路由", "origin": "成都", "start_odometer": 10000})
        entry = app.save_entry({
            "trip_id": trip["id"], "raw_text": "在广元吃米粉30元",
            "recognized": {"category": "meal", "amount": 30, "location": "广元", "item": "米粉"},
        })

        def put(path, payload):
            responses = []
            handler = app.Handler.__new__(app.Handler)
            handler.path = path
            handler.body = lambda: payload
            handler.json_response = lambda body, status=200: responses.append((body, int(status)))
            handler.do_PUT()
            return responses[0]

        trip_response, trip_status = put(
            f"/api/trips/{trip['id']}", {"start_odometer": 9900}
        )
        self.assertEqual(trip_status, 200)
        self.assertEqual(trip_response["start_odometer"], 9900)
        self.assertEqual(app.dashboard(trip["id"])["entries"][0]["amount"], 30)

        entry_response, entry_status = put(f"/api/entries/{entry['id']}", {
            "trip_id": trip["id"], "raw_text": "在广元吃米粉45元",
            "recognized": {"category": "meal", "amount": 45, "location": "广元", "item": "米粉"},
        })
        self.assertEqual(entry_status, 200)
        self.assertEqual(entry_response["amount"], 45)
        report = app.dashboard(trip["id"])
        self.assertEqual(report["trip"]["start_odometer"], 9900)
        self.assertEqual(report["entries"][0]["amount"], 45)

    def test_audit_amount_multiple_entries_and_purchase_categories(self):
        self.assertEqual(app.parse_text("在兰州吃饭花了1,200元")["recognized"]["amount"], 1200)
        self.assertEqual(app.parse_text("在兰州吃饭花了1，200元")["recognized"]["amount"], 1200)
        multi = app.parse_text("在兰州午餐30元，晚餐50元")
        self.assertIn("multiple_entries", {gap["field"] for gap in multi["missing"]})
        no_unit = app.parse_text("午餐花费30，晚餐花费50")
        self.assertIn("multiple_entries", {gap["field"] for gap in no_unit["missing"]})
        mixed = app.parse_text("加油300元，午餐30元")
        self.assertIn("multiple_entries", {gap["field"] for gap in mixed["missing"]})
        for phrase in ("加95号汽油300元40升，咖啡30元", "加95号汽油300元40升，酒店200元", "加95号汽油300元40升，水果30元"):
            self.assertIn("multiple_entries", {gap["field"] for gap in app.parse_text(phrase)["missing"]})
        self.assertNotIn("multiple_entries", {gap["field"] for gap in app.parse_text("午餐30元，合计30元")["missing"]})
        trip = app.create_trip({"name": "多笔拒绝", "origin": "成都", "start_odometer": 1000})
        with self.assertRaisesRegex(ValueError, "多笔消费"):
            app.save_entry({"trip_id": trip["id"], "raw_text": multi["raw_text"],
                            "recognized": {"category": "meal", "amount": 50}})
        cases = {
            "买汽油添加剂80元": "vehicle", "今天没加油，在兰州吃饭30元": "meal",
            "买防晒衣100元": "clothing", "买一瓶啤酒12元": "daily",
        }
        for text, category in cases.items():
            self.assertEqual(app.parse_text(text)["recognized"]["category"], category)

    def test_audit_non_finite_trip_values_and_stale_fuel_odometer(self):
        for value in ("nan", "inf", float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                app.create_trip({"name": "非法", "origin": "成都", "start_odometer": value})
        with self.assertRaises(ValueError):
            app.create_trip({"name": "布尔", "origin": "成都", "start_odometer": True})
        trip = app.create_trip({"name": "过期里程", "origin": "成都", "start_odometer": 1000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油10升100元",
                        "recognized": {"category": "fuel", "amount": 100, "fuel_liters": 10, "odometer": 1100}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油20升200元",
                        "recognized": {"category": "fuel", "amount": 200, "fuel_liters": 20}})
        fuel = app.dashboard(trip["id"])["fuel"]
        report = app.dashboard(trip["id"])
        self.assertEqual(fuel["status"], "insufficient_data")
        self.assertIsNone(fuel["l_per_100km"])
        self.assertEqual(report["vehicle_cost"], 300)
        self.assertEqual(report["vehicle_cost_per_km"], 3.0)
        self.assertEqual(report["core_cost_per_km"], 3.0)
        with self.assertRaises(ValueError):
            app.finish_trip(trip["id"], {"end_odometer": "nan"})

    def test_write_request_requires_json_and_same_origin(self):
        handler = app.Handler.__new__(app.Handler)
        headers = Message(); headers["Host"] = "ledger.example.ts.net"; headers["Content-Type"] = "text/plain"; headers["Origin"] = "https://evil.example"
        handler.headers = headers
        with self.assertRaisesRegex(ValueError, "application/json"):
            handler.validate_write_request()
        headers.replace_header("Content-Type", "application/json")
        with self.assertRaisesRegex(ValueError, "来源"):
            handler.validate_write_request()
        headers.replace_header("Origin", "https://ledger.example.ts.net")
        handler.validate_write_request()

    def test_v26_migration_is_repeatable_for_legacy_entries(self):
        if app.DB_PATH.exists():
            app.DB_PATH.unlink()
        with sqlite3.connect(app.DB_PATH) as db:
            db.executescript("""CREATE TABLE entries (id INTEGER PRIMARY KEY, trip_id INTEGER, occurred_at TEXT,
                category TEXT, category_label TEXT, amount REAL, location TEXT, note TEXT, raw_text TEXT,
                fuel_grade INTEGER, fuel_liters REAL, fuel_unit_price REAL, odometer REAL, full_tank INTEGER,
                people INTEGER, nights INTEGER, created_at TEXT);""")
        app.init_db(); app.init_db()
        with app.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(entries)")}
            self.assertTrue({"updated_at", "version", "client_revision", "deleted_at", "delete_expires_at"} <= columns)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='odometer_readings'").fetchone())
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='purged_records'").fetchone())

    def test_v26_soft_delete_restore_revisions_and_client_revision(self):
        trip = app.create_trip({"name": "回收站", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"], "client_id": "phone-e1", "client_revision": 1,
            "raw_text": "在兰州吃面30元", "recognized": {"category": "meal", "amount": 30, "location": "兰州", "item": "牛肉面"}})
        replay = app.save_entry({"trip_id": trip["id"], "client_id": "phone-e1", "client_revision": 1,
            "raw_text": "在兰州吃面30元", "recognized": {"category": "meal", "amount": 30}})
        self.assertTrue(replay["validation"]["idempotent"])
        updated = app.update_entry(entry["id"], {"trip_id": trip["id"], "client_revision": 2,
            "client_id": "phone-e1",
            "raw_text": "在兰州吃面35元", "recognized": {"category": "meal", "amount": 35, "location": "兰州", "item": "牛肉面"}})
        self.assertEqual(updated["version"], 2)
        deleted = app.delete_entry(entry["id"], {"trip_id": trip["id"], "client_id": "phone-e1", "client_revision": 3})
        self.assertTrue(deleted["delete_expires_at"])
        self.assertEqual(app.dashboard(trip["id"])["total_spend"], 0)
        self.assertEqual(len(app.trash(trip["id"])["entries"]), 1)
        restored = app.restore_record("entry", entry["id"], {"trip_id": trip["id"], "client_id": "phone-e1", "client_revision": 4})
        self.assertIsNone(restored["deleted_at"])
        self.assertEqual(app.dashboard(trip["id"])["total_spend"], 35)
        self.assertEqual([r["action"] for r in app.record_revisions("entry", entry["id"])], ["create", "update", "delete", "restore"])
        with self.assertRaisesRegex(ValueError, "过期"):
            app.update_entry(entry["id"], {"trip_id": trip["id"], "client_revision": 2,
                "client_id": "phone-e1",
                "raw_text": "在兰州吃面40元", "recognized": {"category": "meal", "amount": 40}})

    def test_v27_permanently_delete_one_trash_record_and_its_revisions(self):
        trip = app.create_trip({"name": "单项清理", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"], "client_id": "purge-entry", "client_revision": 1, "raw_text": "停车20元",
            "recognized": {"category": "parking", "amount": 20}})
        other = app.save_entry({"trip_id": trip["id"], "raw_text": "午餐30元",
            "recognized": {"category": "meal", "amount": 30}})
        reading = app.save_odometer_reading({"trip_id": trip["id"], "client_id": "purge-odo", "client_revision": 1, "odometer": 1100})
        app.delete_entry(entry["id"], {"trip_id": trip["id"], "client_id": "purge-entry"})
        app.delete_odometer_reading(reading["id"], {"trip_id": trip["id"], "client_id": "purge-odo"})

        deleted = app.permanently_delete_record("entry", entry["id"], {"trip_id": trip["id"]})
        self.assertTrue(deleted["permanently_deleted"])
        self.assertGreaterEqual(deleted["revisions_deleted"], 2)
        self.assertEqual(deleted["record_type"], "entry")
        with app.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM entries WHERE id=?", (entry["id"],)).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM record_revisions WHERE record_type='entry' AND record_id=?", (entry["id"],)).fetchone())
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM purged_records WHERE record_type='entry' AND record_id=? AND client_id='purge-entry'",
                (entry["id"],)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM entries WHERE id=?", (other["id"],)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM odometer_readings WHERE id=?", (reading["id"],)).fetchone())

        odo_deleted = app.permanently_delete_record(
            "odometer_reading", reading["id"], {"trip_id": trip["id"], "client_id": "purge-odo"})
        self.assertTrue(odo_deleted["permanently_deleted"])
        self.assertEqual(odo_deleted["record_type"], "odometer_reading")
        odo_replay = app.permanently_delete_record(
            "odometer_reading", reading["id"], {"trip_id": trip["id"], "client_id": "purge-odo"})
        self.assertTrue(odo_replay["idempotent"])
        with app.connect() as db:
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM purged_records WHERE record_type='odometer_reading' AND record_id=? AND client_id='purge-odo'",
                (reading["id"],)).fetchone())
        with self.assertRaises(app.RecordGoneError):
            app.save_odometer_reading({"trip_id": trip["id"], "client_id": "purge-odo",
                "client_revision": 2, "odometer": 1200})
        odo_delete_replay = app.sync_record({"entity": "odometer", "op": "delete",
            "client_id": "purge-odo", "client_revision": 3,
            "payload": {"trip_id": trip["id"], "id": reading["id"]}})
        self.assertTrue(odo_delete_replay["tombstone"])
        with self.assertRaises(app.RecordGoneError):
            app.sync_record({"entity": "odometer", "op": "restore",
                "client_id": "purge-odo", "client_revision": 4,
                "payload": {"trip_id": trip["id"], "id": reading["id"]}})

    def test_v27_purge_tombstone_blocks_replay_but_not_other_entity_or_reused_id(self):
        trip = app.create_trip({"name": "防重放", "origin": "成都", "start_odometer": 1000})
        original_payload = {"trip_id": trip["id"], "client_id": "shared-client", "client_revision": 1,
            "raw_text": "停车20元", "recognized": {"category": "parking", "amount": 20}}
        entry = app.save_entry(original_payload)
        # 同一 client_id 可在另一实体中使用，entry 墓碑不得误伤 odometer。
        reading = app.save_odometer_reading({"trip_id": trip["id"], "client_id": "shared-client",
            "client_revision": 1, "odometer": 1100})
        app.delete_entry(entry["id"], {"trip_id": trip["id"], "client_id": "shared-client", "client_revision": 2})
        app.permanently_delete_record("entry", entry["id"], {
            "trip_id": trip["id"], "client_id": "shared-client", "client_revision": 2})

        with self.assertRaises(app.RecordGoneError):
            app.save_entry({**original_payload, "client_revision": 99})
        replay_delete = app.sync_record({"entity": "entry", "op": "delete",
            "client_id": "shared-client", "client_revision": 99,
            "payload": {"trip_id": trip["id"], "id": entry["id"]}})
        self.assertTrue(replay_delete["tombstone"])
        self.assertTrue(replay_delete["purged"])
        self.assertEqual(replay_delete["client_revision"], 99)
        for op in ("upsert", "restore"):
            with self.subTest(op=op), self.assertRaises(app.RecordGoneError):
                app.sync_record({"entity": "entry", "op": op,
                    "client_id": "shared-client", "client_revision": 100,
                    "payload": {**original_payload, "id": entry["id"]}})
        updated_reading = app.update_odometer_reading(reading["id"], {
            "trip_id": trip["id"], "client_id": "shared-client", "client_revision": 2,
            "odometer": 1120})
        self.assertEqual(updated_reading["odometer"], 1120)

        # 新 id 必须越过表和墓碑历史最大值，不复用已永久删除的 id。
        replacement = app.save_entry({"trip_id": trip["id"], "client_id": "replacement-client",
            "client_revision": 1, "raw_text": "午餐30元",
            "recognized": {"category": "meal", "amount": 30}})
        self.assertGreater(replacement["id"], entry["id"])
        changed = app.sync_record({"entity": "entry", "op": "upsert",
            "client_id": "replacement-client", "client_revision": 2,
            "payload": {"trip_id": trip["id"], "id": replacement["id"],
                "raw_text": "午餐35元", "recognized": {"category": "meal", "amount": 35}}})
        self.assertEqual(changed["record"]["amount"], 35)

    def test_v27_client_purge_closes_delayed_upsert_race_for_both_entities(self):
        trip = app.create_trip({"name": "延迟上传", "origin": "成都", "start_odometer": 1000})
        for entity, client_id, payload in (
            ("entry", "late-entry", {"trip_id": trip["id"], "raw_text": "停车20元",
                "recognized": {"category": "parking", "amount": 20}}),
            ("odometer", "late-odo", {"trip_id": trip["id"], "odometer": 1100}),
        ):
            with self.subTest(entity=entity):
                purged = app.permanently_purge_client({"entity": entity, "trip_id": trip["id"],
                    "client_id": client_id, "client_revision": 2})
                self.assertTrue(purged["permanently_deleted"])
                self.assertIsNone(purged["record_id"])
                with self.assertRaises(app.RecordGoneError):
                    app.sync_record({"entity": entity, "op": "upsert", "client_id": client_id,
                        "client_revision": 3, "payload": payload})
                replay = app.sync_record({"entity": entity, "op": "delete", "client_id": client_id,
                    "client_revision": 4, "payload": {"trip_id": trip["id"]}})
                self.assertTrue(replay["tombstone"])
                self.assertIsNone(replay["id"])

        # 另一种时序：延迟 upsert 先落库，永久删除随后到达时也必须删实体和历史。
        arrived = app.save_entry({"trip_id": trip["id"], "client_id": "arrived-first",
            "client_revision": 1, "raw_text": "午餐30元",
            "recognized": {"category": "meal", "amount": 30}})
        with self.assertRaises(app.RecordConflictError):
            app.permanently_purge_client({"entity": "entry", "trip_id": trip["id"],
                "client_id": "arrived-first", "client_revision": 2})
        app.delete_entry(arrived["id"], {"trip_id": trip["id"],
            "client_id": "arrived-first", "client_revision": 2})
        removed = app.permanently_purge_client({"entity": "entry", "trip_id": trip["id"],
            "client_id": "arrived-first", "client_revision": 3})
        self.assertEqual(removed["record_id"], arrived["id"])
        self.assertGreaterEqual(removed["revisions_deleted"], 1)
        with app.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM entries WHERE id=?", (arrived["id"],)).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM record_revisions WHERE record_type='entry' AND record_id=?",
                (arrived["id"],)).fetchone())

        again = app.permanently_purge_client({"entity": "entry", "trip_id": trip["id"],
            "client_id": "arrived-first", "client_revision": 5})
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["client_revision"], 5)

    def test_v27_client_purge_http_rejects_active_record_with_retry_identity(self):
        trip = app.create_trip({"name": "先软删除", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"], "client_id": "active-purge",
            "client_revision": 3, "raw_text": "停车20元",
            "recognized": {"category": "parking", "amount": 20}})
        responses = []
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/trash/purge-client"
        handler.body = lambda: {"entity": "entry", "trip_id": trip["id"],
            "client_id": "active-purge", "client_revision": 4}
        handler.json_response = lambda body, status=200: responses.append((body, int(status)))
        handler.do_POST()
        body, status = responses[0]
        self.assertEqual(status, 409)
        self.assertTrue(body["conflict"])
        self.assertEqual(body["record_id"], entry["id"])
        self.assertEqual(body["client_id"], "active-purge")
        self.assertEqual(body["client_revision"], 3)
        with app.connect() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM entries WHERE id=? AND deleted_at IS NULL",
                                            (entry["id"],)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM purged_records WHERE client_id='active-purge'").fetchone())

    def test_v27_stale_ordinary_requests_cannot_touch_reused_id(self):
        trip = app.create_trip({"name": "旧ID保护", "origin": "成都", "start_odometer": 1000})
        old = app.save_entry({"trip_id": trip["id"], "client_id": "old-page",
            "client_revision": 1, "raw_text": "停车20元",
            "recognized": {"category": "parking", "amount": 20}})
        app.delete_entry(old["id"], {"trip_id": trip["id"], "client_id": "old-page", "client_revision": 2})
        app.permanently_delete_record("entry", old["id"], {
            "trip_id": trip["id"], "client_id": "old-page", "client_revision": 2})
        replacement = app.save_entry({"trip_id": trip["id"], "client_id": "new-page",
            "client_revision": 1, "raw_text": "午餐30元",
            "recognized": {"category": "meal", "amount": 30}})
        self.assertGreater(replacement["id"], old["id"])
        # 新分配器不会复用；手工模拟历史数据库已经复用 id，验证普通接口仍以 client_id 防误伤。
        with app.connect() as db:
            db.execute("UPDATE entries SET id=? WHERE id=?", (old["id"], replacement["id"]))
        stale_update = {"trip_id": trip["id"], "client_id": "old-page", "client_revision": 3,
            "raw_text": "误改99元", "recognized": {"category": "meal", "amount": 99}}
        with self.assertRaises(app.RecordConflictError):
            app.update_entry(old["id"], stale_update)
        with self.assertRaises(app.RecordConflictError):
            app.delete_entry(old["id"], {"trip_id": trip["id"], "client_id": "old-page", "client_revision": 3})
        app.delete_entry(old["id"], {"trip_id": trip["id"], "client_id": "new-page", "client_revision": 2})
        with self.assertRaises(app.RecordConflictError):
            app.restore_record("entry", old["id"], {
                "trip_id": trip["id"], "client_id": "old-page", "client_revision": 4})
        with app.connect() as db:
            row = db.execute("SELECT * FROM entries WHERE id=?", (old["id"],)).fetchone()
        self.assertEqual(row["amount"], 30)
        self.assertEqual(row["client_id"], "new-page")
        self.assertIsNotNone(row["deleted_at"])

        old_reading = app.save_odometer_reading({"trip_id": trip["id"], "client_id": "old-odo-page",
            "client_revision": 1, "odometer": 1100})
        app.delete_odometer_reading(old_reading["id"], {
            "trip_id": trip["id"], "client_id": "old-odo-page", "client_revision": 2})
        app.permanently_delete_record("odometer_reading", old_reading["id"], {
            "trip_id": trip["id"], "client_id": "old-odo-page", "client_revision": 2})
        replacement_reading = app.save_odometer_reading({"trip_id": trip["id"],
            "client_id": "new-odo-page", "client_revision": 1, "odometer": 1200})
        self.assertGreater(replacement_reading["id"], old_reading["id"])
        with app.connect() as db:
            db.execute("UPDATE odometer_readings SET id=? WHERE id=?",
                       (old_reading["id"], replacement_reading["id"]))
        with self.assertRaises(app.RecordConflictError):
            app.update_odometer_reading(old_reading["id"], {
                "trip_id": trip["id"], "client_id": "old-odo-page",
                "client_revision": 3, "odometer": 1250})
        with self.assertRaises(app.RecordConflictError):
            app.delete_odometer_reading(old_reading["id"], {
                "trip_id": trip["id"], "client_id": "old-odo-page", "client_revision": 3})
        app.delete_odometer_reading(old_reading["id"], {
            "trip_id": trip["id"], "client_id": "new-odo-page", "client_revision": 2})
        with self.assertRaises(app.RecordConflictError):
            app.restore_record("odometer_reading", old_reading["id"], {
                "trip_id": trip["id"], "client_id": "old-odo-page", "client_revision": 4})
        with app.connect() as db:
            reading_row = db.execute(
                "SELECT * FROM odometer_readings WHERE id=?", (old_reading["id"],)).fetchone()
        self.assertEqual(reading_row["odometer"], 1200)
        self.assertEqual(reading_row["client_id"], "new-odo-page")
        self.assertIsNotNone(reading_row["deleted_at"])

    def test_v27_save_and_permanent_tombstone_are_serialized(self):
        trip = app.create_trip({"name": "原子防复活", "origin": "成都", "start_odometer": 1000})
        for entity, client_id, payload, table, saver in (
            ("entry", "atomic-entry", {"trip_id": trip["id"], "client_id": "atomic-entry",
                "client_revision": 1, "raw_text": "停车20元",
                "recognized": {"category": "parking", "amount": 20}}, "entries", app.save_entry),
            ("odometer", "atomic-odo", {"trip_id": trip["id"], "client_id": "atomic-odo",
                "client_revision": 1, "odometer": 1100}, "odometer_readings", app.save_odometer_reading),
        ):
            with self.subTest(entity=entity):
                gate = sqlite3.connect(app.DB_PATH)
                gate.execute("BEGIN IMMEDIATE")
                outcome = {}
                def delayed_save():
                    try:
                        outcome["record"] = saver(payload)
                    except Exception as exc:  # 线程内将异常传回主测试。
                        outcome["error"] = exc
                worker = threading.Thread(target=delayed_save)
                worker.start()
                time.sleep(0.05)
                gate.execute("""INSERT INTO purged_records
                    (record_type,record_id,trip_id,client_id,client_revision,purged_at)
                    VALUES (?,?,?,?,?,?)""", (
                        "entry" if entity == "entry" else "odometer_reading", None,
                        trip["id"], client_id, 2, app.now_text()))
                gate.commit(); gate.close()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(outcome.get("error"), app.RecordGoneError)
                with app.connect() as db:
                    self.assertIsNone(db.execute(f"SELECT 1 FROM {table} WHERE client_id=?", (client_id,)).fetchone())

    def test_v27_direct_post_of_purged_client_id_returns_410(self):
        trip = app.create_trip({"name": "POST防重放", "origin": "成都", "start_odometer": 1000})
        payload = {"trip_id": trip["id"], "client_id": "post-purged", "client_revision": 1,
            "raw_text": "停车20元", "recognized": {"category": "parking", "amount": 20}}
        entry = app.save_entry(payload)
        app.delete_entry(entry["id"], {"trip_id": trip["id"], "client_id": "post-purged", "client_revision": 2})
        app.permanently_delete_record("entry", entry["id"], {
            "trip_id": trip["id"], "client_id": "post-purged", "client_revision": 2})
        responses = []
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/entries"
        handler.body = lambda: {**payload, "client_revision": 3}
        handler.json_response = lambda body, status=200: responses.append((body, int(status)))
        handler.do_POST()
        body, status = responses[0]
        self.assertEqual(status, 410)
        self.assertTrue(body["gone"])

        reading_payload = {"trip_id": trip["id"], "client_id": "post-purged-odo",
            "client_revision": 1, "odometer": 1100}
        reading = app.save_odometer_reading(reading_payload)
        app.delete_odometer_reading(reading["id"], {"trip_id": trip["id"], "client_id": "post-purged-odo", "client_revision": 2})
        app.permanently_delete_record("odometer_reading", reading["id"], {
            "trip_id": trip["id"], "client_id": "post-purged-odo", "client_revision": 2})
        responses.clear()
        handler.path = "/api/odometer-readings"
        handler.body = lambda: {**reading_payload, "client_revision": 3}
        handler.do_POST()
        body, status = responses[0]
        self.assertEqual(status, 410)
        self.assertTrue(body["gone"])

    def test_v27_permanent_delete_requires_matching_trip_and_trash_state(self):
        first = app.create_trip({"name": "第一段", "origin": "成都", "start_odometer": 1000})
        active = app.save_entry({"trip_id": first["id"], "raw_text": "停车20元",
            "recognized": {"category": "parking", "amount": 20}})
        with self.assertRaisesRegex(ValueError, "回收站"):
            app.permanently_delete_record("entry", active["id"], {"trip_id": first["id"]})
        app.delete_entry(active["id"], {"trip_id": first["id"]})
        with self.assertRaisesRegex(ValueError, "不属于"):
            app.permanently_delete_record("entry", active["id"], {"trip_id": first["id"] + 1})
        self.assertEqual(len(app.trash(first["id"])["records"]), 1)

    def test_v27_permanent_delete_http_route_is_distinct_from_soft_delete(self):
        trip = app.create_trip({"name": "回收站路由", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"], "raw_text": "停车20元",
            "recognized": {"category": "parking", "amount": 20}})
        app.delete_entry(entry["id"], {"trip_id": trip["id"]})
        responses = []
        handler = app.Handler.__new__(app.Handler)
        handler.path = f"/api/trash/entries/{entry['id']}"
        handler.body = lambda: {"trip_id": trip["id"]}
        handler.json_response = lambda body, status=200: responses.append((body, int(status)))
        handler.do_DELETE()
        body, status = responses[0]
        self.assertEqual(status, 200)
        self.assertTrue(body["permanently_deleted"])
        with app.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM entries WHERE id=?", (entry["id"],)).fetchone())

    def test_v27_client_purge_http_route_persists_tombstone_without_record_id(self):
        trip = app.create_trip({"name": "无ID墓碑路由", "origin": "成都", "start_odometer": 1000})
        responses = []
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/trash/purge-client"
        handler.body = lambda: {"entity": "entry", "trip_id": trip["id"],
            "client_id": "route-no-id", "client_revision": 2}
        handler.json_response = lambda body, status=200: responses.append((body, int(status)))
        handler.do_POST()
        body, status = responses[0]
        self.assertEqual(status, 200)
        self.assertTrue(body["permanently_deleted"])
        self.assertIsNone(body["record_id"])
        with app.connect() as db:
            tombstone = db.execute(
                "SELECT * FROM purged_records WHERE record_type='entry' AND client_id='route-no-id'").fetchone()
        self.assertIsNotNone(tombstone)
        self.assertEqual(tombstone["trip_id"], trip["id"])

    def test_v26_manual_odometer_is_shared_dashboard_boundary(self):
        trip = app.create_trip({"name": "手工里程", "origin": "成都", "start_odometer": 1000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油100元10升",
                        "recognized": {"category": "fuel", "amount": 100, "fuel_liters": 10, "odometer": 1100}})
        reading = app.save_odometer_reading({"trip_id": trip["id"], "client_id": "odo-1", "client_revision": 1,
                                             "occurred_at": "2026-09-12T20:00:00", "odometer": 1250, "note": "停车后"})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["current_odometer"], 1250)
        self.assertEqual(report["current_odometer_source"], "manual")
        self.assertEqual(report["distance_km"], 250)
        with self.assertRaisesRegex(ValueError, "不能小于"):
            app.update_odometer_reading(reading["id"], {"trip_id": trip["id"], "client_revision": 2,
                                                          "client_id": "odo-1",
                                                          "occurred_at": "2026-09-12T20:00:00", "odometer": 900})

    def test_v26_parse_records_field_meta_and_five_excel_sheets(self):
        parsed = app.parse_text("在广元午餐30元，晚餐50元")
        self.assertEqual(len(parsed["records"]), 2)
        self.assertTrue(parsed["can_save"])
        self.assertEqual(parsed["records"][1]["recognized"]["location"], "广元")
        self.assertEqual(parsed["records"][0]["field_meta"]["amount"]["state"], "certain")
        fuel = app.parse_text("加油500元40升")
        self.assertEqual(fuel["field_meta"]["fuel_grade"]["state"], "default")
        trip = app.create_trip({"name": "五表", "origin": "成都", "start_odometer": 1000})
        app.save_odometer_reading({"trip_id": trip["id"], "occurred_at": "2026-09-12T08:00:00", "odometer": 1001})
        with zipfile.ZipFile(BytesIO(app.export_workbook())) as book:
            workbook = book.read("xl/workbook.xml").decode()
        for name in ("行程汇总", "消费明细", "里程记录", "回收站", "修改历史"):
            self.assertIn(name, workbook)

    def test_v26_sync_delete_and_restore_are_idempotent(self):
        trip = app.create_trip({"name": "同步", "origin": "成都", "start_odometer": 1000})
        created = app.sync_record({"entity": "entry", "op": "upsert", "client_id": "sync-e1", "client_revision": 1,
            "payload": {"trip_id": trip["id"], "raw_text": "停车20元", "recognized": {"category": "parking", "amount": 20}}})
        self.assertEqual(created["status"], "created")
        replay = app.sync_record({"entity": "entry", "op": "upsert", "client_id": "sync-e1", "client_revision": 1,
            "payload": {"trip_id": trip["id"], "raw_text": "停车20元", "recognized": {"category": "parking", "amount": 20}}})
        self.assertEqual(replay["status"], "idempotent")
        deleted = app.sync_record({"entity": "entry", "op": "delete", "client_id": "sync-e1", "client_revision": 2,
                                    "payload": {"trip_id": trip["id"]}})
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual(app.sync_record({"entity": "entry", "op": "delete", "client_id": "sync-e1", "client_revision": 2,
                                          "payload": {"trip_id": trip["id"]}})["status"], "idempotent")
        self.assertEqual(app.sync_record({"entity": "entry", "op": "restore", "client_id": "sync-e1", "client_revision": 3,
                                          "payload": {"trip_id": trip["id"]}})["status"], "restored")

    def test_v26_sync_upsert_restores_and_applies_new_content(self):
        trip = app.create_trip({"name": "丢响应恢复", "origin": "成都", "start_odometer": 1000})
        created = app.sync_record({"entity": "entry", "op": "upsert", "client_id": "lost-response",
            "client_revision": 1, "payload": {"trip_id": trip["id"], "raw_text": "午餐30元",
            "recognized": {"category": "meal", "amount": 30, "item": "米粉"}}})
        app.sync_record({"entity": "entry", "op": "delete", "client_id": "lost-response",
            "client_revision": 2, "payload": {"trip_id": trip["id"], "id": created["id"]}})

        restored = app.sync_record({"entity": "entry", "op": "upsert", "client_id": "lost-response",
            "client_revision": 3, "payload": {"trip_id": trip["id"], "id": created["id"],
            "raw_text": "午餐35元", "recognized": {"category": "meal", "amount": 35, "item": "米粉"}}})
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(restored["record"]["amount"], 35)
        self.assertEqual(restored["record"]["client_revision"], 3)
        self.assertIsNone(restored["record"]["deleted_at"])
        self.assertEqual(app.dashboard(trip["id"])["total_spend"], 35)

        replay = app.sync_record({"entity": "entry", "op": "upsert", "client_id": "lost-response",
            "client_revision": 3, "payload": {"trip_id": trip["id"], "id": created["id"],
            "raw_text": "午餐35元", "recognized": {"category": "meal", "amount": 35, "item": "米粉"}}})
        self.assertEqual(replay["status"], "idempotent")
        self.assertEqual(replay["record"]["amount"], 35)

    def test_v26_sync_can_delete_and_restore_legacy_record_by_id_and_trip(self):
        first = app.create_trip({"name": "旧账", "origin": "成都", "start_odometer": 1000})
        legacy = app.save_entry({"trip_id": first["id"], "raw_text": "停车20元",
                                 "recognized": {"category": "parking", "amount": 20}})
        deleted = app.sync_record({"entity": "entry", "op": "delete", "id": legacy["id"],
                                    "trip_id": first["id"], "client_revision": 2, "payload": {}})
        self.assertEqual(deleted["status"], "deleted")
        restored = app.sync_record({"entity": "entry", "op": "restore", "id": legacy["id"],
                                     "trip_id": first["id"], "client_revision": 3, "payload": {}})
        self.assertEqual(restored["status"], "restored")
        missing = app.sync_record({"entity": "entry", "op": "delete", "id": legacy["id"],
                                   "trip_id": first["id"] + 999, "client_revision": 4, "payload": {}})
        self.assertTrue(missing["tombstone"])

    def test_v27_legacy_odometer_can_edit_online_and_bind_on_offline_sync(self):
        trip = app.create_trip({"name": "旧里程迁移", "origin": "成都", "start_odometer": 1000})
        online_legacy = app.save_odometer_reading({"trip_id": trip["id"], "odometer": 1100})
        changed = app.update_odometer_reading(online_legacy["id"], {
            "trip_id": trip["id"], "client_id": None, "client_revision": 2, "odometer": 1110})
        self.assertEqual(changed["odometer"], 1110)
        app.delete_odometer_reading(online_legacy["id"], {
            "trip_id": trip["id"], "client_id": None, "client_revision": 3})

        offline_legacy = app.save_odometer_reading({"trip_id": trip["id"], "odometer": 1200})
        synced = app.sync_record({"entity": "odometer", "op": "upsert",
            "client_id": "bound-after-offline-edit", "client_revision": 2,
            "payload": {"id": offline_legacy["id"], "trip_id": trip["id"],
                "odometer": 1210, "source": "manual"}})
        self.assertEqual(synced["status"], "updated")
        self.assertEqual(synced["record"]["odometer"], 1210)
        self.assertEqual(synced["record"]["client_id"], "bound-after-offline-edit")

    def test_v26_revision_replay_conflict_and_legacy_put(self):
        trip = app.create_trip({"name": "修订", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"], "client_id": "rev-e", "client_revision": 1,
            "raw_text": "停车20元", "recognized": {"category": "parking", "amount": 20,
            "occurred_at": "2026-09-12T10:00:00"}})
        changed = app.update_entry(entry["id"], {"trip_id": trip["id"], "client_revision": 2,
            "client_id": "rev-e",
            "raw_text": "停车25元", "recognized": {"category": "parking", "amount": 25,
            "occurred_at": "2026-09-12T10:00:00"}})
        replay = app.update_entry(entry["id"], {"trip_id": trip["id"], "client_revision": 2,
            "client_id": "rev-e",
            "raw_text": "停车25元", "recognized": {"category": "parking", "amount": 25,
            "occurred_at": "2026-09-12T10:00:00"}})
        self.assertEqual(replay["version"], changed["version"])
        with self.assertRaisesRegex(ValueError, "内容不同"):
            app.update_entry(entry["id"], {"trip_id": trip["id"], "client_revision": 2,
                "client_id": "rev-e",
                "raw_text": "停车26元", "recognized": {"category": "parking", "amount": 26,
                "occurred_at": "2026-09-12T10:00:00"}})
        legacy = app.update_entry(entry["id"], {"trip_id": trip["id"], "client_id": "rev-e", "raw_text": "停车30元",
            "recognized": {"category": "parking", "amount": 30, "occurred_at": "2026-09-12T10:00:00"}})
        self.assertGreater(legacy["version"], changed["version"])

    def test_v26_explicit_odometer_time_finished_boundary_and_expired_trash(self):
        trip = app.create_trip({"name": "边界", "origin": "成都", "start_odometer": 1000})
        first = app.save_odometer_reading({"trip_id": trip["id"], "occurred_at": "2026-09-12T10:00:00", "odometer": 1100})
        with self.assertRaisesRegex(ValueError, "同一记录时间"):
            app.save_odometer_reading({"trip_id": trip["id"], "occurred_at": "2026-09-12T10:00:00", "odometer": 1110})
        app.finish_trip(trip["id"], {"end_odometer": 1200, "ended_at": "2026-09-12T12:00:00"})
        report = app.dashboard(trip["id"])
        self.assertEqual((report["current_odometer"], report["current_odometer_source"]), (1200, "finish"))
        app.reopen_trip(trip["id"])
        app.delete_odometer_reading(first["id"], {"trip_id": trip["id"]})
        with app.connect() as db:
            db.execute("UPDATE odometer_readings SET delete_expires_at=? WHERE id=?", ("2000-01-01T00:00:00", first["id"]))
        self.assertFalse(app.trash(trip["id"])["records"])
        with self.assertRaises(app.RecordGoneError):
            app.restore_record("odometer_reading", first["id"], {"trip_id": trip["id"]})

    def test_v26_safe_fuel_and_meal_split_and_meta_contract(self):
        result = app.parse_text("加油300元40升；午餐30元")
        self.assertEqual([item["recognized"]["category"] for item in result["records"]], ["fuel", "meal"])
        self.assertIsNone(result["records"][1]["recognized"]["fuel_liters"])
        for meta in result["records"][0]["field_meta"].values():
            self.assertIn(meta["state"], {"certain", "review", "missing", "default"})
            self.assertIn("reason", meta); self.assertIn("evidence", meta)

    def test_v21_day_routes_are_metadata_and_spend_is_derived_from_record_dates(self):
        trip = app.create_trip({"name": "青甘大环线", "origin": "西宁", "destination": "敦煌",
                                "start_odometer": 10000, "started_at": "2026-09-20T08:00:00"})
        app.save_entry({"trip_id": trip["id"], "raw_text": "早餐30元", "recognized": {
            "category": "meal", "amount": 30, "item": "早餐", "occurred_at": "2026-09-20T09:00:00"}})
        app.save_odometer_reading({"trip_id": trip["id"], "odometer": 10200,
                                   "occurred_at": "2026-09-20T18:00:00"})
        later = app.save_entry({"trip_id": trip["id"], "raw_text": "住宿260元", "recognized": {
            "category": "lodging", "amount": 260, "item": "民宿", "occurred_at": "2026-09-21T20:00:00"}})
        saved = app.save_trip_day(trip["id"], "2026-09-20", {
            "title": "抵达青海湖", "origin": "西宁", "destination": "青海湖", "via": "湟源", "note": "首日"})
        self.assertEqual((saved["day_number"], saved["spend"], saved["distance_km"]), (1, 30, 200))
        self.assertEqual(saved["title"], "抵达青海湖")
        # 时间改到下一天后，不改消费记录结构，Day 归属和金额即时重算。
        app.update_entry(later["id"], {"trip_id": trip["id"], "raw_text": "住宿260元", "recognized": {
            "category": "lodging", "amount": 260, "item": "民宿", "occurred_at": "2026-09-22T20:00:00"}})
        days = app.trip_days(trip["id"])["days"]
        by_date = {day["travel_date"]: day for day in days}
        self.assertEqual(by_date["2026-09-20"]["spend"], 30)
        self.assertNotIn("2026-09-21", by_date)
        self.assertEqual((by_date["2026-09-22"]["day_number"], by_date["2026-09-22"]["spend"]), (3, 260))
        report = app.dashboard(trip["id"])
        self.assertEqual(report["app_version"], "3.2.0")
        self.assertEqual(sum(day["spend"] for day in report["days"]), report["total_spend"])

    def test_v21_day_route_requires_active_trip_and_excel_has_daily_sheet(self):
        trip = app.create_trip({"name": "每日导出", "origin": "成都", "destination": "兰州",
                                "start_odometer": 1000, "started_at": "2026-09-20T08:00:00"})
        app.save_trip_day(trip["id"], "2026-09-20", {"origin": "成都", "destination": "广元"})
        with zipfile.ZipFile(BytesIO(app.export_workbook())) as workbook:
            self.assertIn("xl/worksheets/sheet6.xml", workbook.namelist())
            daily = workbook.read("xl/worksheets/sheet6.xml").decode()
            workbook_xml = workbook.read("xl/workbook.xml").decode()
        self.assertIn("每日行程", workbook_xml)
        self.assertIn("成都", daily)
        app.finish_trip(trip["id"], {"end_odometer": 1000})
        with self.assertRaisesRegex(ValueError, "进行中的行程"):
            app.save_trip_day(trip["id"], "2026-09-21", {"destination": "兰州"})

    def test_v31_cents_confirmed_stats_and_independent_fuel_amounts(self):
        trip = app.create_trip({"name": "V3.1 口径", "origin": "成都", "start_odometer": 10000,
                                "departure_date": "2026-09-20", "planned_days": 10})
        fuel = app.save_entry({"trip_id": trip["id"], "raw_text": "敦煌加油",
            "recognized": {"category": "fuel", "amount": 356, "fuel_grade": 95,
                           "fuel_liters": 42.6, "fuel_unit_price": 8.36,
                           "odometer": 10400, "full_tank": True}})
        self.assertEqual((fuel["amount_cents"], fuel["fuel_calculated_amount_cents"], fuel["fuel_discount_cents"]),
                         (35600, 35614, 14))
        self.assertEqual(fuel["full_tank"], 1)
        app.save_entry({"trip_id": trip["id"], "raw_text": "尚未确认的餐饮",
            "recognized": {"category": "meal", "amount": 88, "status": "pending", "item": "午餐"}})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["total_spend"], 356)
        self.assertEqual(report["vehicle_cost"], 356)
        self.assertEqual((report["trip"]["departure_date"], report["trip"]["planned_days"]), ("2026-09-20", 10))

    def test_v31_unknown_full_tank_never_creates_consumption_interval(self):
        trip = app.create_trip({"name": "满箱边界", "origin": "成都", "start_odometer": 10000})
        app.save_entry({"trip_id": trip["id"], "raw_text": "加油", "recognized": {
            "category": "fuel", "amount": 300, "fuel_liters": 30, "odometer": 10300, "full_tank": None}})
        app.save_entry({"trip_id": trip["id"], "raw_text": "再次加满", "recognized": {
            "category": "fuel", "amount": 400, "fuel_liters": 40, "odometer": 10600, "full_tank": True}})
        report = app.dashboard(trip["id"])
        self.assertEqual(report["fuel"]["status"], "insufficient_data")
        self.assertIsNone(report["fuel"]["l_per_100km"])

    def test_v31_explicit_null_listed_price_does_not_refill_from_raw_text(self):
        trip = app.create_trip({"name": "清空挂牌价", "origin": "成都", "start_odometer": 1000})
        entry = app.save_entry({"trip_id": trip["id"],
            "raw_text": "油价8块5每升，加45升95号汽油，支付360元，当前里程1200",
            "recognized": {"category": "fuel", "amount": 360, "fuel_grade": 95,
                           "fuel_liters": 45, "fuel_unit_price": None, "odometer": 1200,
                           "full_tank": None}})
        self.assertIsNone(entry["fuel_unit_price"])
        self.assertIsNone(entry["fuel_calculated_amount_cents"])
        self.assertIsNone(entry["fuel_discount_cents"])

    def test_v31_departure_date_is_day_anchor_after_edit(self):
        trip = app.create_trip({"name": "日期锚点", "origin": "成都", "start_odometer": 1000,
                                "started_at": "2026-09-20T08:00:00", "departure_date": "2026-09-18"})
        app.save_odometer_reading({"trip_id": trip["id"], "odometer": 1100,
                                   "occurred_at": "2026-09-18T18:00:00"})
        before = {day["travel_date"]: day for day in app.trip_days(trip["id"])["days"]}
        self.assertEqual(before["2026-09-18"]["day_number"], 1)
        self.assertEqual(before["2026-09-18"]["start_odometer"], 1000)
        app.update_trip_start_odometer(trip["id"], {"departure_date": "2026-09-19"})
        after = {day["travel_date"]: day for day in app.trip_days(trip["id"])["days"]}
        self.assertEqual(after["2026-09-18"]["day_number"], 1)
        self.assertEqual(after["2026-09-18"]["start_odometer"], 1100)
        self.assertEqual(after["2026-09-19"]["start_odometer"], 1000)

    def test_v31_default_export_never_falls_back_to_finished_history(self):
        trip = app.create_trip({"name": "已结束历史", "origin": "成都", "start_odometer": 1000})
        app.save_entry({"trip_id": trip["id"], "recognized": {"category": "meal", "amount": 30}})
        app.finish_trip(trip["id"], {"end_odometer": 1000})
        with zipfile.ZipFile(BytesIO(app.export_workbook())) as workbook:
            empty_export = workbook.read("xl/workbook.xml").decode() + workbook.read("xl/worksheets/sheet1.xml").decode()
        with zipfile.ZipFile(BytesIO(app.export_workbook(include_all=True))) as workbook:
            all_export = workbook.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("已结束历史", empty_export)
        self.assertIn("已结束历史", all_export)


if __name__ == "__main__":
    unittest.main()
