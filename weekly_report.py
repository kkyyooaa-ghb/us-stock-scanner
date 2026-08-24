"""
美股掃描週報 V1.3.0(V1.3 episode 閘門 + legacy baseline + 永久歸檔)
================================================================
目的:觀察期的每週數據彙總 — 回答「精選 0 檔是否結構性?分數分布長怎樣?
     MIN_PRIORITY_FOR_GO=7 初始值該定哪?」這組問題。
     V1.1.0(2026-07-08):P1 track_performance 已上線 → 新增「📐 校準報告」
     區塊(R 期望值 / 勝率 / 停損率 / DistTag 分組 / 極端跳空分組),
     對齊台股週日校準節奏;為 V1.2.0 計分核心重寫提供舊引擎 baseline。
     V1.2.0(2026-07-23):週報 Markdown/JSON + 每日完整 CSV 永久保存至
     reports/,Telegram 降為同源推播端,不再是唯一週報來源。
     V1.2.1(2026-07-27):校準樣本依 Status 策略前綴切分世代。
     V1.3.0(2026-08-03):V1.3 shadow completed-R 60/100 成為唯一調參閘門；
     Notion legacy-v0 的 15 筆 D8 統計降為歷史 baseline，不再授權調參。

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
from config import Config
from episode_analysis import (
    TUNING_SCOPE_ORDER_TYPES,
    TUNING_SCOPE_SELECTED_LEGS,
)
from snapshot_health import (
    archived_snapshot_eligibility,
    health_report_allows_calibration,
    legacy_health_fallback_allowed,
    read_health_report,
)
from snapshot_schema import snapshot_data_rows, write_snapshot
from sources import is_trading_day
from trade_plan import strategy_config_hash, universe_version

try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:
    ET_TZ = timezone(timedelta(hours=-4))

GH_API = "https://api.github.com"
REPORT_ROOT = Path(__file__).resolve().parent / "reports"
LEGACY_CALIBRATION_MIN_R = 15
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


def download_artifact(artifact_id: int) -> dict | None:
    """Download one scanner artifact with its health and runtime provenance."""
    import pandas as pd
    repo  = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    url = f"{GH_API}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         timeout=60, allow_redirects=True)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            snapshot_name = next(
                (name for name in names if Path(name).name == "scan_result.csv"),
                None,
            )
            if snapshot_name is None:
                return None
            with zf.open(snapshot_name) as handle:
                frame = pd.read_csv(handle, encoding="utf-8-sig")

            health_name = next(
                (name for name in names if Path(name).name == "snapshot_health.json"),
                None,
            )
            health = None
            if health_name is not None:
                with zf.open(health_name) as handle:
                    health = json.loads(handle.read().decode("utf-8"))

            provenance_name = next(
                (name for name in names if Path(name).name == "runtime_provenance.json"),
                None,
            )
            runtime_provenance = None
            if provenance_name is not None:
                with zf.open(provenance_name) as handle:
                    runtime_provenance = json.loads(handle.read().decode("utf-8"))
        return {
            "frame": frame,
            "health": health,
            "runtime_provenance": runtime_provenance,
        }
    except Exception as e:
        print(f"⚠️  artifact {artifact_id} 下載/解析失敗:{e}")
        return None


def download_csv(artifact_id: int):
    """Backward-compatible frame-only adapter."""
    artifact = download_artifact(artifact_id)
    return artifact.get("frame") if artifact else None


def _legacy_health_report(detail: str = "artifact predates health sidecars") -> dict:
    """Make the migration exception explicit instead of treating None as healthy."""
    return {
        "policy_version": "snapshot-usability-v1",
        "status": "warning",
        "usable_for_shadow": True,
        "eligible_for_weekly_calibration": True,
        "retryable": False,
        "errors": [],
        "warnings": [{"code": "legacy_without_health", "message": detail}],
        "metrics": {},
        "legacy_without_health": True,
    }


def resolve_artifact_health(
    candidate: dict,
    report_root: Path = REPORT_ROOT,
) -> dict:
    """Resolve run-level artifact health without borrowing another run's status.

    Before the health-policy cutover, a curated day-level sidecar is allowed to
    correct historical artifacts (notably the 2026-08-18 quarantine). From the
    cutover onward each Actions candidate must carry its own inline run health;
    a daily archive sidecar belongs to the already-selected immutable snapshot
    and must never make a different run usable or blocked.
    """
    et_date = str(candidate.get("et_date", "")).strip()
    sidecar = report_root / "daily_health" / f"{et_date}.json"
    inline = candidate.get("health")
    if legacy_health_fallback_allowed(et_date):
        if et_date and sidecar.is_file():
            return read_health_report(sidecar)
        if isinstance(inline, dict):
            return inline
        return _legacy_health_report()
    if isinstance(inline, dict):
        return inline
    return _missing_health_report(
        "post-policy artifact omitted snapshot_health.json"
    )


def select_daily_artifacts(
    candidates: list[dict],
    report_root: Path = REPORT_ROOT,
) -> list[dict]:
    """Prefer health-accepted premarket data; retain incidents if none passed."""
    selected: dict[str, tuple[tuple[int, int, datetime], dict]] = {}
    for candidate in candidates:
        frame = candidate.get("frame")
        if frame is None:
            continue
        health = resolve_artifact_health(candidate, report_root)
        usable = health_report_allows_calibration(health)
        data = snapshot_data_rows(frame)
        if usable and not data.empty and "Priority" in data.columns:
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

        enriched = {
            **candidate,
            "health": health,
            "health_usable": usable,
            "incident": not usable,
        }
        rank = (int(usable), quality, candidate["created_at"])
        current = selected.get(candidate["et_date"])
        if current is None or rank > current[0]:
            selected[candidate["et_date"]] = (rank, enriched)
    return [
        selected[date][1]
        for date in sorted(selected)
    ]


# ==========================================================================
# 1.5 永久保存每日 CSV(供回測/重算;不再依賴會過期的 Actions artifacts)
# ==========================================================================
def archive_daily_csv(df, et_date: str, report_root: Path = REPORT_ROOT) -> str:
    """把去重後的每日完整掃描 CSV 保存到 reports/daily/YYYY-MM-DD.csv。

    ⚠️ 舊版 artifact 一律原樣保存。快照是不可變的歷史事實,不能用今天的
    schema 重寫 —— 硬套現行契約只會在版本升級當週讓整份週報中止歸檔,
    而那正是最需要把資料保下來的一週。
    """
    daily_dir = report_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{et_date}.csv"
    if path.is_file():
        return path.relative_to(report_root.parent).as_posix()
    versions = (
        set(df["SnapshotSchemaVersion"].dropna().astype(str))
        if "SnapshotSchemaVersion" in df.columns
        else set()
    )
    if versions == {Config.SNAPSHOT_SCHEMA_VERSION}:
        write_snapshot(df, path)
    else:
        # 歷史 30 欄 artifact 與較舊 schema 版本:原樣保存,不杜撰 V1.3 facts。
        if versions:
            print(f"  ℹ️  {et_date} 為舊 schema {sorted(versions)},原樣歸檔"
                  f"(現行 {Config.SNAPSHOT_SCHEMA_VERSION})")
        df.to_csv(path, index=False, encoding="utf-8-sig")
    return path.relative_to(report_root.parent).as_posix()


def _missing_health_report(detail: str = "artifact omitted snapshot_health.json") -> dict:
    return {
        "policy_version": "snapshot-usability-v1",
        "status": "blocked",
        "usable_for_shadow": False,
        "eligible_for_weekly_calibration": False,
        "retryable": False,
        "errors": [{"code": "health_report_missing", "message": detail}],
        "warnings": [],
        "metrics": {},
    }


def archive_daily_health(
    report: dict | None,
    et_date: str,
    report_root: Path = REPORT_ROOT,
) -> str:
    daily_health = report_root / "daily_health"
    daily_health.mkdir(parents=True, exist_ok=True)
    path = daily_health / f"{et_date}.json"
    if path.is_file():
        return path.relative_to(report_root.parent).as_posix()
    payload = report if isinstance(report, dict) else _missing_health_report()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path.relative_to(report_root.parent).as_posix()


def archive_runtime_provenance(
    provenance: dict | None,
    et_date: str,
    report_root: Path = REPORT_ROOT,
) -> str | None:
    if not isinstance(provenance, dict):
        return None
    destination = report_root / "runtime_provenance"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{et_date}.json"
    if path.is_file():
        return path.relative_to(report_root.parent).as_posix()
    path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path.relative_to(report_root.parent).as_posix()


def archive_incident_artifact(
    candidate: dict,
    report_root: Path = REPORT_ROOT,
) -> list[str]:
    """Permanently retain an unusable raw attempt without replacing daily CSV."""
    if health_report_allows_calibration(candidate.get("health")):
        return []
    incident_dir = (
        report_root
        / "incidents"
        / str(candidate["et_date"])
        / f"run-{candidate['id']}"
    )
    incident_dir.mkdir(parents=True, exist_ok=True)
    frame_path = incident_dir / "scan_result.csv"
    if not frame_path.is_file():
        candidate["frame"].to_csv(
            frame_path,
            index=False,
            encoding="utf-8-sig",
        )
    health_path = incident_dir / "snapshot_health.json"
    if not health_path.is_file():
        health_path.write_text(
            json.dumps(
                candidate.get("health") or _missing_health_report(),
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    paths = [frame_path, health_path]
    if isinstance(candidate.get("runtime_provenance"), dict):
        provenance_path = incident_dir / "runtime_provenance.json"
        if not provenance_path.is_file():
            provenance_path.write_text(
                json.dumps(
                    candidate["runtime_provenance"],
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        paths.append(provenance_path)
    return [path.relative_to(report_root.parent).as_posix() for path in paths]


def operational_week_summary(
    week_start: str,
    week_end: str,
    selected_artifacts: list[dict],
) -> dict:
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    scheduled: list[str] = []
    cursor = start
    while cursor <= end:
        date_text = cursor.isoformat()
        calendar = is_trading_day(date_text)
        if (
            calendar.get("is_session")
            if calendar.get("ok")
            else cursor.weekday() < 5
        ):
            scheduled.append(date_text)
        cursor += timedelta(days=1)

    observed = sorted({item["et_date"] for item in selected_artifacts})
    usable = sorted({
        item["et_date"]
        for item in selected_artifacts
        if item.get("health_usable")
    })
    incident = sorted((set(scheduled) - set(usable)) | {
        item["et_date"]
        for item in selected_artifacts
        if item.get("incident")
    })
    missing = sorted(set(scheduled) - set(observed))
    return {
        "scheduled_days": len(scheduled),
        "usable_days": len(usable),
        "incident_days": len(incident),
        "scheduled_dates": scheduled,
        "usable_dates": usable,
        "incident_dates": incident,
        "missing_artifact_dates": missing,
    }


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
        # 舊 schema 歸檔的 CSV 沒有 SelectedLeg;缺欄時填 NA 代表「無從判斷」,
        # 下方只排除明確為 none 者,不對舊資料臆測。
        sub["SelectedLeg"] = (
            df["SelectedLeg"] if "SelectedLeg" in df.columns else pd.NA
        )
        sub["date"] = et_date
        frames.append(sub)
    allw = pd.concat(frames, ignore_index=True)
    allw["Score"] = pd.to_numeric(allw["Score"], errors="coerce").fillna(0)
    allw["Priority"] = pd.to_numeric(allw["Priority"], errors="coerce").fillna(0)

    # Top5 只列實際觸發腿別者。2026-08-05 的 EA 以全週最高分 19.83 排在
    # 首位,卻是 SelectedLeg=none、Priority=1、無交易計畫 —— 掛在「本週
    # 最高分」底下會讓人誤以為那是最佳標的。Score 高而無腿別不是錯,只是
    # 不可執行,不該佔用這個版位。
    leg_text = allw["SelectedLeg"].astype(str).str.strip().str.lower()
    not_actionable = allw["SelectedLeg"].notna() & leg_text.isin({"none", ""})
    candidates = allw.loc[~not_actionable]
    if candidates.empty:
        candidates = allw

    idx = candidates.groupby("Ticker")["Score"].idxmax()
    best = candidates.loc[idx].sort_values("Score", ascending=False)
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

    # 盤前跳空分層已停用(V1.3.2)。Notion 既有的「盤前跳空%」是未版本化的
    # v0 值,分母誤用 Yahoo regularMarketPreviousClose(盤前 = Close[-2]),
    # 實測與前一交易日漲跌幅 corr=0.955 —— 那不是跳空。舊值無法與新值區分,
    # 因此整個分層停用,直到 Notion 端也帶得動 PreGapDefinitionVersion。
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
        "ext_gap":    None,
        "rest_gap":   None,
        "gap_stratification": "disabled_unversioned_pregap_v0",
    }


def legacy_v12_sample_complete(calib: dict) -> bool:
    """歷史 D8 樣本是否完整；此結果不授權 V1.3 調參。"""
    v12 = (calib or {}).get("engine_stats", {}).get("v1_2", {})
    return v12.get("n_r", 0) >= LEGACY_CALIBRATION_MIN_R


def _v13_projection_lines(projection: dict | None) -> list[str]:
    """閘門達標預估的週報呈現。

    只顯示最低門檻那一格與區間 —— 週報是每週掃一眼的東西,細節留在
    `reports/shadow_episode_summary.md`。預估不可得時明說,不留白。
    """
    if not isinstance(projection, dict):
        return ["  📅 達標預估：無(episode summary 未提供)"]
    if not projection.get("ok"):
        return [f"  📅 達標預估：無法計算({projection.get('reason', 'unknown')})"]

    milestone = (projection.get("milestones") or {}).get("minimum") or {}
    if milestone.get("beyond_horizon") or not milestone.get("eta_date"):
        return ["  📅 達標預估：以目前訊號率無法在預估視界內達標"]

    basis = projection.get("basis") or {}
    lines = [
        f"  📅 預估達 {milestone['threshold']} 筆：<b>{milestone['eta_date']}</b>"
        f"（約 {milestone['trading_days']} 個交易日）"
    ]
    low = milestone.get("eta_date_optimistic")
    high = milestone.get("eta_date_pessimistic")
    if low and high:
        lines.append(f"     區間 {low} ～ {high}(信心度 {projection.get('confidence')})")
    if basis:
        lines.append(
            f"     決定性管線 {basis.get('pipeline_open_episodes', '?')} 筆 + "
            f"每掃描日新增 {basis.get('completions_per_scan_day', '?')} 筆完成"
        )
    lines.append("     ⚠️ 動到 ConfigHash 會讓 cohort 歸零,預估同步作廢")
    return lines


def _blocked_v13_gate(reason: str, detail: str = "") -> dict:
    """Return a machine-readable fail-closed V1.3 gate."""
    return {
        "ok": False,
        "status": "blocked",
        "reason": reason,
        "detail": detail,
        "parameter_tuning_allowed": False,
        "global_analysis_allowed": False,
        "analysis_review_allowed": False,
        "power_ci_review_due": False,
        "parameter_change_authorized": False,
        "scope_model": "overall_gate_with_independent_segments",
        "all_segments_required_for_global": False,
        "expected": {
            "schema_version": Config.SNAPSHOT_SCHEMA_VERSION,
            "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
            "trade_plan_version": Config.TRADE_PLAN_VERSION,
            "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
            "config_hash": strategy_config_hash(),
            "minimum_completed": Config.EPISODE_TUNING_MIN_COMPLETED,
            "target_completed": Config.EPISODE_TUNING_TARGET,
            "segment_min_completed": Config.EPISODE_SEGMENT_MIN_COMPLETED,
        },
    }


def _nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load_v13_calibration_gate(
    summary_path: str | Path = REPORT_ROOT / "shadow_episode_summary.json",
) -> dict:
    """Load and strictly validate the current V1.3 episode maturity gate.

    Missing, malformed or mismatched summaries never inherit an older readiness
    decision. They return a blocked gate so report generation can continue while
    parameter tuning remains explicitly forbidden.
    """
    path = Path(summary_path)
    if not path.is_file():
        return _blocked_v13_gate("summary_missing", str(path))
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _blocked_v13_gate("summary_invalid_json", str(exc))
    except OSError as exc:
        return _blocked_v13_gate("summary_unreadable", str(exc))
    if not isinstance(summary, dict):
        return _blocked_v13_gate("summary_invalid_shape")

    cohort = summary.get("selection_cohort")
    expected_identity = {
        "schema_version": Config.SNAPSHOT_SCHEMA_VERSION,
        "trade_plan_version": Config.TRADE_PLAN_VERSION,
        "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
    }
    actual_identity = {
        key: summary.get(key)
        for key in expected_identity
    }
    if actual_identity != expected_identity:
        return _blocked_v13_gate(
            "version_mismatch",
            f"expected={expected_identity}; actual={actual_identity}",
        )
    expected_cohort = {
        "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
        "config_hash": strategy_config_hash(),
    }
    if not isinstance(cohort, dict) or {
        key: cohort.get(key) for key in expected_cohort
    } != expected_cohort:
        return _blocked_v13_gate(
            "cohort_mismatch",
            f"expected={expected_cohort}; actual={cohort}",
        )

    maturity = summary.get("maturity")
    if not isinstance(maturity, dict):
        return _blocked_v13_gate("invalid_maturity", "maturity must be an object")
    completed = maturity.get("completed_r")
    minimum = maturity.get("minimum_completed")
    target = maturity.get("target_completed")
    if (
        not _nonnegative_int(completed)
        or minimum != Config.EPISODE_TUNING_MIN_COMPLETED
        or target != Config.EPISODE_TUNING_TARGET
        or not _nonnegative_int(minimum)
        or not _nonnegative_int(target)
        or target < minimum
    ):
        return _blocked_v13_gate("invalid_maturity", f"actual={maturity}")

    expected_status = (
        "target_reached"
        if completed >= target
        else "minimum_reached"
        if completed >= minimum
        else "collecting"
    )
    # 母體一致性同樣是授權條件 —— SCAN_POOL 不在 ConfigHash 內,只靠 cohort
    # identity 擋不住成分股異動造成的混合樣本。缺欄或形狀不對一律 fail closed,
    # 不當作「沒問題」。
    universe = summary.get("universe_cohort")
    if not isinstance(universe, dict):
        return _blocked_v13_gate(
            "missing_universe_cohort",
            "summary 未提供 universe_cohort;無法證明樣本出自單一母體",
        )
    universe_consistent = universe.get("consistent")
    if not isinstance(universe_consistent, bool):
        return _blocked_v13_gate("invalid_universe_cohort", f"actual={universe}")
    if universe.get("current") != universe_version():
        return _blocked_v13_gate(
            "universe_version_mismatch",
            f"expected={universe_version()}; actual={universe.get('current')}",
        )

    expected_allowed = completed >= minimum and (
        universe_consistent or not Config.EPISODE_REQUIRE_SINGLE_UNIVERSE
    )
    expected_target_review = completed >= target and (
        universe_consistent or not Config.EPISODE_REQUIRE_SINGLE_UNIVERSE
    )
    analysis_review_allowed = maturity.get(
        "analysis_review_allowed",
        maturity.get("global_analysis_allowed"),
    )
    power_ci_review_due = maturity.get(
        "power_ci_review_due",
        expected_target_review,
    )
    parameter_change_authorized = maturity.get(
        "parameter_change_authorized",
        False,
    )
    if (
        maturity.get("stage") != expected_status
        or maturity.get("parameter_tuning_allowed") is not expected_allowed
        or maturity.get("global_analysis_allowed") is not expected_allowed
        or analysis_review_allowed is not expected_allowed
        or power_ci_review_due is not expected_target_review
        or parameter_change_authorized is not False
        or maturity.get("scope_model")
        != "overall_gate_with_independent_segments"
        or maturity.get("all_segments_required_for_global") is not False
        or maturity.get("segment_min_completed")
        != Config.EPISODE_SEGMENT_MIN_COMPLETED
        or maturity.get("remaining_to_minimum") != max(minimum - completed, 0)
        or maturity.get("remaining_to_target") != max(target - completed, 0)
    ):
        return _blocked_v13_gate("invalid_maturity", f"actual={maturity}")

    expected_scope = {
        "mode": "independent_after_global",
        "global_gate_requires_all_segments": False,
        "segment_min_completed": Config.EPISODE_SEGMENT_MIN_COMPLETED,
        "selected_legs": list(TUNING_SCOPE_SELECTED_LEGS),
        "order_types": list(TUNING_SCOPE_ORDER_TYPES),
    }
    supplied_scope = summary.get("segment_scope")
    if supplied_scope is not None and supplied_scope != expected_scope:
        return _blocked_v13_gate(
            "invalid_segment_scope",
            f"expected={expected_scope}; actual={supplied_scope}",
        )

    validated_segments = {}
    tuning_scopes = {
        "by_selected_leg": set(TUNING_SCOPE_SELECTED_LEGS),
        "by_order_type": set(TUNING_SCOPE_ORDER_TYPES),
    }
    for key, tuning_scope in tuning_scopes.items():
        segments = summary.get(key)
        if not isinstance(segments, list):
            return _blocked_v13_gate("invalid_segments", f"{key} must be a list")
        for segment in segments:
            if not isinstance(segment, dict):
                return _blocked_v13_gate("invalid_segments", f"{key} row must be an object")
            segment_completed = segment.get("completed_r")
            segment_minimum = segment.get("segment_min_completed")
            expected_in_scope = segment.get("segment") in tuning_scope
            supplied_in_scope = segment.get(
                "in_tuning_scope",
                expected_in_scope,
            )
            expected_ready = (
                expected_allowed
                and expected_in_scope
                and _nonnegative_int(segment_completed)
                and segment_completed >= Config.EPISODE_SEGMENT_MIN_COMPLETED
            )
            if (
                not _nonnegative_int(segment_completed)
                or segment_minimum != Config.EPISODE_SEGMENT_MIN_COMPLETED
                or supplied_in_scope is not expected_in_scope
                or segment.get("tuning_ready") is not expected_ready
            ):
                return _blocked_v13_gate(
                    "invalid_segments",
                    f"{key} inconsistent row={segment}",
                )
        validated_segments[key] = segments

    return {
        "ok": True,
        "status": expected_status,
        "reason": None,
        "parameter_tuning_allowed": expected_allowed,
        "global_analysis_allowed": expected_allowed,
        "analysis_review_allowed": expected_allowed,
        "power_ci_review_due": expected_target_review,
        "parameter_change_authorized": False,
        "scope_model": "overall_gate_with_independent_segments",
        "all_segments_required_for_global": False,
        "segment_min_completed": Config.EPISODE_SEGMENT_MIN_COMPLETED,
        "segment_scope": expected_scope,
        "completed_r": completed,
        "minimum_completed": minimum,
        "target_completed": target,
        "remaining_to_minimum": max(minimum - completed, 0),
        "remaining_to_target": max(target - completed, 0),
        "schema_version": summary["schema_version"],
        "signal_engine_version": cohort["signal_engine_version"],
        "trade_plan_version": summary["trade_plan_version"],
        "measurement_version": summary["measurement_version"],
        "config_hash": cohort["config_hash"],
        "universe_cohort": universe,
        "blocked_by_universe": bool(
            completed >= minimum and not expected_allowed
        ),
        # 達標預估為純資訊,原樣帶過不參與任何授權判斷。它若缺漏或損壞,
        # 顯示端自行降級 —— 絕不能因為預估算不出來就擋掉一個已合格的閘門。
        "projection": (
            summary.get("projection")
            if isinstance(summary.get("projection"), dict)
            else None
        ),
        **validated_segments,
    }


def refresh_v13_episode_artifacts(report_root: Path = REPORT_ROOT) -> dict:
    """Rebuild V1.3 shadow and episode artifacts from all archived snapshots.

    The weekly report calls this after archiving the current week's snapshots and
    before rendering. Any failure is returned to the caller so the visible gate
    can fail closed instead of silently reusing a stale summary.
    """
    archived_snapshots = sorted((report_root / "daily").glob("*.csv"))
    if not archived_snapshots:
        return {"ok": False, "reason": "no_daily_snapshots"}
    inventory = [
        archived_snapshot_eligibility(path)
        for path in archived_snapshots
    ]
    snapshots = [
        Path(item["snapshot"])
        for item in inventory
        if item["eligible"]
    ]
    incidents = [item for item in inventory if not item["eligible"]]
    if not snapshots:
        return {
            "ok": False,
            "reason": "no_usable_daily_snapshots",
            "incident_count": len(incidents),
        }

    performance_path = report_root / "shadow_performance.csv"
    episodes_path = report_root / "shadow_episodes.csv"
    summary_path = report_root / "shadow_episode_summary.json"
    markdown_path = report_root / "shadow_episode_summary.md"
    scan_dates_path = report_root / "usable_scan_dates.json"
    scan_dates_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dates": [path.stem for path in snapshots],
                "incident_dates": [Path(item["snapshot"]).stem for item in incidents],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    try:
        from track_shadow_performance import main as track_shadow_main
        from build_shadow_episodes import main as build_episodes_main

        shadow_result = track_shadow_main([
            *[str(path) for path in snapshots],
            "--output",
            str(performance_path),
        ])
        if shadow_result != 0:
            return {
                "ok": False,
                "reason": "shadow_refresh_failed",
                "exit_code": shadow_result,
            }

        episode_result = build_episodes_main([
            str(performance_path),
            "--episodes-output",
            str(episodes_path),
            "--summary-output",
            str(summary_path),
            "--markdown-output",
            str(markdown_path),
            "--scan-dates-json",
            str(scan_dates_path),
        ])
        if episode_result != 0:
            return {
                "ok": False,
                "reason": "episode_refresh_failed",
                "exit_code": episode_result,
            }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "episode_refresh_exception",
            "detail": f"{type(exc).__name__}:{exc}",
        }

    return {
        "ok": True,
        "summary_path": str(summary_path),
        "snapshot_count": len(snapshots),
        "incident_count": len(incidents),
        "legacy_without_health_count": sum(
            bool(item.get("legacy_without_health"))
            for item in inventory
        ),
    }


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
                  week_start: str, week_end: str,
                  v13_gate: dict | None = None,
                  operations: dict | None = None) -> str:
    daily = agg["daily"]
    n_days = len(daily)
    operations = operations or {
        "scheduled_days": n_days,
        "usable_days": n_days,
        "incident_days": 0,
        "incident_dates": [],
    }
    v13_gate = v13_gate or _blocked_v13_gate("summary_not_supplied")
    if n_days == 0:
        if v13_gate.get("ok"):
            gate_line = (
                f"V1.3 completed-R {v13_gate['completed_r']}/"
                f"{v13_gate['minimum_completed']}；本週無新快照，不變更調參狀態。"
            )
        else:
            gate_line = (
                f"V1.3 gate unavailable ({v13_gate.get('reason', 'unknown')})；"
                "fail-closed 禁止調參。"
            )
        return (
            f"<b>📋 美股掃描週報(觀察期)</b>  {week_start} ~ {week_end}\n\n"
            f"排程 {operations['scheduled_days']} 日 | 可用 0 日 | "
            f"事故 {operations['incident_days']} 日\n"
            f"🚨 本週 <b>0 次</b>可用掃描紀錄 — 排程可能漏跑或 health 擋下,"
            f"請檢查 Actions 是否有執行/失敗。\n\n{gate_line}"
        )

    avg = lambda k: sum(d[k] for d in daily) / n_days

    phase = (
        "校準期"
        if v13_gate.get("ok") or (calib or {}).get("n_r", 0) > 0
        else "觀察期"
    )
    lines = [f"<b>📋 美股掃描週報({phase})</b>  {week_start} ~ {week_end}",
             f"排程 {operations['scheduled_days']} 日 | "
             f"可用 <b>{operations['usable_days']}</b> 日 | "
             f"事故 {operations['incident_days']} 日",
             f"可用掃描天數 <b>{n_days}</b> | 日均分析 {avg('n'):.0f} 檔", "",
             "<b>分數分布(日均)</b>",
             f"  ≥7 達門檻:{avg('ge7'):.1f} 檔(週總 {agg['total_ge7']})",
             f"  6 分(差1分):{avg('eq6'):.1f} 檔",
             f"  5 分:{avg('eq5'):.1f} 檔",
             f"  3–4 分:{avg('b34'):.1f} 檔",
             f"  &lt;0 反向警告:{avg('warn'):.1f} 檔", ""]
    if operations.get("incident_dates"):
        lines.insert(
            3,
            "  ⚠️ incident: " + ", ".join(operations["incident_dates"]),
        )

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

    # ── V1.3 唯一調參閘門(completed-R episodes,版本與 cohort fail-closed)──
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🧪 V1.3 調參閘門</b>(shadow completed-R episodes)")
    if v13_gate.get("ok"):
        completed = v13_gate["completed_r"]
        minimum = v13_gate["minimum_completed"]
        target = v13_gate["target_completed"]
        lines.append(
            f"  V1.3 completed-R <b>{completed}</b>/{minimum}"
            f"（目標 {target}）| {v13_gate['status']}"
        )
        lines.append(
            f"  Engine {v13_gate['signal_engine_version']} | "
            f"TradePlan {v13_gate['trade_plan_version']} | "
            f"Measurement {v13_gate['measurement_version']}"
        )
        lines.append(
            f"  Schema {v13_gate['schema_version']} | "
            f"ConfigHash {v13_gate['config_hash']}"
        )
        if v13_gate["status"] == "target_reached":
            lines.append(
                "  ✅ 已達 100 筆目標；下一步是 power/CI 與正式升版審查，"
                "不自動修改參數。"
            )
        elif v13_gate["parameter_tuning_allowed"]:
            lines.append(
                "  🟡 已達 60 筆全體門檻，只開放授權分析審查；"
                "每個策略腿／order type 各自滿 20 才可判讀該 segment。"
            )
            lines.append(
                "     五個 segment 不是 global gate 的聯合阻擋條件。"
            )
        elif v13_gate.get("blocked_by_universe"):
            universe = v13_gate.get("universe_cohort") or {}
            lines.append(
                f"  ⛔ 樣本數已達 {v13_gate['minimum_completed']} 筆，"
                "但選股母體不一致 —— 仍禁止調參。"
            )
            lines.append(
                f"     原因 {universe.get('reason', 'unknown')}；"
                f"cohort 內有 {universe.get('distinct', '?')} 套 UniverseVersion"
            )
            lines.append(
                "     SCAN_POOL 不在 ConfigHash 內,需人工決定接受混合或重新起算。"
            )
        else:
            lines.append(
                f"  🔒 尚差 {v13_gate['remaining_to_minimum']} 筆；"
                "未達 60 前禁止調參。"
            )
        lines.extend(_v13_projection_lines(v13_gate.get("projection")))
    else:
        reason = v13_gate.get("reason", "unknown")
        lines.append(f"  ⛔ V1.3 gate unavailable ({reason})")
        lines.append("  🔒 fail-closed：禁止調參，不沿用舊週或 legacy readiness。")
    lines.append("")

    # ── legacy-v0 歷史 baseline；不再授權任何 V1.3 調參──
    calib = calib or {}
    if calib.get("ok") and calib.get("n_r", 0) > 0:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(
            "<b>📎 歷史校準 baseline</b>"
            "(legacy-v0:R模擬 D+5收盤出場/觸停損-1R)"
        )
        lines.append(f"  全期已定案 <b>{calib['n_r']}</b>/{calib['n_total']} 筆")
        engine_stats = calib.get("engine_stats", {})
        v12 = engine_stats.get("v1_2", {})
        legacy = engine_stats.get("legacy", {})
        unknown = engine_stats.get("unclassified", {})
        if engine_stats:
            lines.append(
                f"  V1.2.x legacy-v0:{v12.get('n_r', 0)}/"
                f"{v12.get('n_total', 0)} 筆 | 舊 D8 樣本 "
                f"{v12.get('n_r', 0)}/{LEGACY_CALIBRATION_MIN_R}"
                + ("（歷史完整）" if legacy_v12_sample_complete(calib) else "")
            )
            lines.append(
                f"  舊引擎 baseline:{legacy.get('n_r', 0)}/"
                f"{legacy.get('n_total', 0)} 筆"
            )
            if unknown.get("n_total", 0):
                lines.append(
                    f"  ⚠️ 未分類:{unknown.get('n_r', 0)}/"
                    f"{unknown['n_total']} 筆(不計入歷史 D8)"
                )
        else:
            lines.append("  ⚠️ 引擎分代資料不可用，歷史 D8 不判定")
        lines.append(f"  全期 R期望值 <b>{calib['r_mean']:+.2f}</b> | "
                     f"勝率 {calib['win_rate']:.0%} | "
                     f"停損率 {calib['stop_rate']:.0%}")
        if v12.get("n_r", 0):
            lines.append(
                f"  V1.2.x legacy-v0 R期望值 <b>{v12['r_mean']:+.2f}</b> | "
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

    # 週報判讀只服從 V1.3 gate；legacy 與分數分布均不得解鎖調參。
    if not v13_gate.get("ok"):
        lines.append(
            "💡 <i>V1.3 成熟度不可驗證；依 fail-closed 規則禁止調整權重、"
            "量縮門檻或 MIN_PRIORITY_FOR_GO。</i>"
        )
    elif v13_gate["status"] == "target_reached":
        lines.append(
            "💡 <i>V1.3 已達 100 筆 completed-R 目標；下一步是 power/CI、"
            "獨立驗證與升版決策。任何參數變更必須建立新 ConfigHash/cohort。</i>"
        )
    elif v13_gate["parameter_tuning_allowed"]:
        lines.append(
            "💡 <i>V1.3 已達 60 筆 completed-R 最低門檻；只可開始全體授權分析審查。"
            "各 segment 須各自滿 20 才可個別判讀，五個 segment 不聯合阻擋 global gate。</i>"
        )
    else:
        lines.append(
            f"💡 <i>V1.3 completed-R 累積中"
            f"({v13_gate['completed_r']}/{v13_gate['minimum_completed']});"
            "未達門檻前禁止調參。legacy-v0 只作歷史 baseline。</i>"
        )

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
                       v13_gate: dict | None = None,
                       operations: dict | None = None,
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
        # 5:新增 scheduled/usable/incident operational day counts。
        "schema_version": 5,
        "generated_at_utc": generated_at,
        "period": {"start": week_start, "end": week_end, "timezone": "America/New_York"},
        "sources": {
            "scan_csv": archived_csv_files,
            "notion_database": os.environ.get("NOTION_DB_ID", ""),
            "shadow_episode_summary": "reports/shadow_episode_summary.json",
            "telegram": "delivery_only",
        },
        "aggregate": _json_safe(agg),
        "operations": _json_safe(operations or {}),
        "notion_samples": _json_safe(notion),
        "calibration": _json_safe(calib),
        "v13_calibration_gate": _json_safe(
            v13_gate or _blocked_v13_gate("summary_not_supplied")
        ),
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
        package = download_artifact(artifact["id"])
        if package is not None:
            downloaded.append({**artifact, **package})
    resolved = [
        {**artifact, "health": resolve_artifact_health(artifact)}
        for artifact in downloaded
    ]
    incident_files = []
    for artifact in resolved:
        if not health_report_allows_calibration(artifact.get("health")):
            incident_files.extend(archive_incident_artifact(artifact))
    selected_artifacts = select_daily_artifacts(resolved)
    operations = operational_week_summary(
        week_start,
        week_end,
        selected_artifacts,
    )
    days = []
    archived_csv_files = []
    for artifact in selected_artifacts:
        full_snapshot = artifact["frame"]
        archived_csv_files.append(
            archive_daily_csv(full_snapshot, artifact["et_date"])
        )
        archive_daily_health(artifact.get("health"), artifact["et_date"])
        archive_runtime_provenance(
            artifact.get("runtime_provenance"),
            artifact["et_date"],
        )
        data = snapshot_data_rows(full_snapshot)
        if (
            artifact.get("health_usable")
            and not data.empty
            and "Priority" in data.columns
        ):
            days.append((artifact["et_date"], data))
            print(f"  ✅ {artifact['et_date']}:{len(data)} 檔")
        else:
            print(f"  ⚠️ {artifact['et_date']}:incident 已封存，不納入彙總/校準")

    agg = aggregate(days) if days else {"daily": [], "top5": [],
                                        "eq6_regulars": [], "warn_regulars": [],
                                        "total_ge7": 0}
    refresh = refresh_v13_episode_artifacts(REPORT_ROOT)
    if refresh.get("ok"):
        v13_gate = load_v13_calibration_gate(refresh["summary_path"])
    else:
        v13_gate = _blocked_v13_gate(
            refresh.get("reason", "episode_refresh_failed"),
            refresh.get("detail", ""),
        )
    if v13_gate.get("ok"):
        print(
            "🧪 V1.3 gate:"
            f"{v13_gate['completed_r']}/{v13_gate['minimum_completed']} "
            f"({v13_gate['status']})"
        )
    else:
        print(f"⛔ V1.3 gate fail-closed:{v13_gate['reason']}")

    notion = notion_sample_counts(week_start, week_end)
    calib  = notion_calibration_stats()
    msg = build_message(
        agg, notion, calib, week_start, week_end, v13_gate, operations
    )
    report_files = write_report_files(
        msg,
        agg,
        notion,
        calib,
        week_start,
        week_end,
        archived_csv_files,
        v13_gate,
        operations,
    )
    if incident_files:
        print(f"🧯 incidents 已永久保存:{', '.join(incident_files)}")
    print(f"🗂️  週報已永久保存:{', '.join(report_files)}")

    print("─" * 40 + "\n" + msg.replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", "").replace("&lt;", "<") + "\n" + "─" * 40)

    from outputs import send_telegram   # 重用既有推播(3 次重試)
    send_telegram(msg)


if __name__ == "__main__":
    main()
