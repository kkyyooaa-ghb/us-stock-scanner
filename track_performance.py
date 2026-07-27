"""
track_performance.py — 績效回填(P1,美股版)V1.0.0
血統:對齊台股 V13.9.3 Stage 2/3「回填欄由獨立腳本寫」設計;美股版新寫。

量尺版本:legacy-v0。V1.3 shadow 使用 execution_measurement.py，
兩者輸出不得混算。

目的:把 Notion「美股掃描」DB 既有精選樣本的績效欄位回填,讓週報從
     「觀察期版」升級為「完整校準報告」(R期望值/勝率/n進度),並為
     V1.2.0 計分核心重寫提供舊引擎 baseline(D8:數據先、規則後)。

回填欄位(DB 欄已建好;本腳本只寫以下幾欄,絕不動掃描時寫入的欄):
  掃描日收盤價     D+0(掃描日)收盤價(yfinance auto_adjust 調整後價)
  D+1/D+3/D+5報酬%  = D+N收盤 / 掃描日收盤 − 1,單位百分點(2.34 = +2.34%)。
                    D+N 以「交易日」計 — 用該檔自身 K 線序列對齊,自動跳過假日
  是否觸發停損     模擬交易期間(D+0~D+5,進場發生在掃描日故含 D+0)
                    任一日最低價 ≤ 停損價
  R值             模擬交易:entry=進場參考價、stop=停損價
                    觸發停損 → R = −1
                    未觸發   → R = (D+5收盤 − entry) / (entry − stop)
                    進場參考價缺(⚠️已偏離檔)或 entry≤stop → R 留空
  回顧備註         僅在原本為空時寫入自動註記(絕不覆蓋人工筆記):
                    期間最低價未觸及進場參考價 → 標注「模擬R僅供參考」

冪等/增量:
  只抓「D+5報酬% 為空」的 pages。D+5 尚未走完的樣本,每次回填當下可算的
  部分(掃描日收盤價/D+1/D+3);走完後補 D+5 + R + 停損 → 之後自然不再入選。
  同一筆重跑結果相同(確定性),重複執行無副作用。

排程:weekly_report.yml 於週報步驟之前執行(週日台北 21:00 = 美東週日上午,
     休市中,無盤中未完成 K 線問題);平日手動補跑另有「當日盤中 bar 剔除」防呆。
用法:python track_performance.py(需 env:NOTION_TOKEN / NOTION_DB_ID)
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from config import Config
from sources import get_series, ET_TZ

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

NOTION_API     = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")


def _headers() -> dict:
    return {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Content-Type":   "application/json",
        "Notion-Version": NOTION_VERSION,
    }


# ==========================================================================
# 1. 撈待回填 pages(D+5報酬% 為空)
# ==========================================================================
def _parse_page(pg: dict):
    pr = pg.get("properties", {})

    def _num(key):
        return pr.get(key, {}).get("number")

    title_arr = pr.get("記錄日期", {}).get("title", [])
    title = title_arr[0].get("plain_text", "") if title_arr else ""

    sel = pr.get("股票代號", {}).get("select") or {}
    ticker = (sel.get("name") or "").strip().upper()
    if not ticker and "_" in title:                    # select 缺時從 title 解析
        ticker = title.split("_", 1)[1].strip().upper()

    date_obj = pr.get("掃描日期", {}).get("date") or {}
    scan_date = (date_obj.get("start") or "")[:10]
    if not scan_date and len(title) >= 10:
        scan_date = title[:10]

    if not ticker or not scan_date:
        print(f"  ⚠️  略過無法解析的 page:{title or pg.get('id')}")
        return None

    note_arr = pr.get("回顧備註", {}).get("rich_text", [])
    return {
        "page_id":    pg["id"],
        "title":      title or f"{scan_date}_{ticker}",
        "ticker":     ticker,
        "scan_date":  scan_date,
        "entry":      _num("進場參考價"),
        "stop":       _num("停損價"),
        "note_empty": len(note_arr) == 0,
    }


def fetch_pages_needing_backfill() -> list:
    url = f"{NOTION_API}/databases/{NOTION_DB_ID}/query"
    out, cursor = [], None
    for _ in range(20):    # 上限 2000 筆,遠夠
        body = {
            "filter": {"property": "D+5報酬%", "number": {"is_empty": True}},
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=_headers(), json=body, timeout=20)
        r.raise_for_status()
        j = r.json()
        for pg in j.get("results", []):
            row = _parse_page(pg)
            if row:
                out.append(row)
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
    return out


# ==========================================================================
# 2. 股價歷史(批次一次抓,涵蓋最早掃描日往前 7 天 buffer)
# ==========================================================================
def download_history(tickers: list, earliest_scan: str) -> pd.DataFrame:
    start = (datetime.strptime(earliest_scan, "%Y-%m-%d")
             - timedelta(days=7)).strftime("%Y-%m-%d")
    print(f"  📡 抓 {len(tickers)} 檔日線(自 {start})...")
    return yf.download(sorted(set(tickers)), start=start,
                       progress=False, auto_adjust=True)


def _drop_partial_today(df: pd.DataFrame) -> pd.DataFrame:
    """平日盤中手動補跑防呆:美東 17:00 前剔除當日未完成 bar(同 main.py 慣例)"""
    if df is None or df.empty:
        return df
    now = datetime.now(ET_TZ)
    if now.hour < 17 and df.index[-1].strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d"):
        return df.iloc[:-1]
    return df


# ==========================================================================
# 3. 單筆回填計算(純函式,可離線測試)
# ==========================================================================
def compute_backfill(scan_date: str, close: pd.Series, low: pd.Series,
                     entry, stop) -> dict:
    """
    以該檔自身 K 線序列對齊交易日:D+N = 掃描日之後第 N 根 bar。
    回傳:
      ok / err
      base_close       掃描日收盤價
      ret1/ret3/ret5    報酬(百分點;窗未到 → None)
      window_complete   D+5 是否已走完
      valid_trade       entry/stop 是否構成有效模擬交易
      stop_hit          D+0~D+5 任一日 low ≤ stop(僅 valid_trade 時判)
      entry_touched     D+0~D+5 最低價是否觸及 entry(僅 valid_trade 時判)
      r_value           R 值(觸發停損 −1;未觸發 (D+5收−entry)/(entry−stop))
      r_final           R/停損欄是否已可定案寫入(停損已觸發 或 窗已走完)
    """
    dates = [d.strftime("%Y-%m-%d") for d in close.index]
    if scan_date not in dates:
        return {"ok": False, "err": f"掃描日 {scan_date} 無K線(資料缺或非交易日)"}
    i0 = dates.index(scan_date)
    base = float(close.iloc[i0])
    if base <= 0:
        return {"ok": False, "err": "掃描日收盤價異常(≤0)"}

    out = {
        "ok": True,
        "measurement_version": Config.MEASUREMENT_VERSION,
        "base_close": round(base, 2),
    }

    def _ret(n: int):
        j = i0 + n
        if j < len(close):
            return round((float(close.iloc[j]) / base - 1) * 100, 2)
        return None

    out["ret1"] = _ret(1)
    out["ret3"] = _ret(3)
    out["ret5"] = _ret(5)
    window_complete = out["ret5"] is not None
    out["window_complete"] = window_complete

    # ── 模擬交易(entry=進場參考價;含 D+0,因進場發生在掃描日)──
    valid_trade = (entry is not None and stop is not None
                   and float(entry) > 0 and float(stop) > 0
                   and float(entry) > float(stop))
    out["valid_trade"] = valid_trade

    j_end = min(i0 + 5, len(close) - 1)
    lows = low.loc[close.index[i0]:close.index[j_end]].dropna().astype(float)

    stop_hit = False
    entry_touched = False
    r_value = None
    if valid_trade and len(lows) > 0:
        entry_f, stop_f = float(entry), float(stop)
        low_min = float(lows.min())
        stop_hit      = low_min <= stop_f
        entry_touched = low_min <= entry_f
        if stop_hit:
            r_value = -1.0
        elif window_complete:
            d5_close = float(close.iloc[i0 + 5])
            r_value = round((d5_close - entry_f) / (entry_f - stop_f), 2)

    out["stop_hit"]      = stop_hit
    out["entry_touched"] = entry_touched
    out["r_value"]       = r_value
    out["r_final"]       = stop_hit or window_complete
    return out


# ==========================================================================
# 4. 組 Notion properties + 寫入
# ==========================================================================
def build_props(page: dict, res: dict) -> dict:
    props = {"掃描日收盤價": {"number": res["base_close"]}}
    if res["ret1"] is not None:
        props["D+1報酬%"] = {"number": res["ret1"]}
    if res["ret3"] is not None:
        props["D+3報酬%"] = {"number": res["ret3"]}
    if res["ret5"] is not None:
        props["D+5報酬%"] = {"number": res["ret5"]}

    # R / 停損:只在可定案時寫(停損已觸發 或 D+5 窗走完),且需為有效模擬交易
    if res["r_final"] and res["valid_trade"]:
        props["是否觸發停損"] = {"checkbox": bool(res["stop_hit"])}
        if res["r_value"] is not None:
            props["R值"] = {"number": res["r_value"]}

    # 回顧備註:僅原本為空時寫自動註記(絕不覆蓋人工筆記)
    if res["r_final"] and page.get("note_empty"):
        notes = []
        if res["valid_trade"] and not res["entry_touched"]:
            notes.append("⚠️ D+0~D+5 未觸及進場參考價,模擬R僅供參考")
        if not res["valid_trade"]:
            notes.append("進場/停損價無法構成有效模擬(缺漏或 entry≤stop),R不計")
        if notes:
            props["回顧備註"] = {
                "rich_text": [{"text": {"content": " | ".join(notes)}}]
            }
    return props


def update_page(page_id: str, props: dict) -> bool:
    for attempt in range(3):
        try:
            r = requests.patch(f"{NOTION_API}/pages/{page_id}",
                               headers=_headers(),
                               json={"properties": props}, timeout=15)
            if r.status_code == 200:
                return True
            print(f"    Notion 回填失敗:HTTP {r.status_code} {r.text[:150]}")
        except Exception as e:
            print(f"    Notion 回填例外(第 {attempt+1} 次):{e}")
        time.sleep(2)
    return False


# ==========================================================================
# 5. 主流程
# ==========================================================================
def main() -> int:
    print(f"📐 track_performance 回填 V1.0.0/{Config.MEASUREMENT_VERSION}  "
          f"{datetime.now(ET_TZ).strftime('%Y-%m-%d %H:%M')} ET")

    if not NOTION_TOKEN or not NOTION_DB_ID:
        print("  ⚠️  缺 NOTION_TOKEN / NOTION_DB_ID,跳過回填(不視為錯誤)")
        return 0
    if not HAS_YF:
        print("  ❌ yfinance 未安裝")
        return 1

    try:
        pages = fetch_pages_needing_backfill()
    except Exception as e:
        print(f"  ❌ Notion 查詢失敗:{e}")
        return 1

    print(f"  待回填:{len(pages)} 筆(D+5報酬% 為空)")
    if not pages:
        print("✅ 全部樣本已回填完畢,無事可做")
        return 0

    earliest = min(p["scan_date"] for p in pages)
    tickers  = sorted({p["ticker"] for p in pages})
    data = download_history(tickers, earliest)
    if data is None or data.empty:
        print("  ❌ 股價下載失敗")
        return 1
    data = _drop_partial_today(data)

    done = partial = skipped = failed = 0
    for p in pages:
        close = get_series(data, "Close", p["ticker"])
        low   = get_series(data, "Low",   p["ticker"])
        if close.empty or low.empty:
            print(f"  ⚠️  {p['title']}:無K線資料,略過")
            skipped += 1
            continue

        res = compute_backfill(p["scan_date"], close, low,
                               p["entry"], p["stop"])
        if not res["ok"]:
            print(f"  ⚠️  {p['title']}:{res['err']}")
            skipped += 1
            continue

        props = build_props(p, res)
        if update_page(p["page_id"], props):
            if res["r_final"]:
                r_s = (f"R{res['r_value']:+.2f}" if res["r_value"] is not None
                       else "R不計")
                sl  = " 🛑停損" if res["stop_hit"] else ""
                print(f"  ✅ {p['title']:<18} D+5 {res['ret5']:+.2f}%  {r_s}{sl}")
                done += 1
            else:
                have = [s for s, v in (("D+1", res["ret1"]),
                                       ("D+3", res["ret3"])) if v is not None]
                print(f"  ⏳ {p['title']:<18} 窗未走完,先回填 "
                      f"{'/'.join(have) or '掃描日收盤'}")
                partial += 1
        else:
            failed += 1
        time.sleep(0.35)   # Notion rate limit(3 req/s)保守節流

    print(f"\n📊 回填結果:定案 {done} | 部分 {partial} | "
          f"略過 {skipped} | 失敗 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
