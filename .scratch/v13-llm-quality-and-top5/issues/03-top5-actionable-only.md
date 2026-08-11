# 週報 Top5 排除無腿別

Type: task
Status: resolved
Blocked by: -

## Scope

`weekly_report.aggregate()`:Top5 只列實際觸發腿別者。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-11 claimed。證據:2026-08-05 的 EA 取得 Score 19.83(全週最高),
  卻是 `Priority=1`、`SelectedLeg=none`、`TradePlanStatus=not_applicable`,
  排在週報「本週最高分 Top5」首位。
- 非系統性:當日 79 檔無腿別股票中只有 EA 分數超過 10。

## Answer

- 只改**顯示**,未改計分。改計分會動 `strategy_config_hash`,使 cohort
  由 8/60 歸零、10-09 的達標預估作廢 —— 那個成因留待 10 月閘門開啟後與
  調參一併處理。
- 舊 schema 無 `SelectedLeg` 欄時不過濾;全部無腿別時退回原行為。
- 8/3~8/7 實測:EA 被排除,AMAT 遞補,其餘四檔不變。
