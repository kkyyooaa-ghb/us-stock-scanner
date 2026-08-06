# 分層統計與多重比較防護

Type: task
Status: resolved
Blocked by: -

## Scope

- 新增 `tuning_analysis.py` 與 CLI。
- 探索/確認兩層結論、bootstrap 區間、R 上下界分別計算。
- 閘門未開時扣住結論。
- 新增 `tests/test_tuning_analysis.py`。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-06 claimed(Claude,經 owner 指派)。
- 刻意讓確認層在目前資料下回空 —— 那是正確答案,不是缺陷。

## Answer

- 見 `../spec.md`。
- 後續可考慮:閘門開啟後把本工具接進週報工作流,每週自動產出一份
  `reports/tuning_analysis.{md,json}`,讓「還不能調」與「可以調了」都有
  一致的證據鏈。目前刻意不接,避免在未授權期間產生看起來像結論的檔案。
