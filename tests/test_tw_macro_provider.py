"""台灣央行總經源（TWMacroProvider）的純單元測試——不打網路，餵合成的央行 API JSON
給抽出的純函式 `_parse_discount_rate`，驗證欄位定位（靠 structure 欄名、不硬編欄序）、
取最新非空、期別→期底日換算，以及壞輸入的優雅回 None。"""

from datetime import date

from cyber_sages.data.tw_macro import TWMacroProvider, _period_to_date


def _payload(rows: list[list], columns=("重貼現率", "擔保放款融通利率", "短期融通")) -> dict:
    """構造央行 EG2AM01 風格的回應：data.dataSets 逐列 [期別, 欄1…] + structure.Table1 欄名。"""
    return {
        "meta": {"title": "29.利率 A.中央銀行利率依期間", "units": "年息百分比率"},
        "data": {
            "dataSets": rows,
            "structure": {"Table1": [{"data": c} for c in columns]},
        },
    }


def test_parse_discount_rate_picks_latest_and_locates_column_by_name():
    # 多欄，重貼現率在第一欄——靠 structure 欄名定位（非硬編欄序），取最新一列。
    payload = _payload([
        ["2026M02", "2.000", "2.375", "4.250"],
        ["2026M03", "2.000", "2.375", "4.250"],
        ["2026M04", "1.875", "2.250", "4.125"],
    ])
    ev = TWMacroProvider._parse_discount_rate(payload)
    assert ev is not None
    assert ev.field == "tw_discount_rate" and ev.category == "macro"
    assert ev.value == 1.875 and ev.unit == "%"
    assert ev.as_of == date(2026, 4, 30)  # 期底（月底）
    assert "CBC EG2AM01" in ev.source


def test_parse_discount_rate_column_order_independent():
    # 重貼現率不在第一欄時仍正確定位（防欄序變動）。
    payload = _payload(
        [["2026M04", "9.99", "1.50"]],
        columns=("短期融通", "重貼現率"),
    )
    ev = TWMacroProvider._parse_discount_rate(payload)
    assert ev is not None and ev.value == 1.50  # 取「重貼現率」欄＝1.50，不是首欄 9.99


def test_parse_discount_rate_skips_trailing_empty():
    # 央行對未發布月份可能回空值——取最新「有值」的一列，不誤取空。
    payload = _payload([
        ["2026M03", "2.000", "2.375", "4.250"],
        ["2026M04", "-", "-", "-"],
        ["2026M05", "", "", ""],
    ])
    ev = TWMacroProvider._parse_discount_rate(payload)
    assert ev is not None and ev.value == 2.000 and ev.as_of == date(2026, 3, 31)


def test_parse_discount_rate_none_when_column_absent():
    # structure 無「重貼現率」欄 → 回 None（不硬猜欄序）。
    payload = _payload([["2026M04", "2.000"]], columns=("擔保放款融通利率",))
    assert TWMacroProvider._parse_discount_rate(payload) is None


def test_parse_discount_rate_none_on_empty_or_malformed():
    assert TWMacroProvider._parse_discount_rate({}) is None
    assert TWMacroProvider._parse_discount_rate(_payload([])) is None


def test_period_to_date_month_end_and_bad_format():
    assert _period_to_date("2026M04") == date(2026, 4, 30)
    assert _period_to_date("2024M02") == date(2024, 2, 29)  # 閏年
    assert _period_to_date("2026Q1") is None                # 非月期別
    assert _period_to_date("bad") is None


def test_available_is_true_no_key_required():
    # 央行 API 公開無需金鑰 → 恆可用（抓取失敗在 get_macro 內降級為 []）。
    assert TWMacroProvider.available() is True
