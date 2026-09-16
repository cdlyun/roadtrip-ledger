#!/usr/bin/env python3
"""自驾账本：无第三方运行依赖的 HTTP API + SQLite 服务。"""
from __future__ import annotations

import json
import io
import math
import os
import re
import sqlite3
import sys
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DB_PATH = Path(os.environ.get("ROADTRIP_DB", ROOT / "data" / "roadtrip.db"))
PORT = int(os.environ.get("PORT", "8080"))
APP_VERSION = "3.1.3"
SCHEMA_VERSION = 31

CATEGORIES = {
    "fuel": ("油费", ("加油", "汽油", "油价", "95号", "98号", "95#", "98#")),
    "toll": ("ETC", ("ETC", "etc", "过路费", "高速费", "通行费", "路桥费")),
    "parking": ("停车费", ("停车",)),
    "lodging": ("住宿", ("住宿", "酒店", "宾馆", "旅馆", "民宿", "客栈", "青旅", "营地")),
    "meal": ("餐饮", ("早餐", "午餐", "晚餐", "早饭", "午饭", "晚饭", "夜宵", "吃饭", "餐饮", "餐馆", "饭店", "咖啡", "奶茶", "零食", "饮料")),
    "ticket": ("门票娱乐", ("门票", "景区", "博物馆", "索道", "观光车", "游船", "演出", "娱乐")),
    "daily": ("旅行日用", ("日用", "生活用品", "洗漱", "饮用水", "矿泉水", "药品", "防晒", "补给", "啤酒")),
    "transport": ("其他交通", ("打车", "网约车", "地铁", "公交", "轮渡")),
    "service": ("旅行服务", ("保险", "导游", "寄存", "流量")),
    "clothing": ("衣物", ("衣服", "衣物", "鞋子", "帽子", "外套", "裤子", "买鞋")),
    "shopping": ("购物特产", ("特产", "伴手礼", "纪念品", "购物", "买东西", "采购", "超市")),
    "vehicle": ("车辆费用", ("修车", "维修", "补胎", "拖车", "救援", "罚款", "违章")),
    "other": ("其他", ("其他", "杂费", "未分类")),
}
CORE_CATEGORIES = {"fuel", "toll", "parking", "lodging", "meal", "ticket", "daily", "transport", "service"}
VEHICLE_CATEGORIES = {"fuel", "toll", "parking"}
ENTRY_STATUSES = {"confirmed", "pending", "uncertain", "failed"}
UNSUPPORTED_FLOW_RE = re.compile(r"(?:退款|退回|退押金|押金)")
CN_DIGITS = "零〇一二两三四五六七八九十百千万点"
# 允许规范的千分位写法；解析时会去掉分隔逗号，避免 1,200 被截断为 200。
NUMBER_TOKEN = rf"(?:\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?|[{CN_DIGITS}]+)"


def to_cents(value, label="金额") -> int:
    """将用户输入规范成整数分，避免浮点累计误差。"""
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是有效数字")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是有效数字") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{label}不能小于0")
    return int(round(amount * 100))


def cents_to_amount(cents) -> float:
    return round(int(cents or 0) / 100, 2)


def row_amount_cents(row) -> int:
    """新旧记录统一使用整数分；旧 REAL 值只读迁移，不重写。"""
    value = row["amount_cents"] if "amount_cents" in row.keys() else None
    return int(value) if value is not None else to_cents(row["amount"])


class RecordGoneError(ValueError):
    """回收站保留期已过：旧离线操作不能把记录恢复回来。"""


class RecordConflictError(ValueError):
    """记录身份或状态冲突，客户端需刷新或先完成软删除。"""

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, origin TEXT NOT NULL,
          destination TEXT, started_at TEXT NOT NULL, ended_at TEXT,
          start_odometer REAL, end_odometer REAL, start_full_tank INTEGER NOT NULL DEFAULT 1,
          departure_date TEXT, planned_days INTEGER,
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','finished'))
        );
        CREATE TABLE IF NOT EXISTS app_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
          id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id),
          occurred_at TEXT NOT NULL, category TEXT NOT NULL, category_label TEXT NOT NULL,
          amount REAL NOT NULL CHECK(amount >= 0), amount_cents INTEGER, status TEXT NOT NULL DEFAULT 'confirmed',
          location TEXT, note TEXT, raw_text TEXT NOT NULL,
          fuel_grade INTEGER CHECK(fuel_grade IN (95,98)), fuel_liters REAL,
          fuel_unit_price REAL, fuel_calculated_amount_cents INTEGER, fuel_discount_cents INTEGER,
          odometer REAL, full_tank INTEGER,
          people INTEGER, nights INTEGER, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entries_trip_time ON entries(trip_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_entries_trip_category ON entries(trip_id, category);
        CREATE TABLE IF NOT EXISTS odometer_readings (
          id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id),
          occurred_at TEXT NOT NULL, odometer REAL NOT NULL CHECK(odometer >= 0),
          location TEXT, note TEXT, source TEXT NOT NULL DEFAULT 'manual',
          client_id TEXT, client_revision INTEGER NOT NULL DEFAULT 1,
          version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, deleted_at TEXT, delete_expires_at TEXT,
          occurred_at_explicit INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS record_revisions (
          id INTEGER PRIMARY KEY, record_type TEXT NOT NULL CHECK(record_type IN ('entry','odometer_reading')),
          record_id INTEGER NOT NULL, trip_id INTEGER NOT NULL,
          version INTEGER NOT NULL, action TEXT NOT NULL CHECK(action IN ('create','update','delete','restore')),
          snapshot TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS purged_records (
          id INTEGER PRIMARY KEY,
          record_type TEXT NOT NULL CHECK(record_type IN ('entry','odometer_reading')),
          record_id INTEGER, trip_id INTEGER NOT NULL,
          client_id TEXT, client_revision INTEGER NOT NULL DEFAULT 1,
          purged_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_odometer_trip_time ON odometer_readings(trip_id, occurred_at, id);
        CREATE INDEX IF NOT EXISTS idx_revisions_record ON record_revisions(record_type, record_id, version);
        CREATE INDEX IF NOT EXISTS idx_purged_record ON purged_records(record_type, record_id, trip_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_purged_client_id ON purged_records(record_type, client_id)
          WHERE client_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS trip_days (
          id INTEGER PRIMARY KEY,
          trip_id INTEGER NOT NULL REFERENCES trips(id),
          travel_date TEXT NOT NULL,
          title TEXT,
          origin TEXT,
          destination TEXT,
          via TEXT,
          note TEXT,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(trip_id, travel_date)
        );
        CREATE INDEX IF NOT EXISTS idx_trip_days_trip_date ON trip_days(trip_id, travel_date);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(entries)")}
        for name, definition in {
            "latitude": "REAL", "longitude": "REAL", "gps_accuracy": "REAL",
            "region": "TEXT", "client_id": "TEXT",
            "updated_at": "TEXT", "version": "INTEGER NOT NULL DEFAULT 1",
            "client_revision": "INTEGER NOT NULL DEFAULT 1", "deleted_at": "TEXT",
            "delete_expires_at": "TEXT", "occurred_at_explicit": "INTEGER NOT NULL DEFAULT 0",
            "amount_cents": "INTEGER", "status": "TEXT NOT NULL DEFAULT 'confirmed'",
            "fuel_calculated_amount_cents": "INTEGER", "fuel_discount_cents": "INTEGER",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_client_id "
                   "ON entries(client_id) WHERE client_id IS NOT NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_odometer_client_id "
                   "ON odometer_readings(client_id) WHERE client_id IS NOT NULL")
        reading_columns = {row["name"] for row in db.execute("PRAGMA table_info(odometer_readings)")}
        if "occurred_at_explicit" not in reading_columns:
            db.execute("ALTER TABLE odometer_readings ADD COLUMN occurred_at_explicit INTEGER NOT NULL DEFAULT 0")
        # 每次迁移可重复执行；旧账目的版本从 1 开始，时间以创建时间为准。
        db.execute("UPDATE entries SET version=COALESCE(version,1), client_revision=COALESCE(client_revision,1), "
                   "updated_at=COALESCE(updated_at,created_at)")
        # 历史 REAL 金额仅复制为分，不改写原字段，保证旧导出、修订和记录编号都稳定。
        db.execute("UPDATE entries SET amount_cents=CAST(ROUND(amount*100) AS INTEGER) WHERE amount_cents IS NULL")
        db.execute("UPDATE entries SET status='confirmed' WHERE status IS NULL OR status='' ")
        db.execute("UPDATE entries SET fuel_calculated_amount_cents=CAST(ROUND(fuel_liters*fuel_unit_price*100) AS INTEGER) "
                   "WHERE category='fuel' AND fuel_liters IS NOT NULL AND fuel_unit_price IS NOT NULL "
                   "AND fuel_calculated_amount_cents IS NULL")
        db.execute("UPDATE entries SET fuel_discount_cents=fuel_calculated_amount_cents-amount_cents "
                   "WHERE category='fuel' AND amount_cents IS NOT NULL AND fuel_calculated_amount_cents IS NOT NULL "
                   "AND fuel_discount_cents IS NULL")
        trip_columns = {row["name"] for row in db.execute("PRAGMA table_info(trips)")}
        for name, definition in {"departure_date": "TEXT", "planned_days": "INTEGER"}.items():
            if name not in trip_columns:
                db.execute(f"ALTER TABLE trips ADD COLUMN {name} {definition}")
        db.execute("UPDATE trips SET departure_date=substr(started_at,1,10) WHERE departure_date IS NULL")
        db.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES ('reset_epoch','')")
        db.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))
        db.execute("UPDATE entries SET category_label='ETC' WHERE category='toll' AND category_label<>'ETC'")
        db.execute("PRAGMA optimize")


def find_purged_record(db, record_type: str, client_id=None, record_id=None, trip_id=None):
    """按稳定客户端编号优先查墓碑；无客户端编号时才用旧记录编号兜底。"""
    normalized_client_id = str(client_id or "").strip() or None
    if normalized_client_id:
        return db.execute(
            "SELECT * FROM purged_records WHERE record_type=? AND client_id=? ORDER BY id DESC LIMIT 1",
            (record_type, normalized_client_id),
        ).fetchone()
    if record_id is None or trip_id is None:
        return None
    try:
        record_id, trip_id = int(record_id), int(trip_id)
    except (TypeError, ValueError):
        return None
    return db.execute(
        "SELECT * FROM purged_records WHERE record_type=? AND record_id=? AND trip_id=? ORDER BY id DESC LIMIT 1",
        (record_type, record_id, trip_id),
    ).fetchone()


def reject_purged_client_id(db, record_type: str, client_id) -> None:
    if client_id and find_purged_record(db, record_type, client_id=client_id):
        raise RecordGoneError("该本机记录已永久删除，不能重新入账")


def validate_record_client_id(row, payload: dict) -> None:
    """对已有记录的普通写操作校验稳定身份，防止旧 id 请求误伤新记录。"""
    stored = str(row["client_id"] or "").strip() or None
    supplied = str((payload or {}).get("client_id") or "").strip() or None
    if stored != supplied:
        raise RecordConflictError("客户端编号与当前记录不匹配，请刷新后重试")


def next_record_id(db, table: str, record_type: str) -> int:
    """新 id 永远越过当前表和永久墓碑的历史最大值。"""
    table_max = db.execute(f"SELECT COALESCE(MAX(id),0) AS value FROM {table}").fetchone()["value"]
    tombstone_max = db.execute(
        "SELECT COALESCE(MAX(record_id),0) AS value FROM purged_records WHERE record_type=?",
        (record_type,),
    ).fetchone()["value"]
    return max(int(table_max or 0), int(tombstone_max or 0)) + 1


def number(pattern: str, text: str):
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    value = m.group(1)
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return cn_number(value)


def expense_amount(text: str):
    """提取实付金额，明确排除“8元/升”和“8元每升”类单价。"""
    def parsed(value):
        return float(value.replace(",", "")) if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", value) else cn_number(value)

    # “支付/实付/金额”等明确标签优先于句中其它价格，避免把油价当成总金额。
    amount_label = r"(?:支付金额|消费金额|支付|实付|付款|付了|付|金额|花费|一共|总共|合计)"
    labeled = re.search(
        rf"{amount_label}\s*(?:了|为|是)?\s*[:：]?\s*({NUMBER_TOKEN})"
        rf"(?:\s*(?:元|块钱?|块)\s*([0-9零〇一二两三四五六七八九])?\s*(?:角|毛)?)?",
        text,
    )
    if labeled:
        value = parsed(labeled.group(1))
        return value + (parsed(labeled.group(2)) / 10 if labeled.group(2) else 0)

    # 再识别“三十块五/三十块五毛”；所有候选都排除“每升”油价。
    colloquial_values = []
    for match in re.finditer(
        rf"({NUMBER_TOKEN})\s*(?:元|块)\s*([0-9零〇一二两三四五六七八九])\s*(?:角|毛)?(?![0-9{CN_DIGITS}])",
        text,
    ):
        if re.match(r"\s*(?:(?:/|每)\s*)?升", text[match.end():]):
            continue
        colloquial_values.append(parsed(match.group(1)) + parsed(match.group(2)) / 10)
    if colloquial_values:
        return colloquial_values[-1]
    values = []
    for match in re.finditer(rf"({NUMBER_TOKEN})\s*(?:元|块钱?|块)", text):
        suffix = text[match.end():]
        if re.match(r"\s*(?:(?:/|每)\s*)?升", suffix) or re.match(
            r"\s*[0-9零〇一二两三四五六七八九]\s*(?:角|毛)?\s*(?:(?:/|每)\s*)?升", suffix
        ):
            continue
        values.append(parsed(match.group(1)))
    if values:
        return values[-1]
    cn_matches = []
    for match in re.finditer(r"([零〇一二两三四五六七八九十百千万点]+)\s*(?:元|块钱?|块)", text):
        suffix = text[match.end():]
        if re.match(r"\s*(?:(?:/|每)\s*)?升", suffix) or re.match(
            r"\s*[0-9零〇一二两三四五六七八九]\s*(?:角|毛)?\s*(?:(?:/|每)\s*)?升", suffix
        ):
            continue
        cn_matches.append(cn_number(match.group(1)))
    if cn_matches:
        return cn_matches[-1]
    return None


def cn_number(value: str) -> float:
    """解析记账常见中文数字，例如五百、一百二十六、四十点二。"""
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "点" in value:
        integer, fraction = value.split("点", 1)
        decimal = "".join(str(digits[ch]) for ch in fraction if ch in digits)
        return cn_number(integer or "零") + (float(f"0.{decimal}") if decimal else 0)
    total = section = current = 0
    for ch in value:
        if ch in digits:
            current = digits[ch]
        elif ch in {"十", "百", "千"}:
            unit = {"十": 10, "百": 100, "千": 1000}[ch]
            section += (current or 1) * unit
            current = 0
        elif ch == "万":
            total += (section + current or 1) * 10000
            section = current = 0
    return float(total + section + current)


def detect_category(text: str):
    """按消费行为优先级判断一级分类，避免场所词抢占真实类别。

    例如“在酒店停车20元”应是停车费，不是住宿；
    “在景区停车”也不应被“景区”归到门票。
    """
    # “加油站”可能只是购买日用品的场所；只有出现加油动作、油号、
    # 升数或油价时才把站名当作油费信号。
    negated_fuel = re.search(r"(?:没|没有|未|不)(?:有)?\s*加油", text)
    fuel_additive = re.search(r"(?:汽油|燃油)添加剂", text)
    strong_fuel = re.search(
        r"(?:加(?:了)?\s*(?:(?:95|98|九五|九八|九十五|九十八)\s*(?:号|#)?\s*)?(?:汽油|油)(?!站)|汽油|(?:95|98|九五|九八|九十五|九十八)\s*(?:号|#)?\s*汽油|油价|加油量|升数)",
        text,
    )
    if strong_fuel and not negated_fuel and not fuel_additive:
        return "fuel", CATEGORIES["fuel"][0]

    # 明确消费动作优先；饭店、酒店、停车场、收费站等场所词只用于兜底。
    action_rules = (
        ("toll", r"(?:ETC|过路费|高速费|通行费|路桥费|通行扣费)"),
        ("parking", r"(?:停车费|停车(?!场)|停了车)"),
        ("vehicle", r"(?:汽油添加剂|燃油添加剂|修车|维修|保养|补胎|换胎|换轮胎|轮胎|拖车|救援|罚款|违章|洗车)"),
        ("transport", r"(?:打车|网约车|出租车|地铁|公交|轮渡|船票|车票)"),
        ("lodging", r"(?:住宿|住店|入住|房费|住(?:了)?(?=\s*(?:酒店|宾馆|旅馆|民宿|客栈|青旅|营地|一|两|三|\d)))"),
        ("meal", r"(?:早餐|午餐|晚餐|早饭|午饭|晚饭|夜宵|吃饭|用餐|餐饮|咖啡|奶茶|零食|饮料|米粉|牛肉面|拉面|火锅|烧烤|炒菜|(?:吃|喝|点了)(?!亏))"),
        ("service", r"(?:旅游保险|导游|寄存|手机流量|流量包)"),
        ("clothing", r"(?:防晒衣|衣服|衣物|冲锋衣|鞋子|帽子|外套|裤子|袜子)"),
        ("daily", r"(?:旅行日用|日用|生活用品|洗漱|饮用水|矿泉水|药品|买药|防晒(?!衣)|补给|啤酒)"),
        ("shopping", r"(?:特产|伴手礼|纪念品|购物|买东西|采购)"),
        ("ticket", r"(?:门票|买票|票价|景区票|索道票?|观光车票?|游船|演出票?|娱乐)"),
    )
    for key, pattern in action_rules:
        if re.search(pattern, text, re.I):
            return key, CATEGORIES[key][0]
    fallback_rules = (
        ("meal", r"(?:餐馆|饭店)"),
        ("lodging", r"(?:酒店|宾馆|旅馆|民宿|客栈|青旅|营地)"),
        ("parking", r"停车场"),
        ("toll", r"收费站"),
        ("ticket", r"(?:景区|博物馆)"),
        ("shopping", r"超市"),
    )
    for key, pattern in fallback_rules:
        if re.search(pattern, text, re.I):
            return key, CATEGORIES[key][0]
    return None, None


def parse_spoken_datetime(text: str, now: datetime | None = None):
    """解析句子中明确说出的本地时间；未说时返回 None，由界面默认当前时间。"""
    now = (now or datetime.now()).replace(microsecond=0)
    iso = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    chinese_date = re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]?", text)
    relative = re.search(r"(前天|昨天|昨晚|今天|今日|刚才|刚刚|现在)", text)
    clock = re.search(
        rf"(?:(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间)\s*)?"
        rf"({NUMBER_TOKEN})\s*(?:点|时)(?:(半)|\s*({NUMBER_TOKEN})\s*分?)?",
        text,
    )
    colon_clock = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)", text)
    if not any((iso, chinese_date, relative, clock, colon_clock)):
        return None
    try:
        if iso:
            year, month, day = map(int, iso.group(1, 2, 3))
        elif chinese_date:
            year = int(chinese_date.group(1) or now.year)
            month, day = map(int, chinese_date.group(2, 3))
        else:
            offset = -2 if relative and relative.group(1) == "前天" else -1 if relative and relative.group(1) in {"昨天", "昨晚"} else 0
            base = now + timedelta(days=offset)
            year, month, day = base.year, base.month, base.day
        hour, minute, second = now.hour, now.minute, now.second
        if iso and iso.group(4) is not None:
            hour, minute, second = int(iso.group(4)), int(iso.group(5)), int(iso.group(6) or 0)
        elif colon_clock:
            hour, minute, second = map(int, (colon_clock.group(1), colon_clock.group(2), colon_clock.group(3) or 0))
        elif clock:
            period, hour_text, half, minute_text = clock.groups()
            hour = int(float(hour_text)) if re.fullmatch(r"\d+(?:\.\d+)?", hour_text) else int(cn_number(hour_text))
            minute = 30 if half else (int(float(minute_text)) if minute_text and re.fullmatch(r"\d+(?:\.\d+)?", minute_text) else int(cn_number(minute_text)) if minute_text else 0)
            second = 0
            if period in {"下午", "傍晚", "晚上", "晚间"} and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            elif period == "凌晨" and hour == 12:
                hour = 0
        return datetime(year, month, day, hour, minute, second).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def clean_item_text(value: str | None):
    """去掉消费内容尾部的金额和后续字段，保留“一瓶啤酒”等数量描述。"""
    if not value:
        return None
    result = str(value).strip(" ，,。；;：:")
    result = re.sub(r"\s*(?:在|于)\s*[^，,。；;\d]{1,24}\s*$", "", result)
    result = re.split(
        r"\s*(?:，|,|。|；|;)?\s*(?:地点|位置|当前里程|里程表|表显|升数|加油量|记录时间)\s*[:：]?",
        result,
        maxsplit=1,
    )[0]
    labeled = rf"(?:支付金额|消费金额|支付|实付|付款|付了|付|金额|花费|花了|一共|总共|合计)\s*(?:了|为|是)?\s*[:：]?\s*{NUMBER_TOKEN}\s*(?:元|块钱?|块)?"
    colloquial = rf"{NUMBER_TOKEN}\s*(?:元|块)\s*[0-9零〇一二两三四五六七八九]\s*(?:角|毛)?"
    priced = rf"(?:共|总共|一共)?\s*(?:{colloquial}|{NUMBER_TOKEN}\s*(?:元|块钱?|块))"
    result = re.sub(rf"\s*(?:{labeled}|{priced})\s*$", "", result).strip(" ，,。；;")
    return result or None


def count_number(pattern: str, text: str):
    """解析人数、晚数等小整数，兼容“3人”和“两晚”等转写。"""
    match = re.search(pattern, text)
    if not match:
        return None
    value = match.group(1)
    return int(float(value)) if re.fullmatch(r"\d+(?:\.\d+)?", value) else int(cn_number(value))


def missing(field, label, level, reason):
    return {"field": field, "label": label, "level": level, "reason": reason}


def normalize_local_datetime(value) -> str:
    """校验并统一记账时间到秒，不接受日期、时区或任意文本。"""
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", raw):
        raise ValueError("记录时间格式无效，请使用年-月-日T时:分")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("记录时间不是有效的日期时间") from exc
    return parsed.isoformat(timespec="seconds")


def multiple_consumption_amounts(text: str, category: str | None) -> bool:
    """拒绝一句包含多笔普通消费的转写，避免只保存最后一个金额。"""
    # 不能按分类豁免：加油句也可能混入咖啡、住宿或任意购物消费。
    # 只排除能确定为单价的金额，并把“30元，合计30元”视为同一笔汇总。
    values, has_total = [], False
    amount_label = r"(?:支付金额|消费金额|支付|实付|付款|付了|付|金额|花费|花了|一共|总共|合计)"
    pattern = rf"(?:{amount_label}\s*(?:了|为|是)?\s*[:：]?\s*({NUMBER_TOKEN})(?:\s*(?:元|块钱?|块))?|({NUMBER_TOKEN})\s*(?:元|块钱?|块))"
    for match in re.finditer(pattern, text):
        value = match.group(1) or match.group(2)
        value_start = match.start(1) if match.group(1) is not None else match.start(2)
        value_end = value_start + len(value)
        # “每瓶3元共6元”“每晚200元共400元”前者是单位价，不是第二笔消费。
        if re.search(r"(?:每|单价)\s*(?:瓶|晚|升|份|个|张|件)?\s*$", text[max(0, value_start - 8):value_start]):
            continue
        if re.match(r"\s*(?:(?:/|每)\s*)?升", text[value_end:]) or re.match(
            r"\s*(?:元|块)\s*[0-9零〇一二两三四五六七八九]\s*(?:角|毛)?\s*(?:(?:/|每)\s*)?升", text[value_end:]
        ):
            continue
        values.append(value)
        has_total = has_total or bool(re.search(r"(?:一共|总共|合计)", match.group(0)))
    normalized = {value.replace(",", "") for value in values}
    return len(values) > 1 and not (has_total and len(normalized) == 1)


def safe_split_records(text: str) -> list[str] | None:
    """仅处理明显分隔的多笔口述。

    每段必须各自有消费动作/分类与一个总金额；加油的油号、升数、里程、单价
    是同一笔字段，绝不按逗号拆开。不能证明安全时交回原有的“请拆分”阻断。
    """
    chunks = [part.strip() for part in re.split(r"[；;。]|(?<!\d)，(?!\d{3}(?:\D|$))|(?<!\d),(?!\d{3}(?:\D|$))", text) if part.strip()]
    if len(chunks) < 2:
        return None
    accepted = []
    for chunk in chunks:
        category, _ = detect_category(chunk)
        # 油费的附属字段段没有金额+分类，天然不通过；完整油费段可与另一笔消费拆开。
        if not category or expense_amount(chunk) is None or multiple_consumption_amounts(chunk, category):
            return None
        accepted.append(chunk)
    return accepted if len(accepted) >= 2 else None


def field_meta(fields: dict, gaps: list[dict], derived: list[str], raw_text: str) -> dict:
    """四态字段契约：certain/review/missing/default，低置信字段不可伪装成确定值。"""
    gap_by_field = {gap["field"]: gap for gap in gaps}
    result = {}
    for key, value in fields.items():
        if key in derived:
            state, reason, evidence = "certain", "由原句中的金额和数量确定性计算", raw_text
        elif key == "fuel_grade" and fields.get("category") == "fuel" and not re.search(r"(?:95|98|九五|九八|九十五|九十八)", raw_text):
            state, reason, evidence = "default", "未说油号，按95号默认", None
        elif value is None or key in gap_by_field:
            gap = gap_by_field.get(key, {})
            state, reason, evidence = "missing", gap.get("reason", "原句未提供该字段"), None
        else:
            state, reason, evidence = "certain", "从原句明确识别", raw_text
        result[key] = {"state": state, "value": value, "reason": reason, "evidence": evidence}
    return result


def parse_text(text: str, _single: bool = False) -> dict:
    """从手机本地转写文字中提取费用，再用确定性规则生成缺失项。"""
    text = " ".join(str(text or "").strip().split())
    # 仅将数字之间的全角逗号标准化，不影响中文语句分隔符。
    text = re.sub(r"(?<=\d)，(?=\d{3}(?:\D|$))", ",", text)
    if not _single:
        chunks = safe_split_records(text)
        if chunks:
            records = [parse_text(chunk, _single=True) for chunk in chunks]
            # “在广元午餐30元，晚餐50元”继承已明确的地点；不会猜测新的地点。
            last_location = None
            for record in records:
                if record["recognized"].get("location"):
                    last_location = record["recognized"]["location"]
                elif last_location:
                    record["recognized"]["location"] = last_location
                    record["field_meta"]["location"] = {"state": "review", "value": None,
                                                               "reason": "沿用上一笔明确地点，请确认", "evidence": last_location}
            # 保留旧客户端所依赖的提示字段，但它不是阻断：新版会逐条确认 records 后提交。
            return {"raw_text": text, "recognized": records[0]["recognized"],
                    "missing": [missing("multiple_entries", "已拆分多笔消费", "info", "已识别为多笔，请逐笔确认后保存")],
                    "derived_fields": [], "field_meta": records[0]["field_meta"], "records": records,
                    "can_save": all(record["can_save"] for record in records)}
    category, label = detect_category(text)
    amount = expense_amount(text)
    spoken_number = rf"({NUMBER_TOKEN})"
    liters = number(spoken_number + r"\s*(?:升(?!数)|[Ll])", text)
    if liters is None:
        liters = number(r"(?:升数|加油量)\s*(?:是|为)?\s*[:：]?\s*" + spoken_number, text)
    colloquial_unit = re.search(
        rf"({NUMBER_TOKEN})\s*(?:元|块)\s*([0-9零〇一二两三四五六七八九])\s*(?:角|毛)?"
        rf"\s*(?:(?:/|每)\s*)?(?:升|[Ll])",
        text,
    )
    if colloquial_unit:
        whole = (float(colloquial_unit.group(1)) if re.fullmatch(r"\d+(?:\.\d+)?", colloquial_unit.group(1))
                 else cn_number(colloquial_unit.group(1)))
        fraction = (float(colloquial_unit.group(2)) if colloquial_unit.group(2).isdigit()
                    else cn_number(colloquial_unit.group(2)))
        unit_price = whole + fraction / 10
    else:
        unit_price = number(spoken_number + r"\s*(?:元|块钱?|块)\s*(?:/|每)?\s*(?:升|[Ll])", text)
    odometer = number(r"(?:(?:当前|现在)\s*)?(?:里程(?:表)?(?:读数)?|公里数|表显里程|表显)\s*(?:是|为|到)?\s*[:：]?\s*" + spoken_number, text)
    mentioned_grades = {int(x) for x in re.findall(r"(\d{2,3})\s*(?:(?:号|#)(?=\s*(?:汽油|油)?)|(?=汽油))", text)}
    if re.search(r"九五\s*(?:号|汽油|号汽油)", text):
        mentioned_grades.add(95)
    if re.search(r"九八\s*(?:号|汽油|号汽油)", text):
        mentioned_grades.add(98)
    if re.search(r"九十五\s*(?:号|汽油|号汽油)", text):
        mentioned_grades.add(95)
    if re.search(r"九十八\s*(?:号|汽油|号汽油)", text):
        mentioned_grades.add(98)
    grade_invalid = bool(mentioned_grades - {95, 98})
    grade_conflict = 95 in mentioned_grades and 98 in mentioned_grades
    grade = None if grade_invalid or grade_conflict else (98 if 98 in mentioned_grades else 95)
    not_full_pattern = r"(?:没(?:有)?加满|未加满|没有满油|不是满油)"
    full_tank = True if re.search(r"(?:加满|满油)", text) else (False if re.search(not_full_pattern, text) else None)
    # 先识别“没加满”，避免其中的“加满”被误判。没有明确口述时
    # 保持未确认，不能因为是油费而擅自当成满箱区间。
    if re.search(not_full_pattern, text):
        full_tank = False

    location = None
    m = re.search(r"(?:地点|位置)\s*(?:是|在|为)?\s*[:：]?\s*([^，,。；;]+?)(?=\s*(?:买了|购买|消费内容|消费项目|支付|实付|金额|花费|人数|住了|升数|当前里程|里程|记录时间|加(?:了)?\s*(?:95|98|九五|九八|九十五|九十八)?\s*(?:号|#)?\s*(?:汽油|油)|$))", text)
    if not m:
        station = re.search(
            r"(?:^|[，,；;]\s*|(?<!现)在\s*|于\s*)"
            r"([^，,。；;]{1,32}?(?:(?:中国石油|中国石化|中石油|中石化)?加油站|中国石油|中国石化|中石油|中石化))"
            r"(?=[，,。；;]|当前里程|里程表|加|支付|付款|买|购买|吃|喝|停车|$)",
            text,
        )
        if station:
            location = re.sub(
                r"^(?:(?:今天|今日|昨天|昨晚|前天|刚才|刚刚|现在|路上)[，,\s]*)+",
                "",
                station.group(1),
            ).strip()
            location = re.sub(r"^(?:在|于)\s*", "", location).strip() or None
    if not m and not location:
        # 避免把“现在刚刚加油”里的“在”当成地点介词。
        m = re.search(r"(?<!现)(?:在|于)\s*([^，,。；;]{1,24}?)(?:的)?(?=早餐|午餐|晚餐|早饭|午饭|晚饭|夜宵|加了|加油|停车|吃了|吃|喝|用餐|住了|住|住宿|入住|花了|买了|买|门票|游玩|支付|付了|扣了|扣费|过路费|高速费|通行费|ETC|打车|网约车|补胎|换轮胎|维修|保养|洗车|给车)", text, re.I)
    if not m and not location:
        # 也兼容“午餐吃米粉30元，在广元”这类后置地点。
        m = re.search(r"(?:[，,；;]|元|块)\s*(?<!现)(?:在|于)\s*([^，,。；;\d]{1,24})\s*$", text)
    if not m and not location and category == "fuel":
        # 加油站名称常直接出现在“加98号汽油/加油”之前，不一定带“在”。
        # 句首的时间或状态转写词不属于地点。
        fuel_prefix = re.search(
            r"^([^。；;]{1,64}?)(?=加(?:了)?\s*(?:(?:95|98|九五|九八)\s*(?:号|#)?\s*(?:汽油|油)|油))",
            text,
        )
        if fuel_prefix:
            location = re.sub(
                r"^(?:(?:今天|刚才|刚刚|现在|路上)[，,\s]*)+",
                "",
                fuel_prefix.group(1),
            ).strip()
            location = re.sub(r"^(?:在|于)\s*", "", location).strip() or None
    if not m and not location:
        # 无“在”时的地点前缀，如“兰州午餐吃面”、“敦煌门票”。
        prefix = re.search(
            r"^(?:(?:今天|今日|昨天|昨晚|前天|刚才|刚刚|现在|路上)[，,\s]*)*"
            r"([^，,。；;\d]{1,24}?)(?=早餐|午餐|晚餐|早饭|午饭|晚饭|夜宵|停车|吃|喝|住宿|入住|门票|打车|加油)",
            text,
        )
        if prefix:
            location = prefix.group(1).strip() or None
    if m:
        location = m.group(1).strip()
    item = None
    item_match = re.search(r"(?:消费内容|消费项目|消费了什么|物品)\s*(?:是|为)?\s*[:：]?\s*([^，,。；;]+)", text)
    if not item_match:
        item_match = re.search(r"(?:买了|买个?|购买了?|采购了?|点了)\s*([^，,。；;]+)", text)
    if not item_match and category == "meal":
        item_match = re.search(r"(?:早餐|午餐|晚餐|早饭|午饭|晚饭|夜宵)?\s*(?:吃(?:了)?|喝(?:了)?|点了)\s*([^，,。；;]+)", text)
    if not item_match and category == "meal":
        item_match = re.search(r"(?:早餐|午餐|晚餐|早饭|午饭|晚饭|夜宵)\s*([^，,。；;]+)", text)
    if not item_match and category == "lodging":
        item_match = re.search(r"(?:入住|住店|住(?!宿)(?:了)?|订了?)\s*([^，,。；;]+)", text)
    if not item_match and category == "transport":
        item_match = re.search(r"((?:打车|网约车|出租车)(?:去|到|前往)?[^，,。；;]*)", text)
    if item_match:
        item = clean_item_text(item_match.group(1))
    if not item and category:
        defaults = {
            "toll": r"(?:ETC|过路费|高速费|通行费|路桥费)",
            "parking": r"(?:停车费|停车)", "transport": r"(?:打车|网约车|出租车|地铁|公交|轮渡)",
            "service": r"(?:旅游保险|保险|导游|寄存|流量包?)", "vehicle": r"(?:修车|维修|保养|补胎|换轮胎|换胎|轮胎|拖车|救援|洗车)",
            "ticket": r"[^\s，,。；;]*?(?:门票|索道票?|观光车票?|游船|演出票?)", "lodging": r"(?:酒店房费|房费|住宿|酒店|宾馆|民宿|客栈|青旅|营地)",
            "daily": r"(?:旅行日用|生活用品|洗漱用品|矿泉水|药品|防晒|补给)", "clothing": r"(?:冲锋衣|衣服|鞋子|帽子|外套|裤子|袜子)",
            "shopping": r"(?:特产|伴手礼|纪念品|购物)",
        }
        fallback = re.search(defaults.get(category, r"(?!x)x"), text, re.I)
        item = clean_item_text(fallback.group(0)) if fallback else None
    if category == "parking" and item == "停车":
        item = "停车费"
    people = count_number(r"(\d+|[零〇一二两三四五六七八九十]+)\s*(?:个)?人", text)
    nights = count_number(r"(\d+|[零〇一二两三四五六七八九十]+)\s*晚", text)

    # 挂牌油价、实付和升数是三个独立事实。优惠、满减或会员价会让
    # ``实付 / 升数`` 不等于挂牌价，这不是冲突。
    derived = []

    fields = {
        "category": category, "category_label": label, "amount": amount,
        "location": location, "fuel_grade": grade if category == "fuel" else None,
        "fuel_liters": liters if category == "fuel" else None,
        "fuel_unit_price": unit_price if category == "fuel" else None,
        "odometer": odometer if category == "fuel" else None,
        "full_tank": full_tank if category == "fuel" else None,
        "people": int(people) if people else None,
        "nights": int(nights) if nights else None,
        "item": item, "note": item, "occurred_at": parse_spoken_datetime(text),
    }
    gaps = []
    if UNSUPPORTED_FLOW_RE.search(text):
        gaps.append(missing("transaction_type", "退款/押金流水", "required", "MVP 尚不支持退款和押金，已阻止当作普通消费入账"))
    if not category:
        gaps.append(missing("category", "消费类别", "required", "无法确定这笔钱应归到哪一类"))
    if amount is None:
        gaps.append(missing("amount", "金额", "required", "消费金额是入账必填项"))
    if multiple_consumption_amounts(text, category):
        gaps.append(missing("multiple_entries", "拆分多笔消费", "required", "一句话包含多笔金额，请分别识别并保存每笔消费"))
    if category == "fuel":
        if grade_conflict:
            gaps.append(missing("fuel_grade", "确认油号", "required", "同时识别到95号和98号，请确认本次加油油号"))
        elif grade_invalid:
            gaps.append(missing("fuel_grade", "确认油号", "required", "只支持95号或98号汽油"))
        if liters is None:
            gaps.append(missing("fuel_liters", "加油升数", "metric", "缺少后无法计算实时百公里油耗"))
        if odometer is None:
            gaps.append(missing("odometer", "当前里程", "metric", "缺少后无法计算实时里程和每公里成本"))
    if category and not location:
        gaps.append(missing("location", "消费地点", "optional", "用于查看路线上的花费分布"))
    if category not in {None, "fuel"} and not item:
        gaps.append(missing("item", "消费内容", "optional", "用于说明具体买了什么或支付了什么"))

    result = {
        "raw_text": text, "recognized": fields, "missing": gaps,
        "derived_fields": derived,
        "field_meta": field_meta(fields, gaps, derived, text),
        "can_save": not any(x["level"] == "required" for x in gaps),
    }
    result["records"] = [dict(result)]
    return result


def rowdict(row):
    return dict(row) if row else None


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def revision_snapshot(db, record_type: str, row, action: str) -> None:
    """在变更前保存不可变快照；相同状态只保留一次，避免历史重复。"""
    data = rowdict(row)
    if not data:
        return
    snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True)
    previous = db.execute("""SELECT snapshot FROM record_revisions
        WHERE record_type=? AND record_id=? ORDER BY id DESC LIMIT 1""", (record_type, data["id"])).fetchone()
    if previous and previous["snapshot"] == snapshot:
        return
    db.execute("""INSERT INTO record_revisions
        (record_type,record_id,trip_id,version,action,snapshot,created_at)
        VALUES (?,?,?,?,?,?,?)""", (
            record_type, data["id"], data["trip_id"], data.get("version", 1), action,
            snapshot, now_text()))


def revision_state(existing, payload: dict) -> str:
    """返回 legacy/replay/update；同修订号的内容等价性由调用者最终确认。"""
    supplied = payload.get("client_revision")
    if supplied is None:
        return "legacy"
    try:
        supplied = int(supplied)
    except (TypeError, ValueError) as exc:
        raise ValueError("客户端修订号无效") from exc
    current = int(existing["client_revision"] or 1)
    if supplied < current:
        raise ValueError("本机记录版本已过期，请先刷新后再修改")
    return "replay" if supplied == current else "update"


def ensure_client_revision(existing, payload: dict) -> bool:
    """兼容删除/恢复的已处理重放；更新路径必须再比较实际字段。"""
    return revision_state(existing, payload) == "replay"


def values_equal(existing, values: dict, fields: tuple[str, ...]) -> bool:
    def canonical(value):
        if isinstance(value, float):
            return round(value, 9)
        if isinstance(value, bool):
            return int(value)
        return value
    return all(canonical(existing[field]) == canonical(values.get(field)) for field in fields)


def reading_rows(db, trip_id: int, include_deleted=False):
    suffix = "" if include_deleted else " AND deleted_at IS NULL"
    return db.execute(f"SELECT * FROM odometer_readings WHERE trip_id=?{suffix} ORDER BY occurred_at,id", (trip_id,)).fetchall()


def all_odometer_points(db, trip_id: int, exclude_entry_id=None, exclude_reading_id=None):
    """同一条时序包含加油里程与手工里程，统计和校验共享这一个口径。"""
    entry_exclusion = "" if exclude_entry_id is None else " AND id<>?"
    entry_args = [trip_id] + ([] if exclude_entry_id is None else [exclude_entry_id])
    read_exclusion = "" if exclude_reading_id is None else " AND id<>?"
    read_args = [trip_id] + ([] if exclude_reading_id is None else [exclude_reading_id])
    entries = db.execute(
        f"SELECT id,occurred_at,odometer,occurred_at_explicit,'fuel' AS source FROM entries "
        f"WHERE trip_id=? AND deleted_at IS NULL AND odometer IS NOT NULL{entry_exclusion}", entry_args).fetchall()
    readings = db.execute(
        f"SELECT id,occurred_at,odometer,occurred_at_explicit,source FROM odometer_readings "
        f"WHERE trip_id=? AND deleted_at IS NULL{read_exclusion}", read_args).fetchall()
    return sorted([rowdict(r) for r in entries] + [rowdict(r) for r in readings],
                  key=lambda r: (r["occurred_at"], 0 if r["source"] == "fuel" else 1, r["id"]))


def validate_odometer_point(db, trip, occurred_at: str, odometer: float, explicit_time=False,
                            exclude_entry_id=None, exclude_reading_id=None) -> None:
    points = all_odometer_points(db, trip["id"], exclude_entry_id, exclude_reading_id)
    # 表单未指定时间时由服务器打点；它只能作为显示顺序，不能推翻用户后来
    # 补录的明确历史时间。明确时间之间仍必须严格单调且同秒不能取不同数值。
    comparable = points if not explicit_time else [point for point in points if point.get("occurred_at_explicit")]
    same_time = [point for point in comparable if point["occurred_at"] == occurred_at] if explicit_time else []
    if any(float(point["odometer"]) != float(odometer) for point in same_time):
        raise ValueError("同一记录时间不能保存不同的里程读数")
    before = [point for point in comparable if point["occurred_at"] <= occurred_at]
    after = [point for point in comparable if point["occurred_at"] > occurred_at]
    lower = before[-1]["odometer"] if before else trip["start_odometer"]
    upper = after[0]["odometer"] if after else None
    if lower is not None and odometer < float(lower):
        raise ValueError("里程表读数不能小于时间更早的里程记录")
    if upper is not None and odometer > float(upper):
        raise ValueError("里程表读数不能大于时间更晚的里程记录")


def prepare_odometer_reading(db, payload: dict, existing=None) -> dict:
    try:
        trip_id = int(payload.get("trip_id") if payload.get("trip_id") is not None else existing["trip_id"])
    except (TypeError, ValueError) as exc:
        raise ValueError("行程信息无效") from exc
    trip = db.execute("SELECT * FROM trips WHERE id=? AND status='active'", (trip_id,)).fetchone()
    if not trip:
        raise ValueError("请先开始一个进行中的行程；已结束行程不能新增或修改里程")
    raw_odo = payload.get("odometer")
    if raw_odo is None or isinstance(raw_odo, bool):
        raise ValueError("当前里程必填")
    try:
        odometer = float(raw_odo)
    except (TypeError, ValueError) as exc:
        raise ValueError("里程表读数必须是有效数字") from exc
    if not math.isfinite(odometer) or odometer < 0:
        raise ValueError("里程表读数必须是大于等于0的有限数字")
    explicit_time = payload.get("occurred_at") is not None
    occurred_at = normalize_local_datetime(payload.get("occurred_at") or now_text())
    validate_odometer_point(db, trip, occurred_at, odometer,
                            explicit_time=explicit_time,
                            exclude_reading_id=existing["id"] if existing else None)
    source = str(payload.get("source") or "manual").strip()
    if source not in {"manual", "gps", "import"}:
        raise ValueError("里程来源无效")
    return {"trip_id": trip_id, "occurred_at": occurred_at, "odometer": odometer,
            "location": str(payload.get("location") or "").strip() or None,
            "note": str(payload.get("note") or "").strip() or None, "source": source,
            "occurred_at_explicit": int(explicit_time)}


def save_odometer_reading(payload: dict) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        client_id = str(payload.get("client_id") or "").strip() or None
        reject_purged_client_id(db, "odometer_reading", client_id)
        if client_id:
            existing = db.execute("SELECT * FROM odometer_readings WHERE client_id=?", (client_id,)).fetchone()
            if existing:
                if ensure_client_revision(existing, payload):
                    result = rowdict(existing); result["validation"] = {"idempotent": True}; return result
                raise ValueError("该客户端编号已经用于另一条里程记录")
        values = prepare_odometer_reading(db, payload)
        stamp = now_text(); revision = int(payload.get("client_revision") or 1)
        record_id = next_record_id(db, "odometer_readings", "odometer_reading")
        db.execute("""INSERT INTO odometer_readings
          (id,trip_id,occurred_at,odometer,location,note,source,client_id,client_revision,version,created_at,updated_at,occurred_at_explicit)
          VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)""", (
              record_id, values["trip_id"], values["occurred_at"], values["odometer"], values["location"], values["note"],
              values["source"], client_id, revision, stamp, stamp, values["occurred_at_explicit"]))
        row = db.execute("SELECT * FROM odometer_readings WHERE id=?", (record_id,)).fetchone()
        revision_snapshot(db, "odometer_reading", row, "create")
        return rowdict(row)


def update_odometer_reading(reading_id: int, payload: dict) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM odometer_readings WHERE id=? AND deleted_at IS NULL", (reading_id,)).fetchone()
        if not existing or str(payload.get("trip_id")) != str(existing["trip_id"]):
            raise ValueError("里程记录不存在或不属于目标行程")
        validate_record_client_id(existing, payload)
        values = prepare_odometer_reading(db, payload, existing)
        state = revision_state(existing, payload)
        reading_fields = ("trip_id", "occurred_at", "odometer", "location", "note", "source", "occurred_at_explicit")
        if state == "replay":
            if values_equal(existing, values, reading_fields):
                return rowdict(existing)
            raise ValueError("同一客户端修订号内容不同，无法覆盖已有修改")
        revision_snapshot(db, "odometer_reading", existing, "update")
        version, stamp = int(existing["version"]) + 1, now_text()
        client_revision = int(payload.get("client_revision") or existing["client_revision"] + 1)
        db.execute("""UPDATE odometer_readings SET occurred_at=?,odometer=?,location=?,note=?,source=?,occurred_at_explicit=?,
            client_revision=?,version=?,updated_at=? WHERE id=?""", (
                values["occurred_at"], values["odometer"], values["location"], values["note"], values["source"],
                values["occurred_at_explicit"], client_revision, version, stamp, reading_id))
        row = db.execute("SELECT * FROM odometer_readings WHERE id=?", (reading_id,)).fetchone()
        revision_snapshot(db, "odometer_reading", row, "update")
        return rowdict(row)


def delete_odometer_reading(reading_id: int, payload: dict) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM odometer_readings WHERE id=?", (reading_id,)).fetchone()
        if not row or str(payload.get("trip_id")) != str(row["trip_id"]):
            raise ValueError("里程记录不存在或不属于目标行程")
        validate_record_client_id(row, payload)
        if row["deleted_at"] is not None:
            if ensure_client_revision(row, payload):
                return {"deleted": True, "idempotent": True, "reading_id": int(reading_id)}
            raise ValueError("该里程记录已经在回收站")
        trip = db.execute("SELECT id FROM trips WHERE id=? AND status='active'", (row["trip_id"],)).fetchone()
        if not trip: raise ValueError("只能删除进行中行程的里程记录")
        if revision_state(row, payload) == "replay":
            raise ValueError("同一客户端修订号已对应当前里程记录，删除操作冲突")
        stamp = now_text(); expiry = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        revision_snapshot(db, "odometer_reading", row, "delete")
        db.execute("UPDATE odometer_readings SET deleted_at=?,delete_expires_at=?,updated_at=?,version=version+1,client_revision=? WHERE id=?", (
            stamp, expiry, stamp, int(payload.get("client_revision") or row["client_revision"] + 1), reading_id))
        revision_snapshot(db, "odometer_reading", db.execute("SELECT * FROM odometer_readings WHERE id=?", (reading_id,)).fetchone(), "delete")
        return {"deleted": True, "reading_id": int(reading_id), "trip_id": row["trip_id"], "delete_expires_at": expiry}


def restore_record(record_type: str, record_id: int, payload: dict) -> dict:
    table = "entries" if record_type == "entry" else "odometer_readings"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(f"SELECT * FROM {table} WHERE id=? AND deleted_at IS NOT NULL", (record_id,)).fetchone()
        if not row or str(payload.get("trip_id")) != str(row["trip_id"]):
            raise ValueError("回收站记录不存在或不属于目标行程")
        validate_record_client_id(row, payload)
        if row["delete_expires_at"] and row["delete_expires_at"] < now_text():
            raise RecordGoneError("该回收站记录已超过30天恢复期限")
        trip = db.execute("SELECT * FROM trips WHERE id=? AND status='active'", (row["trip_id"],)).fetchone()
        if not trip: raise ValueError("只能恢复到进行中的行程")
        if record_type == "entry" and row["odometer"] is not None:
            validate_odometer_point(db, trip, row["occurred_at"], float(row["odometer"]), exclude_entry_id=record_id)
        if record_type == "odometer_reading":
            validate_odometer_point(db, trip, row["occurred_at"], float(row["odometer"]), exclude_reading_id=record_id)
        if revision_state(row, payload) == "replay":
            raise ValueError("同一客户端修订号已对应回收站状态，恢复操作冲突")
        stamp = now_text()
        revision_snapshot(db, record_type, row, "restore")
        db.execute(f"UPDATE {table} SET deleted_at=NULL,delete_expires_at=NULL,updated_at=?,version=version+1,client_revision=? WHERE id=?", (
            stamp, int(payload.get("client_revision") or row["client_revision"] + 1), record_id))
        restored = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        revision_snapshot(db, record_type, restored, "restore")
        return rowdict(restored)


def restore_and_update_record(record_type: str, record_id: int, payload: dict) -> dict:
    """离线 upsert 命中回收站记录时，在一个事务内恢复并应用新内容。

    这个场景会出现在：服务端已接受旧版本后响应丢失，手机又把同一条
    记录删除、撤销或修改。只清除 deleted_at 会静默丢掉手机上的新内容。
    """
    table = "entries" if record_type == "entry" else "odometer_readings"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(f"SELECT * FROM {table} WHERE id=? AND deleted_at IS NOT NULL", (record_id,)).fetchone()
        if not row or str(payload.get("trip_id")) != str(row["trip_id"]):
            raise ValueError("回收站记录不存在或不属于目标行程")
        validate_record_client_id(row, payload)
        if row["delete_expires_at"] and row["delete_expires_at"] < now_text():
            raise RecordGoneError("该回收站记录已超过30天恢复期限")
        if revision_state(row, payload) == "replay":
            raise ValueError("同一客户端修订号已对应回收站状态，恢复修改操作冲突")

        revision_snapshot(db, record_type, row, "restore")
        stamp = now_text()
        next_version = int(row["version"]) + 1
        next_revision = int(payload["client_revision"])
        if record_type == "entry":
            values, checked = prepare_entry(
                db, payload, exclude_entry_id=record_id, fallback_raw_text=row["raw_text"])
            db.execute("""UPDATE entries SET
              occurred_at=?,category=?,category_label=?,amount=?,amount_cents=?,status=?,location=?,note=?,raw_text=?,
              fuel_grade=?,fuel_liters=?,fuel_unit_price=?,fuel_calculated_amount_cents=?,fuel_discount_cents=?,odometer=?,full_tank=?,people=?,nights=?,
              latitude=?,longitude=?,gps_accuracy=?,region=?,occurred_at_explicit=?,
              deleted_at=NULL,delete_expires_at=NULL,client_revision=?,version=?,updated_at=?
              WHERE id=? AND trip_id=?""", (
                values["occurred_at"], values["category"], values["category_label"], values["amount"],
                values["amount_cents"], values["status"], values["location"], values["note"], values["raw_text"], values["fuel_grade"],
                values["fuel_liters"], values["fuel_unit_price"], values["fuel_calculated_amount_cents"], values["fuel_discount_cents"], values["odometer"],
                values["full_tank"], values["people"], values["nights"], values["latitude"],
                values["longitude"], values["gps_accuracy"], values["region"], values["occurred_at_explicit"],
                next_revision, next_version, stamp, record_id, values["trip_id"]))
            restored = db.execute("SELECT * FROM entries WHERE id=?", (record_id,)).fetchone()
            revision_snapshot(db, record_type, restored, "restore")
            result = rowdict(restored)
            result["validation"] = checked
            return result

        values = prepare_odometer_reading(db, payload, row)
        db.execute("""UPDATE odometer_readings SET
          occurred_at=?,odometer=?,location=?,note=?,source=?,occurred_at_explicit=?,
          deleted_at=NULL,delete_expires_at=NULL,client_revision=?,version=?,updated_at=?
          WHERE id=? AND trip_id=?""", (
            values["occurred_at"], values["odometer"], values["location"], values["note"],
            values["source"], values["occurred_at_explicit"], next_revision, next_version, stamp,
            record_id, values["trip_id"]))
        restored = db.execute("SELECT * FROM odometer_readings WHERE id=?", (record_id,)).fetchone()
        revision_snapshot(db, record_type, restored, "restore")
        return rowdict(restored)


def record_revisions(record_type: str, record_id: int) -> list[dict]:
    with connect() as db:
        return [rowdict(row) for row in db.execute(
            "SELECT * FROM record_revisions WHERE record_type=? AND record_id=? ORDER BY version,id", (record_type, record_id)).fetchall()]


def trash(trip_id: int) -> dict:
    with connect() as db:
        entries = db.execute("""SELECT *, 'entry' AS record_type FROM entries
            WHERE trip_id=? AND deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?)
            ORDER BY deleted_at DESC,id DESC""", (trip_id, now_text())).fetchall()
        readings = db.execute("""SELECT *, 'odometer_reading' AS record_type FROM odometer_readings
            WHERE trip_id=? AND deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?)
            ORDER BY deleted_at DESC,id DESC""", (trip_id, now_text())).fetchall()
    return {"entries": [rowdict(r) for r in entries], "odometer_readings": [rowdict(r) for r in readings],
            "records": sorted([rowdict(r) for r in entries] + [rowdict(r) for r in readings], key=lambda r: (r["deleted_at"], r["id"]), reverse=True)}


def create_trip(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    origin = str(payload.get("origin") or "").strip()
    if not name or not origin:
        raise ValueError("行程名称和出发地必填")
    start_odo = payload.get("start_odometer")
    if start_odo is None or isinstance(start_odo, bool):
        raise ValueError("出发里程必填；出发油箱默认按已加满记录")
    try:
        start_odo = float(start_odo)
    except (TypeError, ValueError) as exc:
        raise ValueError("出发里程必须是有效数字") from exc
    if not math.isfinite(start_odo) or start_odo < 0:
        raise ValueError("出发里程必须是大于等于0的有限数字")
    started_at = normalize_local_datetime(payload.get("started_at") or datetime.now().isoformat(timespec="seconds"))
    departure_date = parse_travel_date(payload.get("departure_date") or started_at[:10])
    planned_days = payload.get("planned_days")
    if planned_days in (None, ""):
        planned_days = None
    else:
        try:
            planned_days = int(planned_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("计划天数必须是正整数") from exc
        if not 1 <= planned_days <= 365:
            raise ValueError("计划天数必须在1到365之间")
    with connect() as db:
        if db.execute("SELECT 1 FROM trips WHERE status='active'").fetchone():
            raise ValueError("请先结束当前行程")
        cur = db.execute("""INSERT INTO trips
            (name,origin,destination,started_at,start_odometer,start_full_tank,departure_date,planned_days,status)
            VALUES (?,?,?,?,?,1,?,?, 'active')""", (
                name, origin, payload.get("destination"), started_at, start_odo, departure_date, planned_days))
        return rowdict(db.execute("SELECT * FROM trips WHERE id=?", (cur.lastrowid,)).fetchone())


def update_trip_start_odometer(trip_id: int, payload: dict) -> dict:
    """修改进行中行程的基础信息。

    保留旧版只传 ``start_odometer`` 的 PUT 请求，同时允许新版
    修改标题、起终点。前端通常只提交实际变更的字段。
    """
    try:
        trip_id = int(trip_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("行程编号无效") from exc
    if not isinstance(payload, dict):
        raise ValueError("行程修改内容无效")
    has_name = "name" in payload
    has_start_odometer = "start_odometer" in payload
    has_origin = "origin" in payload
    has_destination = "destination" in payload
    has_departure_date = "departure_date" in payload
    has_planned_days = "planned_days" in payload
    if not any((has_name, has_start_odometer, has_origin, has_destination, has_departure_date, has_planned_days)):
        raise ValueError("请提供要修改的行程信息")
    name = None
    if has_name:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("行程标题必填")
    start_odometer = None
    if has_start_odometer:
        value = payload.get("start_odometer")
        if isinstance(value, bool):
            raise ValueError("出发里程必须是有效数字")
        try:
            start_odometer = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("出发里程必须是有效数字") from exc
        if not math.isfinite(start_odometer) or start_odometer < 0:
            raise ValueError("出发里程必须是大于等于0的有限数字")
    origin = None
    if has_origin:
        origin = str(payload.get("origin") or "").strip()
        if not origin:
            raise ValueError("出发地必填")
    destination = None
    if has_destination:
        destination = str(payload.get("destination") or "").strip() or None
    departure_date = parse_travel_date(payload.get("departure_date")) if has_departure_date else None
    planned_days = None
    if has_planned_days:
        raw_days = payload.get("planned_days")
        if raw_days in (None, ""):
            planned_days = None
        else:
            try:
                planned_days = int(raw_days)
            except (TypeError, ValueError) as exc:
                raise ValueError("计划天数必须是正整数") from exc
            if not 1 <= planned_days <= 365:
                raise ValueError("计划天数必须在1到365之间")
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("行程不存在")
        if trip["status"] != "active":
            raise ValueError("只能修改进行中的行程信息")
        if has_start_odometer:
            points = all_odometer_points(db, trip_id)
            earliest = min((float(point["odometer"]) for point in points), default=None)
            if earliest is not None and start_odometer > earliest:
                raise ValueError("出发里程不能大于已记录的最早里程")
        assignments, values = [], []
        if has_name:
            assignments.append("name=?"); values.append(name)
        if has_start_odometer:
            assignments.append("start_odometer=?"); values.append(start_odometer)
        if has_origin:
            assignments.append("origin=?"); values.append(origin)
        if has_destination:
            assignments.append("destination=?"); values.append(destination)
        if has_departure_date:
            assignments.append("departure_date=?"); values.append(departure_date)
        if has_planned_days:
            assignments.append("planned_days=?"); values.append(planned_days)
        values.append(trip_id)
        db.execute(f"UPDATE trips SET {','.join(assignments)} WHERE id=? AND status='active'", values)
        return rowdict(db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def permanently_delete_record(record_type: str, record_id: int, payload: dict) -> dict:
    """彻底删除回收站内的单条记录及它的全部修订历史。"""
    if record_type not in {"entry", "odometer_reading"}:
        raise ValueError("回收站记录类型无效")
    if not isinstance(payload, dict) or payload.get("trip_id") is None:
        raise ValueError("永久删除时必须指定目标行程")
    try:
        trip_id = int(payload["trip_id"])
        record_id = int(record_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("行程或记录编号无效") from exc
    table = "entries" if record_type == "entry" else "odometer_readings"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        record = db.execute(
            f"SELECT id,trip_id,client_id,client_revision,deleted_at FROM {table} WHERE id=? AND trip_id=?",
            (record_id, trip_id),
        ).fetchone()
        if not record:
            purged = find_purged_record(
                db, record_type, client_id=payload.get("client_id"),
                record_id=record_id, trip_id=trip_id)
            if purged:
                if int(purged["trip_id"]) != trip_id:
                    raise ValueError("永久删除墓碑不属于目标行程")
                return {"permanently_deleted": True, "idempotent": True,
                        "record_type": record_type,
                        "record_id": int(purged["record_id"]) if purged["record_id"] is not None else record_id,
                        "trip_id": int(purged["trip_id"]), "revisions_deleted": 0,
                        "purged_at": purged["purged_at"]}
            raise ValueError("回收站记录不存在或不属于目标行程")
        if record["deleted_at"] is None:
            raise ValueError("只能永久删除已在回收站的记录")
        supplied_client_id = str(payload.get("client_id") or "").strip() or None
        if supplied_client_id and record["client_id"] and supplied_client_id != record["client_id"]:
            raise ValueError("客户端编号与回收站记录不匹配")
        tombstone_client_id = record["client_id"] or supplied_client_id
        purged_at = now_text()
        tombstone_revision = max(int(record["client_revision"] or 1),
                                 int(payload.get("client_revision") or 1))
        prior_tombstone = find_purged_record(
            db, record_type, client_id=tombstone_client_id) if tombstone_client_id else None
        if prior_tombstone:
            if int(prior_tombstone["trip_id"]) != trip_id:
                raise ValueError("该客户端编号已属于另一行程的永久删除记录")
            db.execute("""UPDATE purged_records SET
                record_id=COALESCE(record_id,?),client_revision=?,purged_at=? WHERE id=?""", (
                    record_id, max(int(prior_tombstone["client_revision"]), tombstone_revision),
                    purged_at, prior_tombstone["id"]))
        else:
            db.execute("""INSERT INTO purged_records
                (record_type,record_id,trip_id,client_id,client_revision,purged_at)
                VALUES (?,?,?,?,?,?)""", (
                    record_type, record_id, trip_id, tombstone_client_id,
                    tombstone_revision, purged_at))
        revisions_deleted = db.execute(
            "SELECT COUNT(*) AS count FROM record_revisions WHERE record_type=? AND record_id=? AND trip_id=?",
            (record_type, record_id, trip_id),
        ).fetchone()["count"]
        db.execute(
            "DELETE FROM record_revisions WHERE record_type=? AND record_id=? AND trip_id=?",
            (record_type, record_id, trip_id),
        )
        deleted = db.execute(
            f"DELETE FROM {table} WHERE id=? AND trip_id=? AND deleted_at IS NOT NULL",
            (record_id, trip_id),
        ).rowcount
        if deleted != 1:
            raise ValueError("回收站记录状态已变更，请刷新后重试")
        return {"permanently_deleted": True, "record_type": record_type,
                "record_id": record_id, "trip_id": trip_id,
                "revisions_deleted": int(revisions_deleted), "purged_at": purged_at}


def permanently_purge_client(payload: dict) -> dict:
    """按本机编号建立永久墓碑，用于无 server id 的响应丢失场景。"""
    if not isinstance(payload, dict):
        raise ValueError("永久删除内容无效")
    entity = payload.get("entity")
    if entity not in {"entry", "odometer"}:
        raise ValueError("永久删除记录类型无效")
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("永久删除本机记录时必须提供客户端编号")
    try:
        trip_id = int(payload["trip_id"])
        client_revision = int(payload.get("client_revision") or 1)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("行程或客户端修订号无效") from exc
    if client_revision < 1:
        raise ValueError("客户端修订号必须大于0")
    record_type = "entry" if entity == "entry" else "odometer_reading"
    table = "entries" if entity == "entry" else "odometer_readings"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        purged = find_purged_record(db, record_type, client_id=client_id)
        if purged:
            if int(purged["trip_id"]) != trip_id:
                raise ValueError("该客户端编号已属于另一行程的永久删除记录")
            acknowledged_revision = max(int(purged["client_revision"]), client_revision)
            db.execute("UPDATE purged_records SET client_revision=? WHERE id=?",
                       (acknowledged_revision, purged["id"]))
            return {"permanently_deleted": True, "idempotent": True,
                    "record_type": record_type, "record_id": purged["record_id"],
                    "trip_id": trip_id, "client_id": client_id,
                    "client_revision": acknowledged_revision,
                    "revisions_deleted": 0, "purged_at": purged["purged_at"]}
        record = db.execute(f"SELECT * FROM {table} WHERE client_id=?", (client_id,)).fetchone()
        if record and int(record["trip_id"]) != trip_id:
            raise ValueError("该客户端编号已属于另一行程的记录")
        if record and record["deleted_at"] is None:
            raise RecordConflictError(
                "服务器记录尚未进入回收站，请先完成软删除后重试",
                entity=entity, record_id=int(record["id"]), trip_id=trip_id,
                client_id=client_id, client_revision=int(record["client_revision"] or 1))
        record_id = int(record["id"]) if record else None
        revision = max(int(record["client_revision"] or 1) if record else 1, client_revision)
        purged_at = now_text()
        db.execute("""INSERT INTO purged_records
            (record_type,record_id,trip_id,client_id,client_revision,purged_at)
            VALUES (?,?,?,?,?,?)""", (
                record_type, record_id, trip_id, client_id, revision, purged_at))
        revisions_deleted = 0
        if record:
            revisions_deleted = db.execute(
                "SELECT COUNT(*) AS count FROM record_revisions WHERE record_type=? AND record_id=? AND trip_id=?",
                (record_type, record_id, trip_id),
            ).fetchone()["count"]
            db.execute(
                "DELETE FROM record_revisions WHERE record_type=? AND record_id=? AND trip_id=?",
                (record_type, record_id, trip_id),
            )
            db.execute(f"DELETE FROM {table} WHERE id=? AND trip_id=?", (record_id, trip_id))
        return {"permanently_deleted": True, "record_type": record_type,
                "record_id": record_id, "trip_id": trip_id, "client_id": client_id,
                "client_revision": revision, "revisions_deleted": int(revisions_deleted),
                "purged_at": purged_at}


def prepare_entry(db, payload: dict, exclude_entry_id=None, fallback_raw_text="") -> tuple[dict, dict | None]:
    """新增和修改共用的入账校验与字段规范化。"""
    try:
        trip_id = int(payload.get("trip_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("行程信息无效") from exc
    raw_text = str(payload.get("raw_text") or fallback_raw_text or payload.get("note") or "").strip()
    fields = payload.get("recognized") if isinstance(payload.get("recognized"), dict) else payload
    checked = parse_text(raw_text) if raw_text else None
    if raw_text and UNSUPPORTED_FLOW_RE.search(raw_text):
        raise ValueError("MVP 尚不支持退款和押金，不能当作普通消费入账")
    if checked and not checked["can_save"]:
        required = [x["label"] for x in checked["missing"] if x["level"] == "required"]
        # 允许界面补齐普通字段，但油号冲突/非法必须先在原文中消解。
        if "确认油号" in required:
            raise ValueError("请先确认油号，只能选95号或98号")
        if "拆分多笔消费" in required:
            raise ValueError("一句话包含多笔消费，请拆分后分别保存")
    # 旧单笔接口不能悄悄只存 records[0]；新版前端会把安全拆分结果逐条提交。
    if checked and len(checked.get("records", [])) > 1:
        raise ValueError("一句话包含多笔消费，请拆分后分别保存")
    category = fields.get("category")
    amount = fields.get("amount")
    if not category or category not in CATEGORIES or amount is None:
        raise ValueError("消费类别和金额必填")
    amount_cents = to_cents(amount, "实付金额")
    amount = cents_to_amount(amount_cents)

    fuel_liters = fields.get("fuel_liters") if category == "fuel" else None
    if fuel_liters is not None:
        try:
            fuel_liters = float(fuel_liters)
        except (TypeError, ValueError) as exc:
            raise ValueError("加油升数必须是有效数字") from exc
        if not math.isfinite(fuel_liters) or fuel_liters <= 0:
            raise ValueError("加油升数必须大于0")
    # 合法优惠会造成实付/升数与挂牌油价不同；三者需分别保真保存，
    # 不把这种差额误判为输入冲突。
    has_supplied_unit_price = category == "fuel" and "fuel_unit_price" in fields
    supplied_unit_price = fields.get("fuel_unit_price") if has_supplied_unit_price else None
    # ``null`` 是用户明确清空挂牌价，不能又从旧原文回填；只有旧客户端完全
    # 缺这个字段时才兼容解析结果。
    if not has_supplied_unit_price and checked and "fuel_unit_price" not in checked.get("derived_fields", []):
        supplied_unit_price = checked["recognized"].get("fuel_unit_price")
    if supplied_unit_price not in (None, ""):
        try:
            fuel_unit_price = float(supplied_unit_price)
        except (TypeError, ValueError) as exc:
            raise ValueError("油价必须是有效数字") from exc
        if not math.isfinite(fuel_unit_price) or fuel_unit_price <= 0:
            raise ValueError("油价必须大于0")
        fuel_unit_price = round(fuel_unit_price, 3)
    else:
        # 没有明确挂牌价时，实付÷升数仅用于界面提示，绝不伪装成挂牌价持久化。
        fuel_unit_price = None
    fuel_calculated_amount_cents = (
        to_cents(fuel_liters * fuel_unit_price, "计算金额")
        if category == "fuel" and fuel_liters and fuel_unit_price else None
    )
    fuel_discount_cents = (
        fuel_calculated_amount_cents - amount_cents
        if fuel_calculated_amount_cents is not None else None
    )
    fuel_grade = (fields.get("fuel_grade") or 95) if category == "fuel" else None
    if category == "fuel":
        try:
            fuel_grade = int(fuel_grade)
        except (TypeError, ValueError) as exc:
            raise ValueError("油号只能选95号或98号") from exc
        if fuel_grade not in {95, 98}:
            raise ValueError("油号只能选95号或98号")
    full_tank = fields.get("full_tank")
    if category != "fuel":
        full_tank = None
    elif full_tank is not None and not isinstance(full_tank, bool):
        if str(full_tank).lower() in {"1", "true", "yes", "是"}:
            full_tank = True
        elif str(full_tank).lower() in {"0", "false", "no", "否"}:
            full_tank = False
        else:
            raise ValueError("是否加满只能是是、否或留空")
    status = str(fields.get("status") or "confirmed").strip().lower()
    if status not in ENTRY_STATUSES:
        raise ValueError("记录状态无效")

    trip = db.execute("SELECT * FROM trips WHERE id=? AND status='active'", (trip_id,)).fetchone()
    if not trip:
        raise ValueError("请先开始一个进行中的行程；已结束行程不能新增或修改记录")
    explicit_time = fields.get("occurred_at") is not None and not payload.get("_preserve_occurred_at")
    occurred_at = normalize_local_datetime(
        fields.get("occurred_at") or datetime.now().isoformat(timespec="seconds"))
    odo = fields.get("odometer") if category == "fuel" else None
    if odo is not None:
        try:
            odo = float(odo)
        except (TypeError, ValueError) as exc:
            raise ValueError("里程表读数必须是有效数字") from exc
        if not math.isfinite(odo) or odo < 0:
            raise ValueError("里程表读数不能小于0")
        validate_odometer_point(db, trip, occurred_at, odo, explicit_time=explicit_time, exclude_entry_id=exclude_entry_id)
    location = str(fields.get("location") or "").strip() or None
    region = str(fields.get("region") or "").strip() or None
    latitude = fields.get("latitude")
    longitude = fields.get("longitude")
    gps_accuracy = fields.get("gps_accuracy")
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            raise ValueError("GPS 经纬度必须同时提供")
        latitude, longitude = float(latitude), float(longitude)
        if (not math.isfinite(latitude) or not math.isfinite(longitude) or
                not (-90 <= latitude <= 90 and -180 <= longitude <= 180)):
            raise ValueError("GPS 经纬度超出有效范围")
    if gps_accuracy is not None:
        gps_accuracy = float(gps_accuracy)
        if not math.isfinite(gps_accuracy) or gps_accuracy < 0:
            raise ValueError("GPS 精度不能为负数")
    note = str(fields.get("item") or fields.get("note") or "").strip() or None
    duplicate = db.execute("""SELECT id FROM entries
        WHERE trip_id=? AND category=? AND amount=?
          AND deleted_at IS NULL
          AND COALESCE(location,'')=COALESCE(?,'')
          AND COALESCE(odometer,-1)=COALESCE(?,-1)
          AND ABS(strftime('%s', occurred_at)-strftime('%s', ?)) <= 180
          AND (? IS NULL OR id<>?)
        ORDER BY id DESC LIMIT 1""", (
            trip_id, category, amount, location, odo, occurred_at,
            exclude_entry_id, exclude_entry_id)).fetchone()
    if duplicate and not payload.get("confirm_duplicate"):
        raise ValueError(f"可能与最近记录重复（记录#{duplicate['id']}），请确认后再保存")
    return {
        "trip_id": trip_id, "occurred_at": occurred_at, "category": category,
        "category_label": CATEGORIES[category][0], "amount": amount, "amount_cents": amount_cents,
        "location": location, "note": note, "raw_text": raw_text,
        "fuel_grade": fuel_grade, "fuel_liters": fuel_liters,
        "fuel_unit_price": fuel_unit_price,
        "fuel_calculated_amount_cents": fuel_calculated_amount_cents,
        "fuel_discount_cents": fuel_discount_cents,
        "status": status, "odometer": odo,
        "full_tank": None if full_tank is None else int(bool(full_tank)),
        "people": fields.get("people"), "nights": fields.get("nights"),
        "latitude": latitude, "longitude": longitude,
        "gps_accuracy": gps_accuracy, "region": region, "occurred_at_explicit": int(explicit_time),
    }, checked


def save_entry(payload: dict) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        client_id = str(payload.get("client_id") or "").strip() or None
        reject_purged_client_id(db, "entry", client_id)
        if client_id:
            existing = db.execute("SELECT * FROM entries WHERE client_id=?", (client_id,)).fetchone()
            if existing:
                if payload.get("client_revision") is None or ensure_client_revision(existing, payload):
                    result = rowdict(existing)
                    result["validation"] = {"idempotent": True}
                    return result
                raise ValueError("该客户端编号已经用于另一条消费记录")
        values, checked = prepare_entry(db, payload)
        stamp = now_text()
        client_revision = int(payload.get("client_revision") or 1)
        record_id = next_record_id(db, "entries", "entry")
        db.execute("""INSERT INTO entries
          (id,trip_id,occurred_at,category,category_label,amount,amount_cents,status,location,note,raw_text,
           fuel_grade,fuel_liters,fuel_unit_price,fuel_calculated_amount_cents,fuel_discount_cents,odometer,full_tank,people,nights,created_at,
           latitude,longitude,gps_accuracy,region,client_id,client_revision,version,updated_at,occurred_at_explicit)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            record_id, values["trip_id"], values["occurred_at"], values["category"], values["category_label"],
            values["amount"], values["amount_cents"], values["status"], values["location"], values["note"], values["raw_text"],
            values["fuel_grade"], values["fuel_liters"], values["fuel_unit_price"], values["fuel_calculated_amount_cents"], values["fuel_discount_cents"],
            values["odometer"], values["full_tank"], values["people"], values["nights"],
            stamp, values["latitude"], values["longitude"], values["gps_accuracy"], values["region"],
            client_id, client_revision, 1, stamp, values["occurred_at_explicit"]))
        row = db.execute("SELECT * FROM entries WHERE id=?", (record_id,)).fetchone()
        revision_snapshot(db, "entry", row, "create")
        result = rowdict(row)
    result["validation"] = checked
    return result


def update_entry(entry_id: int, payload: dict) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM entries WHERE id=? AND trip_id=? AND deleted_at IS NULL",
            (entry_id, payload.get("trip_id")),
        ).fetchone()
        if not existing:
            raise ValueError("消费记录不存在或不属于目标行程")
        validate_record_client_id(existing, payload)
        # 同一离线操作因响应丢失而重放时，未提供发生时间不能被“现在”替换；
        # 保留原时间才能稳定比较同一个 client_revision。
        submitted_fields = payload.get("recognized") if isinstance(payload.get("recognized"), dict) else payload
        if "occurred_at" not in submitted_fields or submitted_fields.get("occurred_at") in (None, ""):
            payload = dict(payload)
            payload["_preserve_occurred_at"] = True
            if isinstance(payload.get("recognized"), dict):
                payload["recognized"] = {**payload["recognized"], "occurred_at": existing["occurred_at"]}
            else:
                payload["occurred_at"] = existing["occurred_at"]
        values, checked = prepare_entry(
            db, payload, exclude_entry_id=entry_id, fallback_raw_text=existing["raw_text"])
        state = revision_state(existing, payload)
        entry_fields = ("trip_id", "occurred_at", "category", "category_label", "amount", "amount_cents", "status", "location", "note", "raw_text",
                        "fuel_grade", "fuel_liters", "fuel_unit_price", "fuel_calculated_amount_cents", "fuel_discount_cents", "odometer", "full_tank", "people", "nights",
                        "latitude", "longitude", "gps_accuracy", "region", "occurred_at_explicit")
        if state == "replay":
            if values_equal(existing, values, entry_fields):
                result = rowdict(existing); result["validation"] = checked; return result
            raise ValueError("同一客户端修订号内容不同，无法覆盖已有修改")
        revision_snapshot(db, "entry", existing, "update")
        db.execute("""UPDATE entries SET
          occurred_at=?,category=?,category_label=?,amount=?,amount_cents=?,status=?,location=?,note=?,raw_text=?,
          fuel_grade=?,fuel_liters=?,fuel_unit_price=?,fuel_calculated_amount_cents=?,fuel_discount_cents=?,odometer=?,full_tank=?,people=?,nights=?,
          latitude=?,longitude=?,gps_accuracy=?,region=?,occurred_at_explicit=?,client_revision=?,version=?,updated_at=?
          WHERE id=? AND trip_id=?""", (
            values["occurred_at"], values["category"], values["category_label"], values["amount"],
            values["amount_cents"], values["status"], values["location"], values["note"], values["raw_text"], values["fuel_grade"],
            values["fuel_liters"], values["fuel_unit_price"], values["fuel_calculated_amount_cents"], values["fuel_discount_cents"], values["odometer"],
            values["full_tank"], values["people"], values["nights"], values["latitude"],
            values["longitude"], values["gps_accuracy"], values["region"], values["occurred_at_explicit"],
            int(payload.get("client_revision") or existing["client_revision"] + 1), int(existing["version"]) + 1,
            now_text(), entry_id, values["trip_id"]))
        row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        revision_snapshot(db, "entry", row, "update")
        result = rowdict(row)
    result["validation"] = checked
    return result


def delete_entry(entry_id: int, payload: dict) -> dict:
    trip_id = payload.get("trip_id") if isinstance(payload, dict) else None
    if trip_id is None:
        raise ValueError("删除消费记录时必须指定目标行程")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM entries WHERE id=? AND trip_id=?", (entry_id, trip_id)).fetchone()
        if not existing:
            raise ValueError("消费记录不存在或不属于目标行程")
        validate_record_client_id(existing, payload)
        if existing["deleted_at"] is not None:
            if ensure_client_revision(existing, payload):
                return {"deleted": True, "idempotent": True, "entry_id": int(entry_id), "trip_id": existing["trip_id"]}
            raise ValueError("该消费记录已经在回收站")
        trip = db.execute(
            "SELECT id FROM trips WHERE id=? AND status='active'", (existing["trip_id"],)).fetchone()
        if not trip:
            raise ValueError("只能删除进行中行程的消费记录")
        if revision_state(existing, payload) == "replay":
            raise ValueError("同一客户端修订号已对应当前消费记录，删除操作冲突")
        stamp = now_text(); expiry = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        revision_snapshot(db, "entry", existing, "delete")
        db.execute("""UPDATE entries SET deleted_at=?,delete_expires_at=?,updated_at=?,version=version+1,
            client_revision=? WHERE id=? AND trip_id=?""", (
            stamp, expiry, stamp, int(payload.get("client_revision") or existing["client_revision"] + 1),
            entry_id, existing["trip_id"]))
        revision_snapshot(db, "entry", db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone(), "delete")
        return {"deleted": True, "entry_id": int(entry_id), "trip_id": existing["trip_id"], "delete_expires_at": expiry}


def finish_trip(trip_id: int, payload: dict) -> dict:
    end_odo = payload.get("end_odometer")
    if end_odo is None or isinstance(end_odo, bool):
        raise ValueError("结束里程必填")
    try:
        end_odo = float(end_odo)
    except (TypeError, ValueError) as exc:
        raise ValueError("结束里程必须是有效数字") from exc
    if not math.isfinite(end_odo) or end_odo < 0:
        raise ValueError("结束里程必须是大于等于0的有限数字")
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=? AND status='active'", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("未找到进行中的行程")
        if trip["start_odometer"] is not None and end_odo < float(trip["start_odometer"]):
            raise ValueError("结束里程不能小于出发里程")
        points = all_odometer_points(db, trip_id)
        last_odo = max((point["odometer"] for point in points), default=None)
        if last_odo is not None and end_odo < float(last_odo):
            raise ValueError("结束里程不能小于本行程最后记录的里程")
        db.execute("UPDATE trips SET end_odometer=?, ended_at=?, status='finished' WHERE id=?", (
            end_odo, payload.get("ended_at") or datetime.now().isoformat(timespec="seconds"), trip_id))
        return rowdict(db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def reopen_trip(trip_id: int) -> dict:
    """恢复误结束的最近行程；已有其他进行中行程时禁止恢复。"""
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("行程不存在")
        if trip["status"] == "active":
            return rowdict(trip)
        if db.execute("SELECT 1 FROM trips WHERE status='active' AND id<>?", (trip_id,)).fetchone():
            raise ValueError("已有进行中的行程，不能同时恢复另一段行程")
        db.execute("UPDATE trips SET status='active',ended_at=NULL,end_odometer=NULL WHERE id=?", (trip_id,))
        return rowdict(db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def parse_travel_date(value) -> str:
    """只接受本地日程的自然日期，避免把 UTC 偏移写进 Day 归属。"""
    raw = str(value or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("行程日期请使用年-月-日") from exc


def date_from_timestamp(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw[:10]) else None


def trip_departure_date(trip) -> str | None:
    """Day 的唯一锚点是用户可编辑的出发日期，而非创建行程的时间。"""
    return date_from_timestamp(trip["departure_date"]) or date_from_timestamp(trip["started_at"])


def day_number_for(trip, travel_date: str) -> int:
    start = trip_departure_date(trip)
    if not start:
        return 1
    try:
        delta = (datetime.strptime(travel_date, "%Y-%m-%d").date() -
                 datetime.strptime(start, "%Y-%m-%d").date()).days
    except ValueError:
        return 1
    return max(1, delta + 1)


def build_trip_days(trip, entries, readings, day_metadata) -> list[dict]:
    """把路线元数据与既有账务按发生日期组合成 Day 视图。

    消费和里程不存 day_id，因此修改记录时间时会自动改变归属，旧数据也无需迁移。
    """
    entries = [rowdict(row) if isinstance(row, sqlite3.Row) else dict(row) for row in entries]
    readings = [rowdict(row) if isinstance(row, sqlite3.Row) else dict(row) for row in readings]
    metadata = [rowdict(row) if isinstance(row, sqlite3.Row) else dict(row) for row in day_metadata]
    meta_by_date = {row["travel_date"]: row for row in metadata}
    dates = set(meta_by_date)
    start_date = trip_departure_date(trip)
    if start_date:
        # 即使当天尚未新增消费，也保留真实出发日，让出发里程归属稳定。
        dates.add(start_date)
    for row in entries + readings:
        day = date_from_timestamp(row.get("occurred_at"))
        if day:
            dates.add(day)
    if not dates:
        dates.add(trip_departure_date(trip) or datetime.now().date().isoformat())

    results = []
    for travel_date in sorted(dates):
        meta = meta_by_date.get(travel_date, {})
        day_entries = [row for row in entries if date_from_timestamp(row.get("occurred_at")) == travel_date]
        points = []
        for row in day_entries:
            if row.get("odometer") is not None:
                points.append(float(row["odometer"]))
        for row in readings:
            if date_from_timestamp(row.get("occurred_at")) == travel_date and row.get("odometer") is not None:
                points.append(float(row["odometer"]))
        if travel_date == start_date and trip["start_odometer"] is not None:
            points.append(float(trip["start_odometer"]))
        start_odometer = min(points) if points else None
        end_odometer = max(points) if points else None
        distance = round(end_odometer - start_odometer, 1) if len(points) >= 2 and end_odometer >= start_odometer else None
        number = day_number_for(trip, travel_date)
        origin = meta.get("origin") or (trip["origin"] if number == 1 else None)
        destination = meta.get("destination") or (trip["destination"] if travel_date == max(dates) else None)
        title = meta.get("title") or " → ".join(part for part in (origin, destination) if part) or f"Day {number}"
        results.append({
            "id": meta.get("id"), "trip_id": trip["id"], "day_number": number,
            "travel_date": travel_date, "title": title, "origin": origin,
            "destination": destination, "via": meta.get("via"), "note": meta.get("note"),
            "spend": cents_to_amount(sum(row_amount_cents(row) for row in day_entries)),
            "entry_count": len(day_entries), "start_odometer": start_odometer,
            "end_odometer": end_odometer, "distance_km": distance,
        })
    return results


def trip_days(trip_id: int) -> dict:
    try:
        trip_id = int(trip_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("行程编号无效") from exc
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("行程不存在")
        entries = db.execute("SELECT * FROM entries WHERE trip_id=? AND deleted_at IS NULL AND status='confirmed' ORDER BY occurred_at,id", (trip_id,)).fetchall()
        readings = reading_rows(db, trip_id)
        metadata = db.execute("SELECT * FROM trip_days WHERE trip_id=? ORDER BY travel_date,id", (trip_id,)).fetchall()
    days = build_trip_days(trip, entries, readings, metadata)
    return {"trip_id": trip_id, "days": days}


def day_text(payload: dict, field: str, limit=160) -> str | None:
    if field not in payload:
        return None
    value = str(payload.get(field) or "").strip()
    if len(value) > limit:
        raise ValueError(f"{field} 不能超过{limit}个字符")
    return value or None


def save_trip_day(trip_id: int, travel_date: str, payload: dict) -> dict:
    """以行程 + 日期为自然键新增或编辑路线说明，账务不随之改写。"""
    try:
        trip_id = int(trip_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("行程编号无效") from exc
    if not isinstance(payload, dict):
        raise ValueError("每日行程内容无效")
    travel_date = parse_travel_date(travel_date)
    fields = {name: day_text(payload, name) for name in ("title", "origin", "destination", "via", "note") if name in payload}
    if not fields:
        raise ValueError("请填写要保存的路线信息")
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("行程不存在")
        if trip["status"] != "active":
            raise ValueError("只能编辑进行中的行程路线")
        existing = db.execute("SELECT * FROM trip_days WHERE trip_id=? AND travel_date=?", (trip_id, travel_date)).fetchone()
        stamp = now_text()
        if existing:
            assignments, values = [], []
            for key, value in fields.items():
                assignments.append(f"{key}=?")
                values.append(value)
            assignments.extend(("version=version+1", "updated_at=?")); values.extend((stamp, existing["id"]))
            db.execute(f"UPDATE trip_days SET {','.join(assignments)} WHERE id=?", values)
        else:
            db.execute("""INSERT INTO trip_days
                (trip_id,travel_date,title,origin,destination,via,note,version,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,1,?,?)""", (
                    trip_id, travel_date, fields.get("title"), fields.get("origin"), fields.get("destination"),
                    fields.get("via"), fields.get("note"), stamp, stamp))
    return next(day for day in trip_days(trip_id)["days"] if day["travel_date"] == travel_date)


def dashboard(trip_id: int) -> dict:
    with connect() as db:
        trip = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError("行程不存在")
        rows = db.execute("SELECT * FROM entries WHERE trip_id=? AND deleted_at IS NULL AND status='confirmed' ORDER BY occurred_at,id", (trip_id,)).fetchall()
        manual_readings = reading_rows(db, trip_id)
        day_metadata = db.execute("SELECT * FROM trip_days WHERE trip_id=? ORDER BY travel_date,id", (trip_id,)).fetchall()
    total = cents_to_amount(sum(row_amount_cents(row) for row in rows))
    by_category = {}
    for r in rows:
        # 分类键是稳定统计口径；旧记录曾将 vehicle 写为“车辆异常”，因此
        # 仪表盘按当前键映射归并，避免同一类别拆成两个统计项。
        label = CATEGORIES.get(r["category"], (r["category_label"],))[0]
        by_category[label] = cents_to_amount(to_cents(by_category.get(label, 0)) + row_amount_cents(r))
    core_cash = cents_to_amount(sum(row_amount_cents(r) for r in rows if r["category"] in CORE_CATEGORIES))
    non_fuel_core = cents_to_amount(sum(row_amount_cents(r) for r in rows if r["category"] in CORE_CATEGORIES and r["category"] != "fuel"))
    points = [{**rowdict(row), "source": "fuel"} for row in rows if row["odometer"] is not None] + [rowdict(row) for row in manual_readings]
    points.sort(key=lambda point: (point["occurred_at"], 0 if point.get("source") == "fuel" else 1, point["id"]))
    metric_points = [point for point in points if point.get("occurred_at_explicit")] or points
    latest_point = metric_points[-1] if metric_points else None
    latest_odometer = latest_point["odometer"] if latest_point else None
    if trip["status"] == "finished" and trip["end_odometer"] is not None:
        latest_odometer = trip["end_odometer"]
        latest_point = {"odometer": latest_odometer, "occurred_at": trip["ended_at"], "source": "finish"}
    distance_boundary = trip["end_odometer"] if trip["end_odometer"] is not None else latest_odometer
    distance = None
    if trip["start_odometer"] is not None and distance_boundary is not None:
        candidate_distance = round(distance_boundary - trip["start_odometer"], 1)
        # 兼容升级前异常数据：绝不向页面输出负行驶里程或负成本。
        distance = candidate_distance if candidate_distance >= 0 else None

    # 仅以连续满箱区间计算油耗；缺少可靠边界时明确返回不可计算。
    fuels = [r for r in rows if r["category"] == "fuel"]
    fuel_cash = cents_to_amount(sum(row_amount_cents(r) for r in fuels))
    reliable_intervals, boundary, interval_contaminated = [], None, False
    if trip["start_full_tank"] and trip["start_odometer"] is not None:
        boundary = float(trip["start_odometer"])
    for row in fuels:
        # 任意未加满/未知加满或缺失里程、升数的加油都会破坏当前区间；
        # 下一次明确加满仅作为新的边界，不能倒推此前油耗。
        if row["full_tank"] != 1 or row["odometer"] is None or row["fuel_liters"] is None:
            interval_contaminated = True
            continue
        current = float(row["odometer"])
        if boundary is not None and not interval_contaminated and current > boundary:
            reliable_intervals.append({"entry_id": row["id"], "from_odometer": boundary,
                "to_odometer": current, "distance_km": round(current - boundary, 1),
                "liters": float(row["fuel_liters"])})
        boundary = current
        interval_contaminated = False
    reliable_distance = round(sum(i["distance_km"] for i in reliable_intervals), 1)
    reliable_liters = round(sum(i["liters"] for i in reliable_intervals), 3)
    fuel = {"status": "insufficient_data", "cash_spend": fuel_cash, "consumption_cost": fuel_cash,
            "liters": None, "distance_km": None, "l_per_100km": None, "cost_per_km": None,
            "reliable_intervals": reliable_intervals}
    if reliable_distance > 0:
        fuel.update({"status": "reliable_full_tank", "liters": reliable_liters,
                     "distance_km": reliable_distance,
                     "l_per_100km": round(reliable_liters / reliable_distance * 100, 2),
                     "cost_per_km": round(fuel_cash / distance, 2) if distance else None})
    core_trip_cost = round(non_fuel_core + fuel_cash, 2)
    road_vehicle_cost = cents_to_amount(sum(row_amount_cents(r) for r in rows if r["category"] in {"toll", "parking"}))
    vehicle_cost = cents_to_amount(sum(row_amount_cents(r) for r in rows if r["category"] in VEHICLE_CATEGORIES))
    vehicle_cost_per_km = round(vehicle_cost / distance, 2) if distance else None
    days = build_trip_days(trip, rows, manual_readings, day_metadata)
    today = datetime.now().date().isoformat()
    current_day = next((day for day in days if day["travel_date"] == today), None)
    today_rows = [row for row in rows if date_from_timestamp(row["occurred_at"]) == today]
    today_spend = cents_to_amount(sum(row_amount_cents(row) for row in today_rows))
    today_entry_count = len(today_rows)
    return {"trip": rowdict(trip), "total_spend": total, "cash_spend": total,
            "core_cash_spend": core_cash, "core_trip_cost": core_trip_cost,
            "distance_km": distance, "core_cost_per_km": round(core_trip_cost / distance, 2) if distance and core_trip_cost is not None else None,
            "vehicle_cost": vehicle_cost, "vehicle_cost_per_km": vehicle_cost_per_km,
            "current_odometer": latest_odometer, "current_odometer_at": latest_point["occurred_at"] if latest_point else None,
            "current_odometer_source": latest_point.get("source") if latest_point else None,
            "by_category": by_category, "fuel": fuel, "entries": [rowdict(r) for r in reversed(rows)],
            "odometer_readings": [rowdict(r) for r in reversed(manual_readings)],
            "days": days, "current_day": current_day, "today_spend": today_spend,
            "today_entry_count": today_entry_count,
            "recent_entries": [rowdict(r) for r in reversed(rows[-3:])],
            "daily_spend": [{"travel_date": day["travel_date"], "day_number": day["day_number"], "amount": day["spend"]} for day in days],
            "app_version": APP_VERSION}


def excel_column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def worksheet_xml(rows: list[list], widths: list[int]) -> str:
    def cell_xml(row_number: int, column_number: int, value, header: bool) -> str:
        ref = f"{excel_column(column_number)}{row_number}"
        style = ' s="1"' if header else ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"{style} t="n"><v>{value}</v></c>'
        safe = escape("" if value is None else str(value))
        return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{safe}</t></is></c>'

    row_parts = []
    for row_number, row in enumerate(rows, 1):
        cells = "".join(cell_xml(row_number, column_number, value, row_number == 1)
                        for column_number, value in enumerate(row, 1))
        row_parts.append(f'<row r="{row_number}">{cells}</row>')
    columns = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
                      for i, width in enumerate(widths, 1))
    last_ref = f"{excel_column(len(rows[0]))}{max(1, len(rows))}"
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="A1:{last_ref}"/><sheetViews><sheetView workbookViewId="0">'
            f'<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            f'</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>{columns}</cols>'
            f'<sheetData>{"".join(row_parts)}</sheetData><autoFilter ref="A1:{last_ref}"/></worksheet>')


def export_workbook(trip_id: int | None = None, include_all: bool = False) -> bytes:
    """导出可分析的六表工作簿；默认当前进行中旅程，``all=1`` 保留旧全量行为。"""
    with connect() as db:
        if trip_id is None and not include_all:
            active = db.execute("SELECT id FROM trips WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
            trip_id = int(active["id"]) if active else None
        if include_all:
            trips = [rowdict(row) for row in db.execute("SELECT * FROM trips ORDER BY id").fetchall()]
            entries = [rowdict(row) for row in db.execute("SELECT * FROM entries WHERE deleted_at IS NULL ORDER BY trip_id,occurred_at,id").fetchall()]
            readings = [rowdict(row) for row in db.execute("SELECT * FROM odometer_readings WHERE deleted_at IS NULL ORDER BY trip_id,occurred_at,id").fetchall()]
            deleted_entries = [rowdict(row) for row in db.execute("SELECT * FROM entries WHERE deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?) ORDER BY deleted_at,id", (now_text(),)).fetchall()]
            deleted_readings = [rowdict(row) for row in db.execute("SELECT * FROM odometer_readings WHERE deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?) ORDER BY deleted_at,id", (now_text(),)).fetchall()]
            revisions = [rowdict(row) for row in db.execute("SELECT * FROM record_revisions ORDER BY created_at,id").fetchall()]
        elif trip_id is not None:
            trips = [rowdict(row) for row in db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchall()]
            entries = [rowdict(row) for row in db.execute("SELECT * FROM entries WHERE trip_id=? AND deleted_at IS NULL ORDER BY occurred_at,id", (trip_id,)).fetchall()]
            readings = [rowdict(row) for row in db.execute("SELECT * FROM odometer_readings WHERE trip_id=? AND deleted_at IS NULL ORDER BY occurred_at,id", (trip_id,)).fetchall()]
            deleted_entries = [rowdict(row) for row in db.execute("SELECT * FROM entries WHERE trip_id=? AND deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?) ORDER BY deleted_at,id", (trip_id, now_text())).fetchall()]
            deleted_readings = [rowdict(row) for row in db.execute("SELECT * FROM odometer_readings WHERE trip_id=? AND deleted_at IS NOT NULL AND (delete_expires_at IS NULL OR delete_expires_at>?) ORDER BY deleted_at,id", (trip_id, now_text())).fetchall()]
            revisions = [rowdict(row) for row in db.execute("SELECT * FROM record_revisions WHERE trip_id=? ORDER BY created_at,id", (trip_id,)).fetchall()]
        else:
            # 没有当前进行中行程时，默认导出绝不能退化为全量历史账本；
            # 只有显式 all=1 才允许跨行程导出。
            trips = []
            entries = []
            readings = []
            deleted_entries = []
            deleted_readings = []
            revisions = []
    trip_names = {trip["id"]: trip["name"] for trip in trips}
    summary_rows = [["行程ID", "行程名称", "出发地", "目的地", "开始时间", "结束时间", "状态",
                     "出发里程(km)", "结束里程(km)", "行驶里程(km)", "总支出(元)", "用车成本(元)",
                     "百公里油耗(L/100km)", "每公里成本(元/km)"]]
    for trip in trips:
        report = dashboard(trip["id"])
        summary_rows.append([
            trip["id"], trip["name"], trip["origin"], trip["destination"], trip["started_at"], trip["ended_at"],
            "进行中" if trip["status"] == "active" else "已结束", trip["start_odometer"], trip["end_odometer"],
            report["distance_km"], report["total_spend"], report["vehicle_cost"],
            report["fuel"]["l_per_100km"], report["vehicle_cost_per_km"]])
    detail_rows = [["记录ID", "行程ID", "行程名称", "消费时间", "状态", "一级分类", "实付(元)", "实付(分)", "地点", "中文大致地区",
                    "纬度", "经度", "定位精度(米)", "消费内容", "油号", "加油升数(L)", "油价(元/L)",
                    "计算金额(元)", "优惠差额(元)", "里程(km)", "是否加满", "人数", "住宿晚数", "原始文字", "版本", "客户端修订号", "最后修改时间"]]
    for entry in entries:
        full_tank = "" if entry["full_tank"] is None else ("是" if entry["full_tank"] else "否")
        detail_rows.append([
            entry["id"], entry["trip_id"], trip_names.get(entry["trip_id"], ""), entry["occurred_at"], entry.get("status"),
            CATEGORIES.get(entry["category"], (entry["category_label"],))[0], cents_to_amount(row_amount_cents(entry)), row_amount_cents(entry), entry["location"], entry.get("region"),
            entry.get("latitude"), entry.get("longitude"), entry.get("gps_accuracy"), entry["note"], entry["fuel_grade"],
            entry["fuel_liters"], entry["fuel_unit_price"], cents_to_amount(entry.get("fuel_calculated_amount_cents")) if entry.get("fuel_calculated_amount_cents") is not None else None,
            cents_to_amount(entry.get("fuel_discount_cents")) if entry.get("fuel_discount_cents") is not None else None, entry["odometer"], full_tank,
            entry["people"], entry["nights"], entry["raw_text"], entry.get("version"),
            entry.get("client_revision"), entry.get("updated_at")])

    reading_rows = [["记录ID", "行程ID", "行程名称", "记录时间", "当前里程(km)", "地点", "备注", "来源", "版本", "最后修改时间"]]
    for reading in readings:
        reading_rows.append([reading["id"], reading["trip_id"], trip_names.get(reading["trip_id"], ""),
                             reading["occurred_at"], reading["odometer"], reading["location"], reading["note"],
                             reading["source"], reading["version"], reading["updated_at"]])
    trash_rows = [["类型", "记录ID", "行程ID", "行程名称", "删除时间", "可恢复至", "金额/里程", "摘要", "版本"]]
    for entry in deleted_entries:
        trash_rows.append(["消费", entry["id"], entry["trip_id"], trip_names.get(entry["trip_id"], ""), entry["deleted_at"],
                           entry["delete_expires_at"], entry["amount"], entry["note"] or CATEGORIES.get(entry["category"], (entry["category_label"],))[0], entry["version"]])
    for reading in deleted_readings:
        trash_rows.append(["里程", reading["id"], reading["trip_id"], trip_names.get(reading["trip_id"], ""), reading["deleted_at"],
                           reading["delete_expires_at"], reading["odometer"], reading["note"], reading["version"]])
    revision_rows = [["类型", "记录ID", "行程ID", "版本", "操作", "时间", "快照JSON"]]
    for revision in revisions:
        revision_rows.append([revision["record_type"], revision["record_id"], revision["trip_id"], revision["version"],
                              revision["action"], revision["created_at"], revision["snapshot"]])
    day_rows = [["行程ID", "行程名称", "Day", "日期", "路线标题", "出发地", "目的地", "途经", "日支出(元)",
                 "消费笔数", "起始里程(km)", "结束里程(km)", "当日里程(km)", "备注"]]
    for trip in trips:
        for day in trip_days(trip["id"])["days"]:
            day_rows.append([
                trip["id"], trip["name"], day["day_number"], day["travel_date"], day["title"],
                day["origin"], day["destination"], day["via"], day["spend"], day["entry_count"],
                day["start_odometer"], day["end_odometer"], day["distance_km"], day["note"],
            ])

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet4.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet5.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet6.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="行程汇总" sheetId="1" r:id="rId1"/><sheet name="消费明细" sheetId="2" r:id="rId2"/><sheet name="里程记录" sheetId="3" r:id="rId3"/><sheet name="回收站" sheetId="4" r:id="rId4"/><sheet name="修改历史" sheetId="5" r:id="rId5"/><sheet name="每日行程" sheetId="6" r:id="rId6"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet4.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet5.xml"/><Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet6.xml"/><Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF126B52"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml(summary_rows, [10, 22, 14, 14, 20, 20, 10, 16, 16, 16, 14, 16, 22, 20]))
        archive.writestr("xl/worksheets/sheet2.xml", worksheet_xml(detail_rows, [10, 10, 22, 20, 12, 15, 13, 12, 20, 24, 14, 14, 16, 28, 9, 14, 14, 14, 14, 14, 12, 9, 12, 38, 10, 14, 20]))
        archive.writestr("xl/worksheets/sheet3.xml", worksheet_xml(reading_rows, [10, 10, 22, 20, 16, 20, 28, 12, 10, 20]))
        archive.writestr("xl/worksheets/sheet4.xml", worksheet_xml(trash_rows, [12, 10, 10, 22, 20, 20, 16, 30, 10]))
        archive.writestr("xl/worksheets/sheet5.xml", worksheet_xml(revision_rows, [18, 10, 10, 10, 12, 20, 80]))
        archive.writestr("xl/worksheets/sheet6.xml", worksheet_xml(day_rows, [10, 22, 9, 14, 24, 18, 18, 28, 14, 12, 16, 16, 16, 28]))
    return output.getvalue()


def sync_record(payload: dict) -> dict:
    """离线队列统一入口。

    新记录用 client_id 去重；v25 及更早的服务器记录没有该字段时，删除/恢复
    可用 ``payload.id + payload.trip_id`` 精确定位，且必须通过同一版本校验。
    """
    entity = payload.get("entity")
    op = payload.get("op")
    if entity not in {"entry", "odometer"} or op not in {"upsert", "delete", "restore"}:
        raise ValueError("同步实体或操作无效")
    if payload.get("client_revision") is None:
        raise ValueError("离线同步必须提供客户端修订号")
    try:
        if int(payload["client_revision"]) < 1:
            raise ValueError("客户端修订号必须大于0")
    except (TypeError, ValueError) as exc:
        raise ValueError("客户端修订号无效") from exc
    client_id = str(payload.get("client_id") or "").strip()
    data = dict(payload.get("payload") or {})
    if client_id:
        data["client_id"] = client_id
    data["client_revision"] = payload.get("client_revision")
    table = "entries" if entity == "entry" else "odometer_readings"
    record_id = payload.get("id", data.get("id"))
    trip_id = data.get("trip_id", payload.get("trip_id"))
    record_type = "entry" if entity == "entry" else "odometer_reading"
    with connect() as db:
        purged = find_purged_record(
            db, record_type, client_id=client_id,
            record_id=record_id, trip_id=trip_id)
        if purged:
            if trip_id is not None and int(purged["trip_id"]) != int(trip_id):
                raise RecordConflictError("客户端编号与永久删除记录的行程不匹配")
            if op == "delete":
                acknowledged_revision = max(int(purged["client_revision"]), int(data["client_revision"]))
                if acknowledged_revision != int(purged["client_revision"]):
                    db.execute("UPDATE purged_records SET client_revision=? WHERE id=?",
                               (acknowledged_revision, purged["id"]))
                return {"status": "idempotent", "entity": entity,
                        "id": int(purged["record_id"]) if purged["record_id"] is not None else None,
                        "client_revision": acknowledged_revision,
                        "version": None, "deleted_at": purged["purged_at"],
                        "tombstone": True, "purged": True,
                        "delete_confirmed": True}
            raise RecordGoneError("该本机记录已永久删除，不能重新入账或恢复")
        by_client = db.execute(f"SELECT * FROM {table} WHERE client_id=?", (client_id,)).fetchone() if client_id else None
        by_id = None
        if record_id is not None and trip_id is not None:
            try:
                by_id = db.execute(f"SELECT * FROM {table} WHERE id=? AND trip_id=?", (int(record_id), int(trip_id))).fetchone()
            except (TypeError, ValueError) as exc:
                raise ValueError("同步记录编号或行程编号无效") from exc
    if by_client and by_id and by_client["id"] != by_id["id"]:
        raise ValueError("客户端编号与记录编号不属于同一条记录")
    existing = by_client or by_id
    # v25 及更早的记录没有 client_id。首次离线编辑/删除/恢复时，
    # 在同一个写事务内先确认未被永久删除，再绑定稳定本机编号。
    if client_id and existing and existing["client_id"] is None:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reject_purged_client_id(db, record_type, client_id)
            current = db.execute(
                f"SELECT * FROM {table} WHERE id=? AND trip_id=?",
                (existing["id"], existing["trip_id"]),
            ).fetchone()
            if not current:
                raise ValueError("记录状态已变更，请刷新后重试")
            current_client_id = str(current["client_id"] or "").strip() or None
            if current_client_id not in {None, client_id}:
                raise RecordConflictError("客户端编号与当前记录不匹配，请刷新后重试")
            if current_client_id is None:
                db.execute(
                    f"UPDATE {table} SET client_id=? WHERE id=? AND trip_id=? AND client_id IS NULL",
                    (client_id, existing["id"], existing["trip_id"]),
                )
            existing = db.execute(f"SELECT * FROM {table} WHERE id=?", (existing["id"],)).fetchone()
    if op == "upsert":
        if not client_id:
            raise ValueError("新增或更新同步必须提供客户端编号")
        if existing:
            if existing["deleted_at"] is not None:
                data.setdefault("trip_id", existing["trip_id"])
                record = restore_and_update_record(
                    "entry" if entity == "entry" else "odometer_reading", existing["id"], data)
                return {"status": "restored", "entity": entity, "id": record["id"],
                        "client_revision": record.get("client_revision"), "version": record.get("version"),
                        "deleted_at": record.get("deleted_at"), "record": record}
            data.setdefault("trip_id", existing["trip_id"])
            record = update_entry(existing["id"], data) if entity == "entry" else update_odometer_reading(existing["id"], data)
            status = "idempotent" if int(data.get("client_revision") or 0) == int(existing["client_revision"]) else "updated"
        else:
            record = save_entry(data) if entity == "entry" else save_odometer_reading(data)
            status = "created"
    else:
        if not existing:
            if op == "delete":
                # 客户端可能在服务器已接受删除后断线；重放应收敛为成功而非反复报错。
                return {"status": "idempotent", "entity": entity, "id": record_id,
                        "client_revision": data.get("client_revision"), "version": None,
                        "deleted_at": None, "tombstone": True,
                        "delete_confirmed": True}
            raise ValueError("找不到要同步的本机记录；旧记录请提供 id 和 trip_id")
        data.setdefault("trip_id", existing["trip_id"])
        if op == "delete":
            result = delete_entry(existing["id"], data) if entity == "entry" else delete_odometer_reading(existing["id"], data)
            with connect() as db:
                deleted_row = db.execute(
                    f"SELECT version,deleted_at,delete_expires_at,client_revision FROM {table} WHERE id=? AND trip_id=?",
                    (existing["id"], existing["trip_id"]),
                ).fetchone()
            return {"status": "idempotent" if result.get("idempotent") else "deleted", "entity": entity,
                    "id": existing["id"],
                    "client_revision": (deleted_row["client_revision"] if deleted_row else data.get("client_revision")),
                    "version": (deleted_row["version"] if deleted_row else
                                int(existing["version"]) + (0 if result.get("idempotent") else 1)),
                    "deleted_at": (deleted_row["deleted_at"] if deleted_row else None),
                    "delete_expires_at": (deleted_row["delete_expires_at"] if deleted_row else result.get("delete_expires_at")),
                    "delete_confirmed": True}
        if existing["deleted_at"] is None:
            if ensure_client_revision(existing, data):
                record, status = rowdict(existing), "idempotent"
            else:
                raise ValueError("该本机记录不在回收站")
        else:
            record = restore_record("entry" if entity == "entry" else "odometer_reading", existing["id"], data)
            status = "restored"
    return {"status": status, "entity": entity, "id": record["id"],
            "client_revision": record.get("client_revision"), "version": record.get("version"),
            "deleted_at": record.get("deleted_at"), "record": record}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def json_response(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def validate_write_request(self):
        """只接受 JSON，且浏览器 Origin 必须与请求 Host 同源。

        Tailscale Serve 会把 HTTPS 请求转给本地 HTTP 服务，但保留客户端请求的
        Host；因此以 Host 比较，而不信任可由客户端伪造的 Forwarded/X-Forwarded-*。
        无 Origin 的非浏览器本机调用保留可用，网络边界由 127.0.0.1 端口绑定承担。
        """
        if not hasattr(self, "headers"):  # 供无 socket 的单元测试直接调用。
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise ValueError("写入请求必须使用 application/json")
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlparse(origin)
        host = self.headers.get("Host", "")
        if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
            raise ValueError("写入请求来源不受允许")

    def xlsx_response(self, payload: bytes):
        filename = f"自驾账本-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def client_reset_response(self):
        payload = '''<!doctype html><meta charset="utf-8"><title>正在重置</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="font-family:system-ui;padding:32px;color:#173c32">正在清除本机旧账本数据……
<script>
(async()=>{localStorage.clear();sessionStorage.clear();
if("caches" in window){for(const key of await caches.keys())await caches.delete(key)}
if("serviceWorker" in navigator){for(const item of await navigator.serviceWorker.getRegistrations())await item.unregister()}
location.replace("/?factory_reset="+Date.now())})().catch(()=>location.replace("/?factory_reset="+Date.now()));
</script></body>'''.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Clear-Site-Data", '"cache", "storage"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                with connect() as db:
                    reset_epoch = db.execute(
                        "SELECT value FROM app_meta WHERE key='reset_epoch'").fetchone()["value"]
                    schema_version = db.execute(
                        "SELECT value FROM app_meta WHERE key='schema_version'").fetchone()["value"]
                return self.json_response({"ok": True, "reset_epoch": reset_epoch,
                                           "app_version": APP_VERSION, "schema_version": int(schema_version)})
            if parsed.path == "/api/client-reset":
                return self.client_reset_response()
            if parsed.path == "/api/trips":
                with connect() as db:
                    rows = db.execute("SELECT * FROM trips ORDER BY id DESC").fetchall()
                return self.json_response([rowdict(r) for r in rows])
            if parsed.path == "/api/dashboard":
                trip_id = int(parse_qs(parsed.query).get("trip_id", [0])[0])
                return self.json_response(dashboard(trip_id))
            m = re.fullmatch(r"/api/trips/(\d+)/days", parsed.path)
            if m:
                return self.json_response(trip_days(int(m.group(1))))
            if parsed.path == "/api/trash":
                trip_id = int(parse_qs(parsed.query).get("trip_id", [0])[0])
                return self.json_response(trash(trip_id))
            if parsed.path == "/api/odometer-readings":
                trip_id = int(parse_qs(parsed.query).get("trip_id", [0])[0])
                with connect() as db:
                    rows = reading_rows(db, trip_id)
                return self.json_response([rowdict(row) for row in rows])
            m = re.fullmatch(r"/api/(entries|odometer-readings)/(\d+)/revisions", parsed.path)
            if m:
                return self.json_response(record_revisions("entry" if m.group(1) == "entries" else "odometer_reading", int(m.group(2))))
            if parsed.path == "/api/export.xlsx":
                query = parse_qs(parsed.query)
                selected = query.get("trip_id", [None])[0]
                try:
                    selected_trip = int(selected) if selected not in (None, "") else None
                except (TypeError, ValueError) as exc:
                    raise ValueError("导出行程编号无效") from exc
                include_all = query.get("all", ["0"])[0] in {"1", "true"}
                return self.xlsx_response(export_workbook(selected_trip, include_all))
            return super().do_GET()
        except RecordGoneError as exc:
            return self.json_response({"error": str(exc), "gone": True}, HTTPStatus.GONE)
        except RecordConflictError as exc:
            return self.json_response(
                {"error": str(exc), "conflict": True, **exc.details}, HTTPStatus.CONFLICT)
        except (ValueError, sqlite3.Error) as exc:
            return self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        try:
            self.validate_write_request()
            if self.path == "/api/trash/purge-client":
                return self.json_response(permanently_purge_client(self.body()))
            if self.path == "/api/parse":
                return self.json_response(parse_text(self.body().get("text", "")))
            if self.path == "/api/trips":
                return self.json_response(create_trip(self.body()), HTTPStatus.CREATED)
            if self.path == "/api/entries":
                return self.json_response(save_entry(self.body()), HTTPStatus.CREATED)
            if self.path == "/api/odometer-readings":
                return self.json_response(save_odometer_reading(self.body()), HTTPStatus.CREATED)
            if self.path == "/api/sync":
                return self.json_response(sync_record(self.body()))
            m = re.fullmatch(r"/api/(entries|odometer-readings)/(\d+)/restore", self.path)
            if m:
                return self.json_response(restore_record("entry" if m.group(1) == "entries" else "odometer_reading", int(m.group(2)), self.body()))
            m = re.fullmatch(r"/api/trips/(\d+)/finish", self.path)
            if m:
                return self.json_response(finish_trip(int(m.group(1)), self.body()))
            m = re.fullmatch(r"/api/trips/(\d+)/reopen", self.path)
            if m:
                return self.json_response(reopen_trip(int(m.group(1))))
            return self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except RecordGoneError as exc:
            return self.json_response({"error": str(exc), "gone": True}, HTTPStatus.GONE)
        except RecordConflictError as exc:
            return self.json_response(
                {"error": str(exc), "conflict": True, **exc.details}, HTTPStatus.CONFLICT)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            print(f"[api-error] POST {self.path}: {exc}", flush=True)
            return self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_PUT(self):
        try:
            self.validate_write_request()
            m = re.fullmatch(r"/api/trips/(\d+)", urlparse(self.path).path)
            if m:
                return self.json_response(update_trip_start_odometer(int(m.group(1)), self.body()))
            m = re.fullmatch(r"/api/trips/(\d+)/days/(\d{4}-\d{2}-\d{2})", urlparse(self.path).path)
            if m:
                return self.json_response(save_trip_day(int(m.group(1)), m.group(2), self.body()))
            m = re.fullmatch(r"/api/entries/(\d+)", urlparse(self.path).path)
            if m:
                return self.json_response(update_entry(int(m.group(1)), self.body()))
            m = re.fullmatch(r"/api/odometer-readings/(\d+)", urlparse(self.path).path)
            if m:
                return self.json_response(update_odometer_reading(int(m.group(1)), self.body()))
            return self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except RecordGoneError as exc:
            return self.json_response({"error": str(exc), "gone": True}, HTTPStatus.GONE)
        except RecordConflictError as exc:
            return self.json_response(
                {"error": str(exc), "conflict": True, **exc.details}, HTTPStatus.CONFLICT)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            return self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self):
        try:
            self.validate_write_request()
            m = re.fullmatch(r"/api/trash/(entries|odometer-readings)/(\d+)", urlparse(self.path).path)
            if m:
                record_type = "entry" if m.group(1) == "entries" else "odometer_reading"
                return self.json_response(permanently_delete_record(record_type, int(m.group(2)), self.body()))
            m = re.fullmatch(r"/api/entries/(\d+)", urlparse(self.path).path)
            if m:
                return self.json_response(delete_entry(int(m.group(1)), self.body()))
            m = re.fullmatch(r"/api/odometer-readings/(\d+)", urlparse(self.path).path)
            if m:
                return self.json_response(delete_odometer_reading(int(m.group(1)), self.body()))
            return self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except RecordGoneError as exc:
            return self.json_response({"error": str(exc), "gone": True}, HTTPStatus.GONE)
        except RecordConflictError as exc:
            return self.json_response(
                {"error": str(exc), "conflict": True, **exc.details}, HTTPStatus.CONFLICT)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            return self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    init_db()
    if "--init-only" in sys.argv:
        return
    print(f"自驾账本已启动：http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
