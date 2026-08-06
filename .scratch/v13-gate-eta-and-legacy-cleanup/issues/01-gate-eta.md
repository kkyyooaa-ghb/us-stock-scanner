# 閘門達標日預估

Type: task
Status: resolved
Blocked by: -

## Scope

- 新增 `gate_projection.py`(決定性管線 + 估計到達率)。
- 接進 `episode_analysis` summary 與 Markdown、週報顯示與 JSON。
- 週報 JSON schema 3 → 4。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-06 claimed(Claude,經 owner 指派)。
- 第一版用樣本標準差算區間,3 個觀察日給出 ±2 天的假性精確;改用
  Poisson 95% 後區間誠實擴大,已成為正式版。

## Answer

- 見 `../spec.md`。目前實測 60 筆 → 2026-10-19(區間 10-09 ～ 11-17)。
- 關鍵防呆:cohort 首日 backlog 排除、預估失敗不影響授權、日曆可注入。
