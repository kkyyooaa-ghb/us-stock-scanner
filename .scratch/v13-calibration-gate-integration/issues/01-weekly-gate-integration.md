# 週報閘門與資料流整合

Type: task
Status: resolved
Blocked by: -

## Scope

- 建立 V1.3 episode summary loader 與嚴格驗證。
- 將 V1.3 成熟度傳入週報 message 與 JSON。
- 保留 legacy 統計，但移除其調參授權文案。
- 重排週報工作流程，確保本週 episode summary 在週報前完成。
- 修正文檔時間與新增回歸測試。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-03 claimed。基線 `tests.test_weekly_report` 在 Windows cp950 下因
  既有 emoji print 發生 `UnicodeEncodeError`；後續驗證使用 UTF-8 Python
  mode，並另行確認這不是本次功能造成的失敗。

## Answer

- 新增嚴格的 V1.3 gate loader，合法 summary 才可回傳 tuning allowed。
- episode summary 增加 `trade_plan_version`，讓週報可驗證完整量尺身分。
- 週報 JSON schema 升為 3，保存經驗證的 `v13_calibration_gate`。
- legacy 15 筆進度改為純歷史完整度；週報最終判讀只服從 V1.3 gate。
- 新增缺檔、損壞、cohort mismatch、maturity 矛盾、60／100、segment 20、
  零掃描週、JSON 輸出與 refresh 順序測試。
- 實際 `reports/shadow_performance.csv` 重建結果為 36 episodes、2 completed-R，
  loader 驗證為 collecting 2/60 且禁止調參。
