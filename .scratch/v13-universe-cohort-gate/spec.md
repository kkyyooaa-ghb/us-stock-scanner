# V1.3 母體一致性閘門

Status: resolved

## Goal

補上 `SCAN_POOL` 造成的 cohort 破口。

## 病灶

`strategy_config_hash()` 是明確 allowlist,**不含 `SCAN_POOL`**。
`universe_version()` 雖然每天寫進快照(`main.py:297`、`528`)、也在 schema
與 shadow performance 欄位裡,但 `episode_analysis._current_measurement_rows`
的 cohort 判定只比對 TradePlan / PlanMeasurement / V13Measurement /
SignalEngine / ConfigHash 五項 —— **UniverseVersion 從未參與**。

後果:增刪成分股

- **不會**讓 cohort 歸零(計數照常累積)
- **但會**改變主題觸發,進而改變**其他**股票的分數與 Top 10
  (2026-07-28 的 REGN dry run 已證實:在 biotech 只有 1 檔觸發的日子,
  新增 REGN 會讓整池點亮)

於是同一份調參樣本混進兩套選股母體,而且沒有任何機制會發現。這比歸零更糟,
因為歸零至少看得見。諷刺的是 `_current_measurement_rows` 的 docstring 自己
寫著「two selection universes must never share one tuning gate — that is the
accident this project has already had once」。

`snapshot_health` 擋不到:它比對「今天的快照 vs 今天的 `SCAN_POOL`」,
兩者同時改動時一路綠燈。

時效性:`config.py` 註明 NDX 每年 12 月重組,正好落在累積 100 筆的區間內。

## 設計選擇:偵測並擋下,而不是靜默丟棄

把 `UniverseVersion` 直接加進 `_current_measurement_rows` 的 required
versions 也能擋,但那會**靜默丟棄**舊母體樣本 —— NDX 換掉一檔就抹掉數月
累積,而且使用者不會意識到發生了什麼。

改為:偵測 → 攤開組成 → 禁止調參 → 由人決定接受混合(需記錄理由)或
重新起算。資料一筆不丟,決策浮上檯面。行為由
`Config.EPISODE_REQUIRE_SINGLE_UNIVERSE` 控制(預設 True)。

## Answer

- `episode_analysis._universe_cohort()` 回報 current / observed 分布 /
  distinct / mixed / matches_current / consistent / reason。
- 三種不一致都擋:多套母體、單一但非現行母體、`UniverseVersion` 欄位缺失
  (缺欄無法證明一致 → fail closed)。空 frame 視為 vacuously consistent。
- `parameter_tuning_allowed` = 樣本達標 **且** 母體一致;新增
  `blocked_by_universe` 讓「筆數夠但母體混合」與「筆數不夠」區分得開。
  segment 的 `tuning_ready` 連帶為 false。
- `weekly_report.load_v13_calibration_gate` 把母體一致性納入授權驗證:
  缺 `universe_cohort` → `missing_universe_cohort`;形狀錯 →
  `invalid_universe_cohort`;summary 的 current 與本次 `universe_version()`
  不符 → `universe_version_mismatch`(擋掉沿用舊 summary)。
- 混合母體時 gate 仍 `ok=True` —— 那不是壞資料,是明確的「不可調參」。
  週報會說明原因、幾套母體,以及 SCAN_POOL 不在 ConfigHash 內這件事。
- episode Markdown 一致時一行帶過,不一致時攤開組成表與處理建議。

### 驗收證據

- `strategy_config_hash()` = `8142e595d788ac06`、`universe_version()` =
  `ndx-99-78834e47b659`,與改動前完全相同。
- 測試 186 → **206 項全部通過**;新增 `tests/test_universe_cohort_gate.py`
  20 項,含一項回歸守門:正式 `reports/shadow_episodes.csv` 目前必須是
  單一 UniverseVersion,將來變髒會有人發現。
- 既有 fixture(`test_episode_analysis`、`test_weekly_report`)原本不帶
  `UniverseVersion` / `universe_cohort`,改動後正確地 fail-closed —— 這是
  預期訊號,已補齊 fixture 而非放寬檢查。
- 實際資料驗證:目前 cohort `consistent=True`、`distinct=1`,gate
  `tuning=False`(因 2/60)、`blocked_by_universe=False`。
