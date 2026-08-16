# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：225
- 排除非本版量尺：25
- 原始日訊號：200
- 獨立 episodes：78
- 去除重複訊號：122 (61.0%)
- 已完成 R episodes：8
- 調參閘門：collecting (8/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 55 | 22 | 1 | 47 | 8 | 0 |

成交率：71.4%；未成交率：28.6%；R 期望區間：-1.20 ～ -1.20。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 19 | 13 | 6 | 10 | 3 | -1.42 | -1.42 | no |
| healthy_pullback | 54 | 39 | 14 | 34 | 5 | -1.06 | -1.06 | no |
| oversold_bounce | 5 | 3 | 2 | 3 | 0 | - | - | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 63 | 46 | 16 | 39 | 7 | -1.04 | -1.04 | no |
| buy_stop_reclaim | 15 | 9 | 6 | 8 | 1 | -2.27 | -2.27 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-08-14（信心度 medium）
- 已完成 R：8；決定性管線（已成交未了結）：47 筆
- 每掃描日新增 episode：4.077（Poisson 95% 2.957～5.197），觀察 13 個掃描日
- 成交率 71.4%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 52 | 42 | 2026-10-14 | 2026-10-14 | 2026-10-15 |
| 目標 | 100 | 92 | 56 | 2026-11-03 | 2026-10-29 | 2026-11-11 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
