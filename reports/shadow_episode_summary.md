# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：294
- 排除非本版量尺：25
- 原始日訊號：269
- 獨立 episodes：94
- 去除重複訊號：175 (65.1%)
- 已完成 R episodes：17
- 調參閘門：collecting (17/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 67 | 23 | 4 | 49 | 17 | 0 |

成交率：74.4%；未成交率：25.6%；R 期望區間：-1.12 ～ -1.12。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 25 | 17 | 6 | 11 | 6 | -1.22 | -1.22 | no |
| healthy_pullback | 62 | 46 | 14 | 34 | 11 | -1.07 | -1.07 | no |
| oversold_bounce | 7 | 4 | 3 | 4 | 0 | - | - | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 73 | 55 | 16 | 40 | 14 | -1.05 | -1.05 | no |
| buy_stop_reclaim | 21 | 12 | 7 | 9 | 3 | -1.42 | -1.42 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-08-21（信心度 medium）
- 已完成 R：17；決定性管線（已成交未了結）：49 筆
- 每掃描日新增 episode：4.059（Poisson 95% 3.082～5.036），觀察 17 個掃描日
- 成交率 74.2%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 43 | 35 | 2026-10-12 | 2026-10-12 | 2026-10-12 |
| 目標 | 100 | 83 | 52 | 2026-11-04 | 2026-11-02 | 2026-11-09 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
