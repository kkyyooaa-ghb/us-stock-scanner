# Episode 與完成樣本統計

Type: task
Status: resolved
Blocked by: 03

## Scope

- 每日原始訊號全部保存。
- 以 ticker、selected_leg 與交易生命週期建立 episode。
- 同一 episode 不重複計為獨立交易。
- 報告 filled/unfilled/ambiguous/open/completed 與按腿別、order type 的樣本進度。

## Comments

- 2026-07-27：開始實作。一檔股票同時只允許一個活躍 episode；首筆訊號
  凍結 selected leg 與 TradePlan，後續同股訊號在生命週期結束前只算觀察。

## Answer

- 新增 `episode_analysis.py`，以 ticker 與 canonical TradePlan 的完整交易生命
  週期建立穩定 `EpisodeId`，並保留 episode 內重複訊號數與觀察到的腿別。
- 新增 filled、unfilled、awaiting、open、completed、ambiguous 與 R 區間 KPI，
  並按 selected leg 與 order type 分層。
- 設定全體 completed-R 60 筆最低／100 筆目標，以及 segment 20 筆的分析閘門；
  閘門只授權分析，不會自動修改參數。
- 每週工作流會永久輸出 episode CSV、JSON 與 Markdown 報告。
- 2026-07-27 全套 36 項測試、語法編譯及 `git diff --check` 全部通過。
