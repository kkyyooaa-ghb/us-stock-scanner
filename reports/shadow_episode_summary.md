# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：507
- 排除非本版量尺：25
- 原始日訊號：482
- 獨立 episodes：131
- 去除重複訊號：351 (72.8%)
- 已完成 R episodes：47
- 調參閘門：collecting (47/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 99 | 29 | 3 | 51 | 47 | 0 |

成交率：77.3%；未成交率：22.7%；R 期望區間：-1.05 ～ -1.05。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 39 | 27 | 10 | 16 | 11 | -1.12 | -1.12 | no |
| healthy_pullback | 73 | 57 | 16 | 23 | 33 | -1.03 | -1.03 | no |
| oversold_bounce | 19 | 15 | 3 | 12 | 3 | -1.00 | -1.00 | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 91 | 72 | 18 | 33 | 38 | -1.03 | -1.03 | no |
| buy_stop_reclaim | 40 | 27 | 11 | 18 | 9 | -1.14 | -1.14 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-09-11（信心度 medium）
- 已完成 R：47；決定性管線（已成交未了結）：51 筆
- 每掃描日新增 episode：3.419（Poisson 95% 2.755～4.084），觀察 31 個掃描日
- 成交率 77.2%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 13 | 13 | 2026-09-30 | 2026-09-30 | 2026-09-30 |
| 目標 | 100 | 53 | 41 | 2026-11-09 | 2026-11-09 | 2026-11-09 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
