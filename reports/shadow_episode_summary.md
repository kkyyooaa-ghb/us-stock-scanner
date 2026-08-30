# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：370
- 排除非本版量尺：25
- 原始日訊號：345
- 獨立 episodes：105
- 去除重複訊號：240 (69.6%)
- 已完成 R episodes：25
- 調參閘門：collecting (25/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 78 | 25 | 2 | 52 | 25 | 0 |

成交率：75.7%；未成交率：24.3%；R 期望區間：-1.09 ～ -1.09。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 28 | 20 | 7 | 14 | 6 | -1.22 | -1.22 | no |
| healthy_pullback | 67 | 51 | 15 | 31 | 19 | -1.05 | -1.05 | no |
| oversold_bounce | 10 | 7 | 3 | 7 | 0 | - | - | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 80 | 62 | 17 | 39 | 22 | -1.05 | -1.05 | no |
| buy_stop_reclaim | 25 | 16 | 8 | 13 | 3 | -1.42 | -1.42 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-08-28（信心度 medium）
- 已完成 R：25；決定性管線（已成交未了結）：52 筆
- 每掃描日新增 episode：3.636（Poisson 95% 2.823～4.449），觀察 22 個掃描日
- 成交率 75.5%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 35 | 30 | 2026-10-12 | 2026-10-12 | 2026-10-12 |
| 目標 | 100 | 75 | 49 | 2026-11-06 | 2026-11-04 | 2026-11-10 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
