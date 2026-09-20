# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：614
- 排除非本版量尺：25
- 原始日訊號：589
- 獨立 episodes：150
- 去除重複訊號：439 (74.5%)
- 已完成 R episodes：57
- 調參閘門：collecting (57/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 114 | 33 | 3 | 56 | 57 | 0 |

成交率：77.5%；未成交率：22.4%；R 期望區間：-1.05 ～ -1.05。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 43 | 29 | 13 | 15 | 14 | -1.09 | -1.09 | no |
| healthy_pullback | 82 | 65 | 17 | 26 | 38 | -1.03 | -1.03 | no |
| oversold_bounce | 25 | 20 | 3 | 15 | 5 | -1.06 | -1.06 | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 100 | 81 | 19 | 37 | 43 | -1.03 | -1.03 | no |
| buy_stop_reclaim | 50 | 33 | 14 | 19 | 14 | -1.11 | -1.11 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-09-18（信心度 medium）
- 已完成 R：57；決定性管線（已成交未了結）：56 筆
- 每掃描日新增 episode：3.472（Poisson 95% 2.851～4.093），觀察 36 個掃描日
- 成交率 77.4%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 3 | 2 | 2026-09-22 | 2026-09-22 | 2026-09-22 |
| 目標 | 100 | 43 | 34 | 2026-11-05 | 2026-11-05 | 2026-11-05 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
