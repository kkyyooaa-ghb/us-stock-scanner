"""
美股掃描週報(觀察期版)V1.0.0
================================
目的:觀察期的每週數據彙總 — 回答「精選 0 檔是否結構性?分數分布長怎樣?
     MIN_PRIORITY_FOR_GO=7 初始值該定哪?」這組問題。
     等 P1(track_performance 回填)上線後,本報告升級為完整校準報告
     (加 R 期望值 / 勝率 / n≥15 進度),對齊台股週日校準節奏。

資料源:
  1. 本 repo 的 scan-result-* artifacts(GitHub API,內建 GITHUB_TOKEN,
     actions:read 即可,不需任何新憑證)— 每日全 99 檔分數
  2. Notion 美股掃描 DB(可選,讀 picks 累計數,失敗優雅跳過)

去重:同一美東日多次執行(手動補跑)只取「最後一次」artifact。
排程:weekly_report.yml 每週日 13:00 UTC = 台北 21:00(台北無 DST,恆定)。
"""
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone

import requests

try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:
    ET_TZ = timezone(timedelta(hours=-4))

GH_API = "https://api.github.com"


# ==========================================================================
# 1. 抓本週 artifacts
# ==========================================================================
def fetch_week_artifacts() -> list[dict]:
    """列出近 7 天的 scan-result artifacts,回傳 [{id, created_at, et_date}]"""
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

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
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
        if created < cutoff:
            continue
        out.append({
            "id":         a["id"],
            "created_at": created,
            "et_date":    created.astimezone(ET_TZ).strftime("%Y-%m-%d"),
        })

    # 同一美東日只取最後一次(手動補跑去重)
    by_date: dict[str, dict] = {}
    for a in out:
        cur = by_date.get(a["et_date"])
        if cur is None or a["created_at"] > cur["created_at"]:
            by_date[a["et_date"]] = a
    deduped = sorted(by_date.values(), key=lambda x: x["et_date"])
    print(f"📦 近 7 天 artifacts:{len(out)} 個 → 去重後 {len(deduped)} 個交易日")
    return deduped


def download_csv(artifact_id: int):
    """下載單一 artifact zip → 解出 scan_result.csv → pandas DataFrame(或 None)"""
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
        # 排除崩潰保底列
        if "Ticker" in df.columns:
            df = df[df["Ticker"].astype(str) != "ERROR"]
        if df.empty or "Priority" not in df.columns:
            return None
        return df
    except Exception as e:
        print(f"⚠️  artifact {artifact_id} 下載/解析失敗:{e}")
        return None


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
def notion_sample_counts(week_start: str) -> dict:
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
        week = _count({"filter": {"property": "掃描日期",
                                  "date": {"on_or_after": week_start}}})
        total = _count({})
        return {"ok": True, "week_count": week, "total_count": total}
    except Exception as e:
        print(f"⚠️  Notion 計數失敗(略過該段):{e}")
        return {"ok": False}


# ==========================================================================
# 4. 組訊息 + 推播
# ==========================================================================
def build_message(agg: dict, notion: dict,
                  week_start: str, week_end: str) -> str:
    daily = agg["daily"]
    n_days = len(daily)
    if n_days == 0:
        return (f"<b>📋 美股掃描週報(觀察期)</b>  {week_start} ~ {week_end}\n\n"
                f"🚨 本週 <b>0 次</b>掃描紀錄 — 排程可能漏跑,"
                f"請檢查 Actions 是否有執行/失敗。")

    avg = lambda k: sum(d[k] for d in daily) / n_days

    lines = [f"<b>📋 美股掃描週報(觀察期)</b>  {week_start} ~ {week_end}",
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
                     f"累計 <b>{notion['total_count']}/15</b>(校準門檻)")
    else:
        lines.append("<b>Notion 樣本</b>  讀取略過")
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

    # 觀察期判讀(規則式,不做主觀建議)
    if agg["total_ge7"] == 0:
        lines.append("💡 <i>本週 0 檔達 7 分門檻。法人腿停用下,達標僅剩"
                     "「吸籌+季營收+主題」一路;6 分常客即是門檻定值的候選證據。"
                     "累積 2 週以上分布後再議 MIN_PRIORITY_FOR_GO(D8:數據先、規則後)。</i>")
    else:
        lines.append("💡 <i>本週已有達門檻精選 — 等 P1 報酬回填上線後,"
                     "週報將升級為完整校準報告(R 期望值/勝率/n 進度)。</i>")

    return "\n".join(lines)


def main():
    now_et = datetime.now(ET_TZ)
    week_end   = now_et.strftime("%Y-%m-%d")
    week_start = (now_et - timedelta(days=6)).strftime("%Y-%m-%d")
    print(f"📋 週報區間(ET):{week_start} ~ {week_end}")

    arts = fetch_week_artifacts()
    days = []
    for a in arts:
        df = download_csv(a["id"])
        if df is not None:
            days.append((a["et_date"], df))
            print(f"  ✅ {a['et_date']}:{len(df)} 檔")
        else:
            print(f"  ⚠️ {a['et_date']}:CSV 無效,略過")

    agg = aggregate(days) if days else {"daily": [], "top5": [],
                                        "eq6_regulars": [], "warn_regulars": [],
                                        "total_ge7": 0}
    notion = notion_sample_counts(week_start)
    msg = build_message(agg, notion, week_start, week_end)

    print("─" * 40 + "\n" + msg.replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", "").replace("&lt;", "<") + "\n" + "─" * 40)

    from outputs import send_telegram   # 重用既有推播(3 次重試)
    send_telegram(msg)


if __name__ == "__main__":
    main()
