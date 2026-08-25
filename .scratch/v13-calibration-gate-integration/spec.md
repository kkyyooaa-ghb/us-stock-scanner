# V1.3 校準閘門與週報口徑整合

Status: resolved

## Goal

讓永久週報、Telegram 與機器可讀 JSON 以 V1.3 shadow episode 成熟度作為
唯一可授權調參的主要閘門。Notion `legacy-v0` 的 15 筆 D8 統計保留作歷史
baseline，但不得再解鎖正式權重或 `MIN_PRIORITY_FOR_GO` 校準。

## Scope

- 驗證並讀取 `reports/shadow_episode_summary.json`。
- 比對現行 Snapshot schema、Shadow measurement、Signal engine 與 ConfigHash。
- 在週報 Markdown、Telegram HTML 與 JSON 顯示 completed-R 60/100 成熟度。
- 缺檔、JSON 損壞、版本／cohort 不符或不合法 maturity 時 fail-closed。
- 週報 workflow 先封存本週 daily snapshots、重算 shadow performance/episodes，
  再產生與推播週報，避免成熟度落後一週。
- 將 legacy 3/15 降為歷史參考文案。
- 修正 `SETUP.md` watchdog 09:25 為實際 09:20 ET。

## Non-goals

- 不改 SignalEngine、TradePlan、ShadowMeasurement、SnapshotSchema 或 ConfigHash。
- 不調整分數、量縮 0.7、門檻 7、Top 10、主題加分或交易規則。
- 不改寫歷史自動產生報告。

## Acceptance criteria

1. V1.3 completed-R 未達 60 時，週報明確顯示禁止調參。
2. 達 60 時只開放全體授權分析審查；segment 仍以各 20 completed-R 獨立判定，
   五個 segment 不聯合阻擋 global gate。
3. 達 100 時顯示 target reached，但不自動修改參數。
4. legacy-v0 不論累積多少樣本都不能解鎖 V1.3。
5. 缺檔、損壞或任何版本／cohort 不符時，週報顯示 fail-closed 原因。
6. 週報 JSON 保存 V1.3 gate 原始驗證結果。
7. workflow 的本週 daily snapshots 必須先進入 shadow/episode 重算，再產生週報。
8. 相關測試、完整測試與 `git diff --check` 通過。

## Answer

- `weekly_report.py` 會在封存本週 daily snapshots 後，先重算 shadow
  performance 與 episodes，再驗證 V1.3 summary 並產生週報。
- V1.3 gate 嚴格比對 Snapshot schema、SignalEngine、TradePlan、
  ShadowMeasurement、ConfigHash、60/100 maturity 與 segment 20 筆規則。
- 缺檔、損壞、版本／cohort 不符或 maturity 矛盾一律輸出 blocked gate，
  `parameter_tuning_allowed=false`。
- Markdown、Telegram 與 JSON schema 3 都顯示同一份 V1.3 gate；legacy-v0
  仍保留統計，但不再產生任何調參授權文案。
- workflow 不再於週報之後才重算 episodes，因此成熟度不會落後一週。
- `SETUP.md` watchdog 時間已與 workflow 對齊為 09:15／09:20 ET。
- 135 項測試通過，1 項真實 XNYS 測試因本機缺 `exchange_calendars` 跳過；
  `git diff --check` 通過。
