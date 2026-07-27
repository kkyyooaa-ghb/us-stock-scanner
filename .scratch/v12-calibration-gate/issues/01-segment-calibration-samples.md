# 依引擎世代切分校準樣本

Type: task
Status: resolved

## Scope

- 重構 `weekly_report.py` 的校準彙總，使核心計算可單元測試。
- 在週報中顯示 V1.2.0 與舊引擎樣本進度。
- 將 D8 readiness 改為只採用 V1.2.0 的已定案 R 樣本。
- 新增測試與決策證據。

## Comments

- 2026-07-27：Notion 即時查詢確認共 138 筆，其中舊引擎 44 筆、
  V1.2.0 94 筆；已回填 R 分別為 20 與 2 筆。

## Answer

`weekly_report.py` 已依 `Status` 前綴輸出分代引擎統計，D8 readiness
只採 V1.2.0 已定案 R 樣本。週報會保留全期與舊引擎 baseline，
並把無法辨識的狀態排除於 D8。

驗證：

- `python -m unittest discover -s tests -v`：6 項通過。
- `python -m py_compile weekly_report.py tests/test_weekly_report.py`：通過。
- Notion 即時聚合與 `reports/latest.json` 對帳：138 筆、22 筆已定案一致。
