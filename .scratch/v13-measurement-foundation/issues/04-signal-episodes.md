# Episode 與完成樣本統計

Type: task
Status: ready-for-agent
Blocked by: 03

## Scope

- 每日原始訊號全部保存。
- 以 ticker、selected_leg 與交易生命週期建立 episode。
- 同一 episode 不重複計為獨立交易。
- 報告 filled/unfilled/ambiguous/open/completed 與按腿別、order type 的樣本進度。
