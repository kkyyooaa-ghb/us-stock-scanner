# V1.3 Shadow Episode Report

- 選股 cohort：v1.2.1 / 8142e595d788ac06
- 輸入訊號：435
- 排除非本版量尺：25
- 原始日訊號：410
- 獨立 episodes：116
- 去除重複訊號：294 (71.7%)
- 已完成 R episodes：38
- 調參閘門：collecting (38/60 minimum；target 100)

## Lifecycle

| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |
|---:|---:|---:|---:|---:|---:|
| 88 | 26 | 2 | 49 | 38 | 0 |

成交率：77.2%；未成交率：22.8%；R 期望區間：-1.07 ～ -1.07。

## By Selected Leg

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| consolidation_dip | 32 | 22 | 8 | 13 | 9 | -1.15 | -1.15 | no |
| healthy_pullback | 69 | 54 | 15 | 25 | 28 | -1.04 | -1.04 | no |
| oversold_bounce | 15 | 12 | 3 | 11 | 1 | -1.00 | -1.00 | no |

## By Order Type

| Segment | Episodes | Filled | Unfilled | Open | Completed R | R lower | R upper | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| buy_limit_zone | 83 | 66 | 17 | 33 | 32 | -1.04 | -1.04 | no |
| buy_stop_reclaim | 33 | 22 | 9 | 16 | 6 | -1.21 | -1.21 | no |

母體一致：ndx-99-78834e47b659（cohort 內單一 UniverseVersion）

## 達標預估

- 基準日：2026-09-04（信心度 medium）
- 已完成 R：38；決定性管線（已成交未了結）：49 筆
- 每掃描日新增 episode：3.37（Poisson 95% 2.664～4.077），觀察 27 個掃描日
- 成交率 77.0%；time exit 40 個交易日
- cohort 首日 backlog 25 筆已排除，不列入到達率

| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |
|---|---:|---:|---:|---|---|---|
| 最低 | 60 | 22 | 19 | 2026-10-02 | 2026-10-02 | 2026-10-02 |
| 目標 | 100 | 62 | 46 | 2026-11-10 | 2026-11-09 | 2026-11-11 |

> ⚠️ 只排 time exit;提早停損會讓實際日期早於本預估
> ⚠️ awaiting_fill 未計入管線,預估偏晚
> ⚠️ cohort 首日 backlog 已排除,不列入到達率
> ⚠️ 區間為到達率的 Poisson 95%;成交率的二項不確定性未計入
> ⚠️ 任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢

> 本報告只使用 v1.3.1-shadow episode；legacy-v0 不混入。閘門未通過前不得依此調整權重。
