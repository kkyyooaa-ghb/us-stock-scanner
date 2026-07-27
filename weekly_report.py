"""
美股掃描週報 V1.2.1(分代引擎校準 + 永久歸檔)
================================
目的:觀察期的每週數據彙總 — 回答「精選 0 檔是否結構性?分數分布長怎樣?
     MIN_PRIORITY_FOR_GO=7 初始值該定哪?」這組問題。
     V1.1.0(2026-07-08):P1 track_performance 已上線 → 新增「📐 校準報告」
     區塊(R 期望值 / 勝率 / 停損率 / DistTag 分組 / 極端跳空分組),
     對齊台股週日校準節奏;為 V1.2.0 計分核心重寫提供舊引擎 baseline。
     V1.2.0(2026-07-23):週報 Markdown/JSON + 每日完整 CSV 永久保存至
     reports/,Telegram 降為同源推播端,不再是唯一週報來源。
     V1.2.1(2026-07-27):校準樣本依 Status 策略前綴切分世代；D8 門檻
     只採 V1.2.0 新引擎 R 樣本，舊 🔥 引擎僅保留為 baseline。

資料源:
  1. 本 repo 的 scan-result-* artifacts(GitHub API,內建 GITHUB_TOKEN,
     actions:read 即可,不需任何新憑證)— 每日全 99 檔分數
  2. Notion 美股掃描 DB(可選,讀 picks 累計數,失敗優雅跳過)
  3. Notion 回填欄(V1.1.0 新增:R值/D+N報酬%/是否觸發停損,
     由 track_performance.py 於本報告前一步驟寫入;失敗優雅跳過)

去重:同一美東日多次執行(手動補跑)只取「最後一次」artifact。
排程:weekly_report.yml 每週日 13:00 UTC = 台北 21:00(台北無 DST,恆定)。
"""
import io
import html
import json
import math
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from snapshot_schema import snapshot_data_rows, write_snapshot

try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:
    ET_TZ = timezone(timedelta(hours=-4))

GH_API = "https://api.github.com"
REPORT_ROOT = Path(__file__).resolve().parent / "reports"
CALIBRATION_MIN_R = 15
LEGACY_MEASUREMENT_VERSION = "legacy-v0"
V12_STATUS_PREFIXES = ("📐", "📉", "🌤️")
LEGACY_STATUS_PREFIXES = ("🔥",)


# ==========================================================================
# 1. 抓本週 artifacts
# ==========================================================================
def fetch_week_artifacts(week_start: str, week_end: str) -> list[dict]:
    """列出指定完整週的 scan-result artifacts,回傳 [{id, created_at, et_date}]"""
    repo  = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("⚠️  缺 GITHUB_REPOSITORY / GITHUB_TOKEN,無法列 artifacts")
        return []

    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    url = f"{GH_API}/repos/{repo}/actions/artifacts?per_page=100"
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        items = r.json().get("artifacts", [])
    except Exception as e:
        print(f"⚠️  列 artifacts 失敗:{e}")
        return []

    out = []
    for a in items:
        if not a.get("name", "").startswith("scan-result-"):
            continue
        if a.get("expired"):
            continue
        try:
            created = datetime.strptime(a["created_at"], "%Y-%m-%dT%H:%M:%SZ") \
                              .replace(tzinfo=timezone.utc)
        except Exception:
            continue
        et_date = created.astimezone(ET_TZ).strftime("%Y-%m-%d")
        if not week_start <= et_date <= week_end:
            continue
        out.append({
            "id":         a["id"],
            "created_at": created,
            "et_date":    et_date,
        })

    ordered = sorted(out, key=lambda item: (item["et_date"], item["created_at"]))
    print(f"📦 {week_start}~{week_end} artifacts:{len(ordered)} 個"
          "（下載後依內容選每日最佳快照）")
    return ordered


def download_csv(artifact_id: int):
    """下載單一 artifact zip → 解出完整 scan_result.csv（含 control row）。"""
    import pandas as pd
    repo  = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    url = f"{GH_API}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         timeout=60, allow_redirects=True)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
            if name is None:
                return None
            with zf.open(name) as f:
                df = pd.read_csv(f, encoding="utf-8-sig")
        return df
    except Exception as e:
        print(f"⚠️  artifact {artifact_id} 下載/解析失敗:{e}")
        return None


def select_daily_artifacts(candidates: list[dict]) -> list[dict]:
    """Prefer successful premarket data over later rerun failures per ET date."""
    selected: dict[str, tuple[tuple[int, datetime], dict]] = {}
    for candidate in candidates:
        frame = candidate.get("frame")
        if frame is None:
            continue
        data = snapshot_data_rows(frame)
        if not data.empty and "Priority" in data.columns:
            sessions = (
                set(data["ScanSession"].astype(str))
                if "ScanSession" in data.columns
                else set()
            )
            if "premarket" in sessions:
                quality = 4
            elif "preopen" in sessions:
                quality = 3
            else:
                quality = 2
        elif (
            "SnapshotRecordType" in frame.columns
            and frame["SnapshotRecordType"].astype(str).eq("control").any()
        ):
            quality = 1
        else:
            quality = 0

        rank = (quality, candidate["created_at"])
        current = selected.get(candidate["et_date"])
        if current is None or rank > current[0]:
            selected[candidate["et_date"]] = (rank, candidate)
    return [
        selected[date][1]
        for date in sorted(selected)
        if selected[date][0][0] > 0
    ]


# ==========================================================================
# 1.5 永久保存每日 CSV(供回測/重算;不再依賴會過期的 Actions artifacts)
# ==========================================================================
def archive_daily_csv(df, et_date: str, report_root: Path = REPORT_ROOT) -> str:
    """把去重後的每日完整掃描 CSV 保存到 reports/daily/YYYY-MM-DD.csv。"""
    daily_dir = report_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{et_date}.csv"
    if "SnapshotSchemaVersion" in df.columns:
        write_snapshot(df, path)
    else:
        # 歷史 30 欄 artifact 原樣保存，不杜撰 V1.3 facts。
        df.to_csv(path, index=False, encoding="utf-8-sig")
    return path.relative_to(report_root.parent).as_posix()


# ==========================================================================
# 2. 彙總
# ==========================================================================
def aggregate(days: list[tuple[str, "pd.DataFrame"]]) -> dict:
    """days = [(et_date, df), ...] → 週彙總 dict"""
    import pandas as pd

    daily = []
    for et_date, df in days:
        p = pd.to_numeric(df["Priority"], errors="coerce").fillna(0)
        daily.append({
            "date":     et_date,
            "n":        len(df),
            "ge7":      int((p >= 7).sum()),
            "eq6":      int((p == 6).sum()),
            "eq5":      int((p == 5).sum()),
            "b34":      int(((p >= 3) & (p <= 4)).sum()),
            "warn":     int((p < 0).sum()),
        })

    # 全週各檔最高 Score(跨日取 max),供 Top5
    frames = []
    for et_date, df in days:
        sub = df[["Ticker", "Priority", "Score", "DistTag", "YoY"]].copy()
        sub["date"] = et_date
        frames.append(sub)
    allw = pd.concat(frames, ignore_index=True)
    allw["Score"] = pd.to_numeric(allw["Score"], errors="coerce").fillna(0)
    allw["Priority"] = pd.to_numeric(allw["Priority"], errors="coerce").fillna(0)
    idx = allw.groupby("Ticker")["Score"].idxmax()
    best = allw.loc[idx].sort_values("Score", ascending=False)
    top5 = best.head(5).to_dict("records")

    # 6 分常客(差 1 分達門檻,門檻定值的關鍵素材)
    eq6_days = allw[allw["Priority"] == 6].groupby("Ticker")["date"].nunique()
    eq6_regulars = eq6_days[eq6_days >= 2].sort_values(ascending=False)

    # 反向警告常客
    warn_days = allw[allw["Priority"] < 0].groupby("Ticker")["date"].nunique()
    warn_regulars = warn_days[warn_days >= 2].sort_values(ascending=False)

    # ── 逢低布局診斷彙總(第一步:為②超賣反彈/③盤整低接計分腿蒐證)──
    dip = _aggregate_dip(days)

    return {
        "daily":         daily,
        "top5":          top5,
        "eq6_regulars":  list(eq6_regulars.items()),
        "warn_regulars": list(warn_regulars.items()),
        "total_ge7":     sum(d["ge7"] for d in daily),
        "dip":           dip,
    }


def _aggregate_dip(days: list[tuple[str, "pd.DataFrame"]]) -> dict:
    """逢低布局型態的週彙總(若 CSV 無診斷欄 → 回 available=False)"""
    import pandas as pd

    DIAG = {"RSI", "VolDry", "NearMA60", "Oversold", "RsiTurnUp", "HoldMA", "SetupType"}
    frames = [df for _, df in days if DIAG.issubset(df.columns)]
    if not frames:
        return {"available": False}

    tagged = []
    for et_date, df in days:
        if not DIAG.issubset(df.columns):
            continue
        sub = df.copy()
        sub["date"] = et_date
        tagged.append(sub)
    allw = pd.concat(tagged, ignore_index=True)
    for c in ("VolDry", "NearMA60", "Oversold", "RsiTurnUp", "HoldMA"):
        allw[c] = pd.to_numeric(allw[c], errors="coerce").fillna(0).astype(int)
    allw["RSI"] = pd.to_numeric(allw["RSI"], errors="coerce")
    n_days = allw["date"].nunique()

    def _avg_flag(col):
        return allw.groupby("date")[col].sum().mean()

    # 型態出現次數(跨日,以 ticker×日 計)
    st = allw["SetupType"].value_counts().to_dict()

    # ③ 盤整低接候選:出現 ≥2 日的常客(這就是你要的觀察名單雛形)
    consol = allw[allw["SetupType"].isin(["consolidation_dip", "both"])]
    consol_reg = consol.groupby("Ticker")["date"].nunique()
    consol_reg = consol_reg[consol_reg >= 1].sort_values(ascending=False)

    # ② 超賣反彈候選
    ob = allw[allw["SetupType"].isin(["oversold_bounce", "both"])]
    ob_reg = ob.groupby("Ticker")["date"].nunique()
    ob_reg = ob_reg[ob_reg >= 1].sort_values(ascending=False)

    # RSI 分布(只統計有效值 ≥0)
    rsi_valid = allw[allw["RSI"] >= 0]["RSI"]
    rsi_buckets = {
        "lt30":   int((rsi_valid < 30).sum()),
        "30_45":  int(((rsi_valid >= 30) & (rsi_valid < 45)).sum()),
        "45_55":  int(((rsi_valid >= 45) & (rsi_valid < 55)).sum()),
        "55_70":  int(((rsi_valid >= 55) & (rsi_valid < 70)).sum()),
        "ge70":   int((rsi_valid >= 70).sum()),
    }

    return {
        "available":     True,
        "n_days":        n_days,
        "avg_vol_dry":   round(_avg_flag("VolDry"), 1),
        "avg_near_ma60": round(_avg_flag("NearMA60"), 1),
        "avg_oversold":  round(_avg_flag("Oversold"), 1),
        "avg_rsi_turn":  round(_avg_flag("RsiTurnUp"), 1),
        "setup_counts":  st,
        "consol_reg":    list(consol_reg.items())[:8],
        "ob_reg":        list(ob_reg.items())[:8],
        "rsi_buckets":   rsi_buckets,
        "rsi_median":    round(float(rsi_valid.median()), 1) if len(rsi_valid) else None,
    }


# ==========================================================================
# 3. Notion 樣本累計(可選,失敗優雅)
# ==========================================================================
def notion_sample_counts(week_start: str, week_end: str) -> dict:
    """回傳 {ok, week_count, total_count};任何失敗 → ok=False"""
    token = os.environ.get("NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DB_ID", "")
    if not token or not db_id:
        return {"ok": False}

    headers = {"Authorization": f"Bearer {token}",
               "Notion-Version": "2022-06-28",
               "Content-Type": "application/json"}
    url = f"https://api.notion.com/v1/databases/{db_id}/query"

    def _count(payload) -> int:
        total, cursor = 0, None
        for _ in range(20):   # 上限 2000 筆,觀察期遠夠
            body = dict(payload)
            body["page_size"] = 100
            if cursor:
                body["start_cursor"] = cursor
            r = requests.post(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            j = r.json()
            total += len(j.get("results", []))
            if not j.get("has_more"):
                break
            cursor = j.get("next_cursor")
        return total

    try:
        week = _count({
            "filter": {
                "and": [
                    {
                        "property": "掃描日期",
                        "date": {"on_or_after": week_start},
                    },
                    {
                        "property": "掃描日期",
                        "date": {"on_or_before": week_end},
                    },
                ]
            }
        })
        total = _count({})
        return {"ok": True, "week_count": week, "total_count": total}
    except Exception as e:
        print(f"⚠️  Notion 計數失敗(略過該段):{e}")
        return {"ok": False}


# ==========================================================================
# 3.5 校準統計(V1.2.1:依策略世代切分;失敗優雅)
# ==========================================================================
def calibration_engine(status: str) -> str:
    """依 Status 的第一個策略標記辨識計分引擎世代。"""
    value = (status or "").lstrip()
    if value.startswith(V12_STATUS_PREFIXES):
        return "v1_2"
    if value.startswith(LEGACY_STATUS_PREFIXES):
        return "legacy"
    return "unclassified"


def summarize_calibration_rows(rows: list[dict]) -> dict:
    """把單一世代或全體 Notion rows 彙總成可輸出、可測試的校準統計。"""
    n_total = len(rows)
    rs = [x for x in rows if x.get("r") is not None]
    if not rs:
        return {
            "measurement_version": LEGACY_MEASUREMENT_VERSION,
            "n_total": n_total,
            "n_r": 0,
        }

    n_r = len(rs)
    r_vals = [x["r"] for x in rs]

    def _avg(key):
        vals = [x.get(key) for x in rows if x.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    groups = {}
    for x in rs:
        groups.setdefault(x.get("dist") or "?", []).append(x["r"])
    dist_stats = sorted(
        ((k, len(v), sum(v) / len(v)) for k, v in groups.items()),
        key=lambda t: -t[1],
    )

    def _is_extreme_gap(row):
        gap = row.get("gap")
        return gap is not None and abs(gap) >= 5

    ext = [x["r"] for x in rs if _is_extreme_gap(x)]
    rest = [x["r"] for x in rs if not _is_extreme_gap(x)]

    return {
        "measurement_version": LEGACY_MEASUREMENT_VERSION,
        "n_total":    n_total,
        "n_r":        n_r,
        "r_mean":     sum(r_vals) / n_r,
        "win_rate":   sum(1 for v in r_vals if v > 0) / n_r,
        "stop_rate":  sum(1 for x in rs if x.get("stop")) / n_r,
        "avg_d1":     _avg("d1"),
        "avg_d3":     _avg("d3"),
        "avg_d5":     _avg("d5"),
        "dist_stats": dist_stats,
        "ext_gap":    (len(ext), sum(ext) / len(ext)) if ext else None,
        "rest_gap":   (len(rest), sum(rest) / len(rest)) if rest else None,
    }


def v12_calibration_ready(calib: dict) -> bool:
    """D8 readiness 僅由 V1.2.0 新引擎的已定案 R 樣本決定。"""
    v12 = (calib or {}).get("engine_stats", {}).get("v1_2", {})
    return v12.get("n_r", 0) >= CALIBRATION_MIN_R


def notion_calibration_stats() -> dict:
    """
    讀全 DB 的回填欄,彙總:
      n_total / n_r(R已回填筆數)/ r_mean / win_rate(R>0)/ stop_rate
      avg_d1/d3/d5(平均報酬,百分點)
      dist_stats:按 DistTag 分組的 (名稱, n, R均值),n 大到小
      ext_gap / rest_gap:盤前極端跳空(|gap|≥5%)組 vs 其他組的 (n, R均值)
      engine_stats:V1.2.0 / 舊引擎 / 未分類的同型統計
    任何失敗 → ok=False(週報退回觀察期版,不炸)
    """
    token = os.environ.get("NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DB_ID", "")
    if not token or not db_id:
        return {"ok": False}

    headers = {"Authorization": f"Bearer {token}",
               "Notion-Version": "2022-06-28",
               "Content-Type": "application/json"}
    url = f"https://api.notion.com/v1/databases/{db_id}/query"

    rows, cursor = [], None
    try:
        for _ in range(20):   # 上限 2000 筆
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            r = requests.post(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            j = r.json()
            for pg in j.get("results", []):
                pr = pg.get("properties", {})

                def _num(key):
                    return pr.get(key, {}).get("number")

                def _text(key):
                    parts = pr.get(key, {}).get("rich_text", [])
                    return "".join(
                        x.get("plain_text")
                        or x.get("text", {}).get("content", "")
                        for x in parts
                    )

                dist = (pr.get("DistTag", {}).get("select") or {}).get("name", "")
                status = _text("Status")
                rows.append({
                    "r":    _num("R值"),
                    "d1":   _num("D+1報酬%"),
                    "d3":   _num("D+3報酬%"),
                    "d5":   _num("D+5報酬%"),
                    "stop": bool(pr.get("是否觸發停損", {}).get("checkbox")),
                    "dist": dist,
                    "gap":  _num("盤前跳空%"),
                    "status": status,
                    "engine": calibration_engine(status),
                })
            if not j.get("has_more"):
                break
            cursor = j.get("next_cursor")
    except Exception as e:
        print(f"⚠️  校準統計讀取失敗(略過該段):{e}")
        return {"ok": False}

    result = {"ok": True, **summarize_calibration_rows(rows)}
    result["engine_stats"] = {
        engine: summarize_calibration_rows(
            [row for row in rows if row["engine"] == engine]
        )
        for engine in ("v1_2", "legacy", "unclassified")
    }
    return result


# ==========================================================================
# 4. 組訊息 + 推播
# ==========================================================================
def build_message(agg: dict, notion: dict, calib: dict,
                  week_start: str, week_end: str) -> str:
    daily = agg["daily"]
    n_days = len(daily)
    if n_days == 0:
        return (f"<b>📋 美股掃描週報(觀察期)</b>  {week_start} ~ {week_end}\n\n"
                f"🚨 本週 <b>0 次</b>掃描紀錄 — 排程可能漏跑,"
                f"請檢查 Actions 是否有執行/失敗。")

    avg = lambda k: sum(d[k] for d in daily) / n_days

    phase = "校準期" if (calib or {}).get("n_r", 0) > 0 else "觀察期"
    lines = [f"<b>📋 美股掃描週報({phase})</b>  {week_start} ~ {week_end}",
             f"掃描天數 <b>{n_days}</b> | 日均分析 {avg('n'):.0f} 檔", "",
             "<b>分數分布(日均)</b>",
             f"  ≥7 達門檻:{avg('ge7'):.1f} 檔(週總 {agg['total_ge7']})",
             f"  6 分(差1分):{avg('eq6'):.1f} 檔",
             f"  5 分:{avg('eq5'):.1f} 檔",
             f"  3–4 分:{avg('b34'):.1f} 檔",
             f"  &lt;0 反向警告:{avg('warn'):.1f} 檔", ""]

    lines.append("<b>本週最高分 Top5</b>(跨日取最佳)")
    for r in agg["top5"]:
        yoy = r.get("YoY")
        try:
            yoy_s = f" YoY{float(yoy)*100:+.0f}%" if yoy == yoy and yoy is not None else ""
        except Exception:
            yoy_s = ""
        lines.append(f"  {r['Ticker']:<6} P{int(r['Priority'])} "
                     f"S{float(r['Score']):.1f} {r.get('DistTag','')}{yoy_s} [{r['date'][5:]}]")
    lines.append("")

    if agg["eq6_regulars"]:
        names = "、".join(f"{t}({d}日)" for t, d in agg["eq6_regulars"][:6])
        lines.append(f"🎯 <b>6 分常客</b>(差 1 分達門檻):{names}")
    if agg["warn_regulars"]:
        names = "、".join(f"{t}({d}日)" for t, d in agg["warn_regulars"][:6])
        lines.append(f"⚠️ 反向警告常客:{names}")
    if agg["eq6_regulars"] or agg["warn_regulars"]:
        lines.append("")

    if notion.get("ok"):
        lines.append(f"<b>Notion 樣本</b>  本週寫入 {notion['week_count']} 筆 | "
                     f"累計 <b>{notion['total_count']}</b> 筆")
    else:
        lines.append("<b>Notion 樣本</b>  讀取略過")
    lines.append("")

    # ── 校準報告(V1.1.0:P1 回填上線後啟用)──
    calib = calib or {}
    if calib.get("ok") and calib.get("n_r", 0) > 0:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(
            "<b>📐 校準報告</b>"
            "(legacy-v0:R模擬 D+5收盤出場/觸停損-1R)"
        )
        lines.append(f"  全期已定案 <b>{calib['n_r']}</b>/{calib['n_total']} 筆")
        engine_stats = calib.get("engine_stats", {})
        v12 = engine_stats.get("v1_2", {})
        legacy = engine_stats.get("legacy", {})
        unknown = engine_stats.get("unclassified", {})
        if engine_stats:
            v12_progress = (
                "✅ 已達門檻" if v12_calibration_ready(calib)
                else f"{v12.get('n_r', 0)}/{CALIBRATION_MIN_R}"
            )
            lines.append(
                f"  V1.2.0 已定案 <b>{v12.get('n_r', 0)}</b>/"
                f"{v12.get('n_total', 0)} 筆 | D8 進度 {v12_progress}"
            )
            lines.append(
                f"  舊引擎 baseline:{legacy.get('n_r', 0)}/"
                f"{legacy.get('n_total', 0)} 筆"
            )
            if unknown.get("n_total", 0):
                lines.append(
                    f"  ⚠️ 未分類:{unknown.get('n_r', 0)}/"
                    f"{unknown['n_total']} 筆(不計入 D8)"
                )
        else:
            lines.append("  ⚠️ 引擎分代資料不可用，D8 不判定")
        lines.append(f"  全期 R期望值 <b>{calib['r_mean']:+.2f}</b> | "
                     f"勝率 {calib['win_rate']:.0%} | "
                     f"停損率 {calib['stop_rate']:.0%}")
        if v12.get("n_r", 0):
            lines.append(
                f"  V1.2.0 R期望值 <b>{v12['r_mean']:+.2f}</b> | "
                f"勝率 {v12['win_rate']:.0%} | "
                f"停損率 {v12['stop_rate']:.0%}"
            )
        d_parts = [f"D+{n} {v:+.2f}%"
                   for n, v in ((1, calib.get("avg_d1")),
                                (3, calib.get("avg_d3")),
                                (5, calib.get("avg_d5")))
                   if v is not None]
        if d_parts:
            lines.append(f"  平均報酬:{' | '.join(d_parts)}")
        if calib.get("dist_stats"):
            parts = [f"{name} n={n} R{rm:+.2f}"
                     for name, n, rm in calib["dist_stats"][:4]]
            lines.append(f"  全期按位階:{' | '.join(parts)}")
        if calib.get("ext_gap") and calib.get("rest_gap"):
            en, er = calib["ext_gap"]
            rn, rr = calib["rest_gap"]
            lines.append(f"  全期盤前極端跳空(|gap|≥5%):n={en} R{er:+.2f}"
                         f" vs 其他 n={rn} R{rr:+.2f}")
        lines.append("")

    # ── 逢低布局診斷(第一步:②超賣反彈 + ③盤整低接 蒐證)──
    dip = agg.get("dip", {})
    if dip.get("available"):
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("<b>🎯 逢低布局診斷</b>(設計新計分腿用,純記錄)")
        lines.append(f"  日均:量縮 {dip['avg_vol_dry']} 檔 | 貼MA60 {dip['avg_near_ma60']} 檔 | "
                     f"超賣 {dip['avg_oversold']} 檔 | 超賣回升 {dip['avg_rsi_turn']} 檔")
        rb = dip["rsi_buckets"]
        lines.append(f"  RSI 分布(中位 {dip.get('rsi_median','-')}):"
                     f"&lt;30:{rb['lt30']} | 30-45:{rb['30_45']} | 45-55:{rb['45_55']} | "
                     f"55-70:{rb['55_70']} | ≥70:{rb['ge70']}")
        sc = dip["setup_counts"]
        lines.append(f"  型態命中(檔×日):盤整低接 {sc.get('consolidation_dip',0)} | "
                     f"超賣反彈 {sc.get('oversold_bounce',0)} | 雙重 {sc.get('both',0)}")
        if dip["consol_reg"]:
            names = "、".join(f"{t}({d}日)" for t, d in dip["consol_reg"][:6])
            lines.append(f"  📐 <b>盤整低接候選</b>:{names}")
        if dip["ob_reg"]:
            names = "、".join(f"{t}({d}日)" for t, d in dip["ob_reg"][:6])
            lines.append(f"  📉 <b>超賣反彈候選</b>:{names}")
        lines.append("")

    # 週報判讀(規則式,不做主觀建議)
    v12_n_r = (
        calib.get("engine_stats", {}).get("v1_2", {}).get("n_r", 0)
        if calib.get("ok") else 0
    )
    if v12_calibration_ready(calib):
        lines.append("💡 <i>V1.2.0 校準樣本已達 n≥15 且 R 已回填 — D8 門檻已過,"
                     "可依上方位階/跳空分組差異啟動 V1.2.0 計分核心校準。</i>")
    elif calib.get("ok") and calib.get("n_r", 0) > 0:
        lines.append(
            f"💡 <i>V1.2.0 R 樣本累積中({v12_n_r}/{CALIBRATION_MIN_R});"
            "舊引擎樣本只作 baseline,不計入 D8。"
            "達門檻後再啟動權重與 MIN_PRIORITY_FOR_GO 校準。</i>"
        )
    elif agg["total_ge7"] == 0:
        lines.append("💡 <i>本週 0 檔達 7 分門檻。法人腿停用下,達標僅剩"
                     "「吸籌+季營收+主題」一路;6 分常客即是門檻定值的候選證據。"
                     "累積 2 週以上分布後再議 MIN_PRIORITY_FOR_GO(D8:數據先、規則後)。</i>")
    else:
        lines.append("💡 <i>本週已有達門檻精選 — 等 P1 報酬回填上線後,"
                     "週報將升級為完整校準報告(R 期望值/勝率/n 進度)。</i>")

    return "\n".join(lines)


# ==========================================================================
# 5. 永久週報輸出(Markdown + JSON;Telegram 僅為同源推播)
# ==========================================================================
def _json_safe(value):
    """遞迴轉成 JSON 可序列化型別(處理 pandas/numpy scalar)。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return str(value)


def telegram_html_to_markdown(message: str) -> str:
    """把既有 Telegram HTML 輸出轉成可版本追蹤的 Markdown。"""
    out = message
    for old, new in (
        ("<b>", "**"), ("</b>", "**"),
        ("<i>", "*"),  ("</i>", "*"),
    ):
        out = out.replace(old, new)
    out = html.unescape(out)
    out = out.replace("━━━━━━━━━━━━━━━━━━", "---")
    # Telegram 以換行排版;Markdown soft break 可能被折成空格,補兩個空白保留版面。
    lines = out.strip().splitlines()
    return "\n".join(
        f"{line}  " if line and line != "---" else line
        for line in lines
    ) + "\n"


def write_report_files(message: str, agg: dict, notion: dict, calib: dict,
                       week_start: str, week_end: str,
                       archived_csv_files: list[str],
                       report_root: Path = REPORT_ROOT) -> list[str]:
    """
    同一份週報資料一次寫出:
      reports/weekly/YYYY-MM-DD_YYYY-MM-DD.{md,json}
      reports/latest.{md,json}
    JSON 保留原始統計快照;Markdown 與 Telegram 由同一個 message 轉出。
    """
    weekly_dir = report_root / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{week_start}_{week_end}"
    markdown = (
        "<!-- AUTO-GENERATED by weekly_report.py; edit source data/code instead. -->\n\n"
        + telegram_html_to_markdown(message)
    )
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot = {
        "schema_version": 2,
        "generated_at_utc": generated_at,
        "period": {"start": week_start, "end": week_end, "timezone": "America/New_York"},
        "sources": {
            "scan_csv": archived_csv_files,
            "notion_database": os.environ.get("NOTION_DB_ID", ""),
            "telegram": "delivery_only",
        },
        "aggregate": _json_safe(agg),
        "notion_samples": _json_safe(notion),
        "calibration": _json_safe(calib),
        "telegram_html": message,
        "markdown": markdown,
    }

    targets = {
        weekly_dir / f"{stem}.md": markdown,
        weekly_dir / f"{stem}.json": json.dumps(
            snapshot, ensure_ascii=False, indent=2, allow_nan=False
        ) + "\n",
        report_root / "latest.md": markdown,
        report_root / "latest.json": json.dumps(
            snapshot, ensure_ascii=False, indent=2, allow_nan=False
        ) + "\n",
    }
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    return [
        path.relative_to(report_root.parent).as_posix()
        for path in targets
    ]


def resolve_report_period(now_et=None, requested_week_end: str = "") -> tuple[str, str]:
    """
    預設永遠取最近一個完整週一～週日，避免週中手動執行覆蓋 latest。
    手動指定時只接受週日且不可是未來日期。
    """
    now_et = now_et or datetime.now(ET_TZ)
    if requested_week_end:
        try:
            end_date = datetime.strptime(requested_week_end, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("REPORT_WEEK_END 必須是 YYYY-MM-DD") from exc
        if end_date.weekday() != 6:
            raise ValueError("REPORT_WEEK_END 必須是週日(完整週結束日)")
        if end_date > now_et.date():
            raise ValueError("REPORT_WEEK_END 不可晚於目前美東日期")
    else:
        days_since_sunday = (now_et.weekday() + 1) % 7
        end_date = now_et.date() - timedelta(days=days_since_sunday)

    start_date = end_date - timedelta(days=6)
    return start_date.isoformat(), end_date.isoformat()


def main():
    requested_week_end = os.environ.get("REPORT_WEEK_END", "").strip()
    try:
        week_start, week_end = resolve_report_period(
            datetime.now(ET_TZ), requested_week_end
        )
    except ValueError as exc:
        print(f"❌ 週報區間設定錯誤:{exc}")
        raise SystemExit(2)
    print(f"📋 週報區間(ET):{week_start} ~ {week_end}")

    arts = fetch_week_artifacts(week_start, week_end)
    downloaded = []
    for artifact in arts:
        frame = download_csv(artifact["id"])
        if frame is not None:
            downloaded.append({**artifact, "frame": frame})
    selected_artifacts = select_daily_artifacts(downloaded)
    days = []
    archived_csv_files = []
    for artifact in selected_artifacts:
        full_snapshot = artifact["frame"]
        archived_csv_files.append(
            archive_daily_csv(full_snapshot, artifact["et_date"])
        )
        data = snapshot_data_rows(full_snapshot)
        if not data.empty and "Priority" in data.columns:
            days.append((artifact["et_date"], data))
            print(f"  ✅ {artifact['et_date']}:{len(data)} 檔")
        else:
            print(f"  ⚠️ {artifact['et_date']}:control snapshot 已封存，不納入彙總")

    agg = aggregate(days) if days else {"daily": [], "top5": [],
                                        "eq6_regulars": [], "warn_regulars": [],
                                        "total_ge7": 0}
    notion = notion_sample_counts(week_start, week_end)
    calib  = notion_calibration_stats()
    msg = build_message(agg, notion, calib, week_start, week_end)
    report_files = write_report_files(
        msg, agg, notion, calib, week_start, week_end, archived_csv_files
    )
    print(f"🗂️  週報已永久保存:{', '.join(report_files)}")

    print("─" * 40 + "\n" + msg.replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", "").replace("&lt;", "<") + "\n" + "─" * 40)

    from outputs import send_telegram   # 重用既有推播(3 次重試)
    send_telegram(msg)


if __name__ == "__main__":
    main()
