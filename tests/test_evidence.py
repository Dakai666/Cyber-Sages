"""EvidenceStore 編號（id）契約：單調遞增、不因 items 移除/重指派而重用。"""

from datetime import date

from cyber_sages.data.evidence import Evidence, EvidenceStore


def _ev(field="x", category="quote"):
    return Evidence(category=category, field=field, value=1.0, source="s", as_of=date.today())


def test_add_assigns_sequential_ids():
    store = EvidenceStore(ticker="T")
    a, b, c = store.add(_ev()), store.add(_ev()), store.add(_ev())
    assert [a.id, b.id, c.id] == ["E001", "E002", "E003"]


def test_ids_are_unique_after_items_reassigned():
    # 回歸：舊版用 len(items)+1 推 id，移除/重指派 items 後再 add 會重用已發過的 id。
    # 現以單調計數器（high-water mark）保證永不重複。
    store = EvidenceStore(ticker="T")
    for _ in range(6):
        store.add(_ev())
    # 直接重指派 items（模擬篩選）——計數器不可因此回退
    store.items = [e for e in store.items if e.id in ("E001", "E002")]
    d = store.add(_ev())
    assert d.id == "E007"  # 接續 high-water mark，不是 E003
    ids = [e.id for e in store.items]
    assert len(ids) == len(set(ids))  # 全域唯一


def test_loaded_store_continues_numbering():
    # 從既有 items 建構（如 JSON 載入）→ 計數器接續其數量，再 add 不撞號。
    store = EvidenceStore(ticker="T", items=[_ev(), _ev()])
    e = store.add(_ev())
    assert e.id == "E003"
