# Cyber-Sages Docs

技術 spec 與 roadmap 紀錄庫。每份文件對應到一個明確的子專案或設計決策。

## 怎麼讀

1. 先看 [`ROADMAP.md`](./ROADMAP.md) 拿全貌：兩條鐵律、現況摘要、5 個 spec、Phase 0–6 開發計劃。
2. 想深入某個子專案，看 `specs/` 對應檔案。
3. 各 spec 的「開放問題」已於 2026-06-13 全數定案（DK 授權按最優解），定案內容在各 spec 的「決議」段落。

## 結構

```
docs/
├── README.md          ← 你在這
├── ROADMAP.md         ← 願景 + 兩條鐵律 + Phase 0–6 計劃
└── specs/             ← 各 spec 紀錄
    ├── 2026-06-12-A-pillar-1-data-layer.md
    ├── 2026-06-12-B-pillar-1-pipeline-hardening.md
    ├── 2026-06-12-C-pillar-2-sage-jury.md
    ├── 2026-06-12-D-pillar-2-decision-structure.md
    └── 2026-06-13-E-sage-runtime-and-nuwa.md
```

## Spec 狀態

| Spec | 標題 | 狀態 | 涵蓋 | Phase |
|---|---|---|---|---|
| A | Pillar 1 資料層擴充 | accepted | W1, W2, W6, W10 + issue #4 + 測試覆蓋 | 1 |
| B | Pillar 1 管線硬化 | accepted | W4, W7, W8, W9（W3/W5 前置 Phase 0） | 0 + 2 |
| C | Pillar 2 陪審團結構 | accepted | P2, P3, P6, P7（P1 移入 E） | 4 |
| D | Pillar 2 決策結構 | accepted | P4, P5 | 5 |
| E | Sage Runtime + Cyber-Nüwa | accepted | P1 + issue #2 三問定案 | 3（E1）+ 6（E2） |

> 完整 audit 結果見對話紀錄（2026-06-12 全專案審查）。未來若 audit 更新，本表需同步。

## 慣例

- 日期前綴 `YYYY-MM-DD-<topic>.md` 方便排序與 git 查閱
- 每份 spec 都有「Status / Dependencies / 涵蓋 gaps / 範圍 / 驗收 / 決議 / 相關檔案 / 參考」
- 設計決策落定後，spec 升級為 `accepted`；實作完成後為 `implemented`
- 決議若在實作中被推翻，回 spec 補記「推翻紀錄」而非直接改寫原決議（保留思路軌跡）
