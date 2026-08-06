# UniverseVersion 納入調參授權

Type: task
Status: resolved
Blocked by: -

## Scope

- `episode_analysis`:`_universe_cohort()` 偵測 + 併入 `overall_ready`。
- `weekly_report`:授權驗證納入母體一致性,並在週報說明阻擋原因。
- `config`:`EPISODE_REQUIRE_SINGLE_UNIVERSE` 開關。
- 測試:新增 20 項;補齊既有 fixture。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-06 claimed(Claude,經 owner 指派)。
- 發現過程:owner 問「不碰 ConfigHash 還能做什麼」,回答前查證
  `SCAN_POOL` 是否受保護,才發現 `UniverseVersion` 只被寫入、從未被驗證。

## Answer

- 見 `../spec.md`。刻意選「偵測並擋下」而非「加進 required versions 靜默
  丟棄」,因為後者會讓 NDX 換一檔就抹掉數月累積,且使用者不會察覺。
- 後續可考慮:`snapshot_health` 也加一項跨日母體漂移偵測,讓問題在每日
  層級就浮現,而不必等到週報。
