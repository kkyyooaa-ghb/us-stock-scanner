"""
llm_enrichment.py — LLM 精選股深度摘要(V1.3 P0 美股化 / P7.5)

設計理念:
  09:00 ET 規則型選股完成後,LLM 對精選標的每檔產 200 字摘要,寫入 Notion。
  規則型抓不到的維度(財測下修、SEC 調查、大單、突發事件)由 LLM 補強。

================================================================
V1.3 P0 改動(2026-08-06 美股化,原模組自台股版移植後從未改市場)
================================================================
  病灶:query 硬加「台股」、`include_domains` 全為台灣財經媒體
  (cnyes/moneydj/udn 等)、prompt 自稱「台股研究助手」。對 NDX 成分股
  近乎零命中,且撈到的少量結果是錯市場新聞,會污染人工判讀。因此
  `Config.LLM_ENRICHMENT_ENABLED` 自 V1.3 起被設為 False,整段是死碼。

  修法:
    a) query 改 `公司英文名 (TICKER) stock news`;新增 Config.COMPANY_NAMES
       解析 99 檔成分股名稱,查無則 fallback 純 ticker(不拋錯)
    b) include_domains 改美國財經媒體 + 新聞稿線 + sec.gov
    c) prompt 改美股研究助手:英文輸入、繁體中文輸出、美股風險/催化語彙,
       並新增標的比對規則(美股 ticker 歧義高)
    d) 時區 UTC+8 → ET,與 sources/snapshot_schema/weekly_report 一致
    e) 重新啟用 LLM_ENRICHMENT_ENABLED;env 快關能力保留

  選股中性:本模組只寫 Notion「LLM 摘要」欄,不參與分數、腿別、Top 10、
  TradePlan 或每日 CSV 快照,亦不進 strategy_config_hash / universe_version。
================================================================

哲學:
  - 規則型優先,LLM 補充不取代:09:25 主流程零變更,本模組只在末段補摘要
  - 失敗優雅:任一階段失敗 → 該檔留空白,主流程不受影響
  - 校準在實戰後:第一週只寫 Notion,不發 TG(觀察品質後再上)
  - 防幻覺:Prompt 嚴格要求「只能根據提供的新聞」+ 結構化 JSON 輸出

================================================================
V13.11.2 改動(2026-05-25 兩個邊界 bug 修正)
================================================================
  Bug 1:LLM 摘要日期過舊
    5/25 真實跑 2882.TW 國泰金摘要返回「2023 年 Q2 利息收入」(3 年前舊聞)。
    根因:Tavily `days=3` 是「優先返回」不是「硬過濾」,當近 3 天無新聞時
    fallback 撈較舊庫存;PROMPT L162「事件過 7 天可降低權重但仍列入」太寬容,
    Gemini 把舊聞當主摘要;程式端沒做 published_date 二次過濾。

    修法(三道防線):
      a) 程式端硬過濾:NEWS_MAX_AGE_DAYS = 14 天,published_date 缺失保留
      b) PROMPT 強化:14 天前忽略 + highlight 取最新日期 + 全無近期則明示
      c) PROMPT 強制 minified JSON(無換行無縮排,避免 pretty-print 浪費 token)

  Bug 2:Gemini JSON 截斷率仍高
    5/25 真實跑 3/8 檔仍因 JSON 截斷失敗(2376/3017/3231),雖然 V13.11.1
    已從 500 提到 1500。根因:Gemini Flash 啟用 response_mime_type=JSON 後
    傾向 pretty-print(縮排換行),token 用量 2~3 倍。

    修法:
      a) max_output_tokens 1500 → 3000(雙保險,Flash 免費 token 不要錢)
      b) PROMPT 強制 minified(配合上面修法 c,Gemini 縮排率會降)
================================================================

技術選型:
  - LLM:Google Gemini 2.5 Flash(Free tier 1,500 RPD,≤10 檔/天 × 22 天/月
    = 220 次,完全免費)
  - 新聞:Tavily(免費 1,000 次/月;≤10 檔/天 × 22 天 = 220 次,在額度內)
  - SDK:google-genai(新版,舊 google-generativeai 已 deprecated)
  - Notion API:沿用既有 REST(不依賴 outputs.py)

時間預算:
  - df_go 上限為 Config.TOP_N_RECOMMENDED = 10 檔
  - 10 檔 × (Tavily 5s + Gemini 15s + Notion 1s) ≈ 210s
  - 整段 timeout 預留 300s(scan.yml job timeout 為 20 分鐘,餘裕充足)

環境變數:
  - GEMINI_API_KEY        (必需)
  - TAVILY_API_KEY        (必需)
  - LLM_ENRICHMENT_ENABLED (可選,預設依 Config 開關;失敗時設 "false" 可快速關閉)
  - NOTION_TOKEN          (沿用既有)
  - NOTION_DB_ID          (沿用既有)

模組對外介面:
  run_llm_enrichment_phase(picks_df, scan_date) — 主入口,在 main.py 末段呼叫
  其他函式為內部 helper,可獨立單元測試
"""
import os
import re
import json
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from config import Config


# ============================================================
# 0. 內部常數與工具
# ============================================================
NOTION_API_BASE   = "https://api.notion.com/v1"
NOTION_VERSION    = "2022-06-28"
NOTION_TIMEOUT    = 15
TAVILY_TIMEOUT    = 10
GEMINI_TIMEOUT    = 30

# V1.3 P0:時區改 ET,與 sources/snapshot_schema/weekly_report 一致。
#   本模組在 09:00 ET 盤前掃描末段執行,新聞也是美東時間發佈;沿用台股版的
#   UTC+8 會讓「今天」與時效 cutoff 相對新聞來源整體平移 12~13 小時。
ET_TZ = ZoneInfo("America/New_York")

# V13.11.2:新聞日期硬過濾門檻(天)
#   5/25 暴露:摘要返回 2023 Q2 舊聞,Tavily days=3 是「優先」不是「硬過濾」。
#   14 天平衡:嚴一點避免舊聞,鬆一點容忍一週沒新聞的情況。
#   published_date 缺失的新聞保留(信任 Tavily 預設過濾)。
NEWS_MAX_AGE_DAYS = 14

# V1.3 P0:美股新聞源。原清單為 cnyes/moneydj/udn 等台灣財經媒體,對美股
# 標的近乎零命中。此處含三類來源:
#   1. 主流財經媒體 — 一般報導與分析
#   2. 新聞稿線(businesswire/prnewswire/globenewswire)— 財報、財測、
#      大單、新品的第一手原文,正是 prompt 要抓的 risk/catalyst
#   3. sec.gov — 8-K 重大事件與內部人交易
US_NEWS_DOMAINS = [
    "reuters.com", "cnbc.com", "bloomberg.com", "wsj.com",
    "marketwatch.com", "barrons.com", "investors.com",
    "finance.yahoo.com", "seekingalpha.com", "fool.com", "benzinga.com",
    "businesswire.com", "prnewswire.com", "globenewswire.com",
    "sec.gov",
]


def _now_et() -> datetime:
    return datetime.now(ET_TZ)


def _now_et_str() -> str:
    return _now_et().strftime('%Y-%m-%d %H:%M:%S ET')


# Gemini 重試設定。2026-08-10 唯一一筆缺漏是 503 UNAVAILABLE(模型暫時
# 過載)—— 那是可重試錯誤,卻被當成永久失敗直接放棄該檔。
GEMINI_MAX_ATTEMPTS = 3
GEMINI_RETRY_BACKOFF_SEC = (2, 5)

# 只重試「稍後會好」的錯誤。金鑰錯誤、權限不足、配額用罄、請求格式錯誤
# 重試再多次也一樣,重試只會白白吃掉整段時間預算。
_RETRYABLE_MARKERS = (
    "503", "unavailable", "500", "internal",
    "429", "resource_exhausted", "rate limit", "overloaded",
    "timeout", "timed out", "deadline",
    "connection", "temporarily",
)


def _is_retryable(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    # 配額用罄雖然常伴隨 429,但當日不會恢復,重試無益
    if "quota" in text and "exceeded" in text:
        return False
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _call_with_retry(call, *, label: str = ""):
    """對可重試的 Gemini 錯誤做退避重試;不可重試者立即拋出。"""
    last: Exception | None = None
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — 需依錯誤內容決定是否重試
            last = exc
            if attempt >= GEMINI_MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            delay = GEMINI_RETRY_BACKOFF_SEC[
                min(attempt - 1, len(GEMINI_RETRY_BACKOFF_SEC) - 1)
            ]
            print(
                f"    ⏳ Gemini 暫時性失敗({label},第 {attempt} 次):{exc};"
                f"{delay}s 後重試"
            )
            time.sleep(delay)
    raise last  # pragma: no cover — 迴圈必定 return 或 raise


def _resolve_company_name(ticker: str, override: str = "") -> str:
    """
    解析公司英文名供新聞檢索使用。

    優先序:呼叫端明確傳入 > Config.COMPANY_NAMES > 空字串(退回純 ticker)。
    查不到名稱**不是錯誤** —— 成分股異動而 COMPANY_NAMES 尚未同步時,
    仍應以純 ticker 搜尋,不可讓 P7.5 中斷主流程。
    """
    if override:
        return override.strip()
    names = getattr(Config, "COMPANY_NAMES", {}) or {}
    return names.get(ticker.strip().upper(), "")


def _is_enabled() -> bool:
    """檢查 LLM enrichment 是否啟用(Config + 環境變數雙重控制)"""
    if not Config.LLM_ENRICHMENT_ENABLED:
        return False
    env_override = os.getenv("LLM_ENRICHMENT_ENABLED", "").lower()
    if env_override in ("false", "0", "no"):
        return False
    return True


# ============================================================
# 1. Tavily 新聞抓取
# ============================================================
def get_news_for_stock_tavily(stock_id: str, stock_name: str = "",
                               days: int = 3, max_results: int = 5) -> dict:
    """
    用 Tavily 抓近 N 天的美股英文新聞。

    參數:
      stock_id   : 美股 ticker(如 NVDA)
      stock_name : 公司英文名(可選;留空則自 Config.COMPANY_NAMES 解析)
      days       : 近 N 天(預設 3)
      max_results: 結果上限(預設 5)

    回傳:
      成功 → {"ok": True, "news": [{"title": "...", "content": "...", "url": "...", "published": "..."}, ...]}
      失敗 → {"ok": False, "err": "...", "news": []}

    說明:
      - V1.3 P0:query 與來源全面改美股。原版硬加「台股」並限制在台灣媒體,
        對 NDX 成分股近乎零命中,是本模組被停用的直接原因。
      - 公司名 + ticker 併用:純 ticker 在美股歧義過高(APP/EA/ARM/MU/STX
        同時是常用英文字),加公司名可大幅收斂。
      - search_depth 用 "basic"(快、便宜),"advanced" 留給未來需要時升級
    """
    token = os.getenv("TAVILY_API_KEY", "")
    if not token:
        return {"ok": False, "err": "TAVILY_API_KEY 未設定", "news": []}

    ticker = stock_id.strip().upper()
    company = _resolve_company_name(ticker, stock_name)
    # 「stock news」錨定財經語境,避免公司名撞到消費性產品或體育新聞
    # (如 Apple、Amazon、Arm、Linde 的一般報導)。
    query = f"{company} ({ticker}) stock news" if company else f"{ticker} stock news"

    payload = {
        "api_key":         token,
        "query":           query,
        "search_depth":    "basic",
        "topic":           "news",
        "days":            days,
        "max_results":     max_results,
        "include_answer":  False,
        "include_raw_content": False,
        "include_domains": list(US_NEWS_DOMAINS),
    }

    try:
        r = requests.post("https://api.tavily.com/search",
                          json=payload, timeout=TAVILY_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.Timeout:
        return {"ok": False, "err": f"Tavily timeout({TAVILY_TIMEOUT}s)", "news": []}
    except Exception as e:
        return {"ok": False, "err": f"Tavily 連線失敗:{e}", "news": []}

    raw_results = data.get("results", [])
    if not raw_results:
        return {"ok": True, "news": [], "note": "Tavily 回傳 0 筆新聞(近期無相關報導)"}

    # V13.11.2:程式端 published_date 二次硬過濾(超過 NEWS_MAX_AGE_DAYS 天硬丟)
    # published_date 缺失或解析失敗的保留(信任 Tavily 預設 days 過濾)
    now_et = _now_et()
    cutoff = now_et - timedelta(days=NEWS_MAX_AGE_DAYS)
    kept, dropped = [], 0
    for r in raw_results:
        pub_str = r.get("published_date", "") or ""
        if pub_str:
            try:
                # Tavily 通常回 ISO 8601 格式(如 2026-05-22T08:30:00Z 或 -04:00)
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                # 無時區者視為 ET(美股新聞來源的自然時區)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=ET_TZ)
                if pub_dt < cutoff:
                    dropped += 1
                    continue  # 超過 14 天硬丟
            except (ValueError, TypeError):
                pass  # 解析失敗保留(不擋)
        kept.append(r)

    if not kept:
        msg = f"Tavily 5 筆全為 {NEWS_MAX_AGE_DAYS} 天前舊聞" if dropped else "Tavily 回傳 0 筆新聞(近期無相關報導)"
        return {"ok": True, "news": [], "note": msg}

    raw_results = kept

    news = []
    for r in raw_results:
        news.append({
            "title":     r.get("title", "")[:200],
            "content":   r.get("content", "")[:500],   # 控長度,Gemini 上下文預算用
            "url":       r.get("url", ""),
            "published": r.get("published_date", ""),
        })
    note = f"已過濾 {dropped} 筆 {NEWS_MAX_AGE_DAYS} 天前舊聞" if dropped else ""
    return {"ok": True, "news": news, "note": note}


# ============================================================
# 2. Gemini 摘要生成
# ============================================================
PROMPT_TEMPLATE = """你是美股研究助手。給定一檔美股 + 近期新聞,產出 200 字以內的摘要,嚴格分三段:

🚨 風險警示(0-2 條,每條 ≤ 30 字):財測下修、財報不如預期、SEC/監管調查、內部人賣股、大客戶流失、競品威脅、分析師降評、訴訟或召回。沒有則寫「無顯著風險」。
✨ 利好催化(0-2 條,每條 ≤ 30 字):財報優於預期、財測上修、大單或新合約、新品發表、分析師升評、庫藏股或股利、併購與策略合作。沒有則寫「無顯著催化」。
📢 最新動態(1 條,≤ 40 字):**以新聞日期最新的事件**為主,純客觀描述。

**嚴格規則**:
1. 只能根據下方提供的新聞,**禁止**杜撰或補充新聞外的資訊。
2. **語言規則**:下方新聞為英文,但輸出一律使用**繁體中文**。公司名、產品名、
   財務術語(如 EPS、guidance、buyback)可保留英文原文,不必硬翻。
3. **時效規則**:每條新聞前綴的 [N] YYYY-MM-DD 是發佈日期(美東時間)。
   - **忽略日期早於 {cutoff_date} 的新聞**(視為舊聞,不可用於 risk/catalyst/highlight)。
   - 若所有新聞日期都早於 {cutoff_date},三段全部寫「近期無顯著事件」。
   - highlight 必須引用列表中**日期最新**的有效事件,不可挑舊聞當主摘要。
4. **標的比對規則**:新聞須確實是關於下方【股票】那家公司。美股 ticker 歧義高,
   若某條新聞只是提到同名產品、同名人物或另一家公司,視為無關而略過。
5. **非事件排除規則**:下列**不算**風險或催化,一律不得寫入:
   - **純價格波動**:「股價下跌 10%」「上月跌 29.78%」「創新高」這類只描述
     漲跌幅的敘述。掃描器已知道價格。除非新聞明確指出**造成**該波動的事件,
     才寫那個事件本身,而不是寫漲跌幅。
   - **平台上架與衍生商品**:加密貨幣代幣化股票、券商/交易所新增該標的、
     ETF 成分調整、選擇權上市等,與公司基本面無關。
   - **泛泛評論與預測性標題**:「該買嗎」「值得關注」「五檔必買股」這類
     內容農場式標題,以及沒有具體事件的分析師隨筆。
   - **過期重述**:只是回顧早於時效門檻之事件的整理文。
6. 若新聞為空、全無關股票本身,或全被上條排除,三段全部寫「無相關新聞」。
6. **輸出 minified JSON,絕對禁止換行、縮排、空格美化、markdown 標記、解釋文字**:
   {{"risk":["..."],"catalyst":["..."],"highlight":"..."}}
7. risk/catalyst 為陣列(0~2 個元素);highlight 為單字串。

【股票】{stock_name}({stock_id})
【今日日期】{today_date}(美東)
【新聞時效門檻】不採用早於 {cutoff_date} 的事件

【近期新聞(英文,已過濾 {age_days} 天前舊聞)】
{news_block}

請輸出 minified JSON(單行,無縮排):"""


def _format_news_block(news_list: list) -> str:
    """把 news list 格式化為 prompt 可讀的文字塊。"""
    if not news_list:
        return "(無相關新聞)"
    lines = []
    for i, n in enumerate(news_list, 1):
        pub = n.get("published", "")[:10] if n.get("published") else "?"
        title = n.get("title", "")
        content = n.get("content", "")
        lines.append(f"[{i}] {pub} | {title}\n    {content}")
    return "\n".join(lines)


def _extract_json_from_response(text: str) -> Optional[dict]:
    """
    從 LLM response 中抽 JSON。處理常見變異:
    - 純 JSON
    - ```json ... ``` markdown code block 包裹
    - JSON 前後有解釋文字
    
    回傳 dict 或 None。
    """
    if not text:
        return None

    text = text.strip()

    # 嘗試 1:純 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 嘗試 2:去 markdown code block 包裹
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 嘗試 3:抓最外層 {...}(處理前後有解釋文字的情況)
    m = re.search(r'\{[^{}]*"highlight"[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 嘗試 4:寬鬆抓任何 {...}
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def enrich_pick_with_gemini(stock_id: str, stock_name: str,
                             news_list: list, days: int = 3) -> dict:
    """
    用 Gemini 2.5 Flash 對單檔股票產出風險/利好/動態摘要。
    
    回傳:
      成功 → {"ok": True, "risk": [...], "catalyst": [...], "highlight": "...",
              "summary_text": "拼接好的可讀文字(供 Notion 寫入用)"}
      失敗 → {"ok": False, "err": "...", "summary_text": ""}
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"ok": False, "err": "GEMINI_API_KEY 未設定", "summary_text": ""}

    # Lazy import,避免主程式啟動時 import 失敗影響其他模組
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return {"ok": False,
                "err": "google-genai SDK 未安裝(pip install google-genai)",
                "summary_text": ""}

    news_block = _format_news_block(news_list)
    # V13.11.2:PROMPT 需要 today_date / cutoff_date 用於時效判斷
    # V1.3 P0:改 ET,與新聞來源和 repo 其餘時間欄位同基準
    now_et = _now_et()
    today_date  = now_et.strftime("%Y-%m-%d")
    cutoff_date = (now_et - timedelta(days=NEWS_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    ticker  = stock_id.strip().upper()
    company = _resolve_company_name(ticker, stock_name)
    prompt = PROMPT_TEMPLATE.format(
        stock_name  = company or "未知公司",
        stock_id    = ticker,
        today_date  = today_date,
        cutoff_date = cutoff_date,
        age_days    = NEWS_MAX_AGE_DAYS,
        news_block  = news_block,
    )

    def _call_once():
        client = genai.Client(api_key=api_key)
        return client.models.generate_content(
            model    = Config.LLM_MODEL,
            contents = prompt,
            config   = genai_types.GenerateContentConfig(
                temperature       = 0.3,    # 低溫降低幻覺
                max_output_tokens = 3000,   # V13.11.2: 1500→3000 雙保險
                                            # 5/25 真實跑 3/8 仍因 JSON 截斷失敗。
                                            # 根因:Gemini Flash 啟用 JSON mode 後
                                            # 傾向 pretty-print(縮排換行),token 用量
                                            # 2~3 倍。即使 PROMPT 改強制 minified,
                                            # 仍給 3000 token buffer(Flash 免費)。
                response_mime_type = "application/json",  # 強制 JSON 輸出
            ),
        )

    try:
        response = _call_with_retry(_call_once, label=ticker)
        raw_text = response.text or ""
    except Exception as e:
        return {"ok": False, "err": f"Gemini 呼叫失敗:{e}", "summary_text": ""}

    parsed = _extract_json_from_response(raw_text)
    if not parsed:
        return {"ok": False,
                "err": f"Gemini 回傳無法 parse 為 JSON(前 100 字:{raw_text[:100]})",
                "summary_text": ""}

    risk      = parsed.get("risk", []) or []
    catalyst  = parsed.get("catalyst", []) or []
    highlight = parsed.get("highlight", "") or ""

    # 防呆:強制成 list / str
    if isinstance(risk, str):     risk = [risk]
    if isinstance(catalyst, str): catalyst = [catalyst]

    risk     = [str(x)[:60] for x in risk if x][:2]
    catalyst = [str(x)[:60] for x in catalyst if x][:2]
    highlight = str(highlight)[:80]

    # 拼接成可讀文字供 Notion / TG 顯示
    lines = []
    if risk:
        lines.append("🚨 " + "/".join(risk))
    else:
        lines.append("🚨 無顯著風險")
    if catalyst:
        lines.append("✨ " + "/".join(catalyst))
    else:
        lines.append("✨ 無顯著催化")
    if highlight:
        lines.append("📢 " + highlight)
    summary_text = "\n".join(lines)

    return {
        "ok":           True,
        "risk":         risk,
        "catalyst":     catalyst,
        "highlight":    highlight,
        "summary_text": summary_text,
    }


# ============================================================
# 3. Notion 「LLM 摘要」欄位寫入
# ============================================================
def _notion_headers() -> dict:
    token = os.getenv("NOTION_TOKEN", "")
    return {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def _query_page_id_by_title(db_id: str, title: str) -> Optional[str]:
    """根據 Title(記錄日期)查 Notion page id。沿用 outputs.py 同款邏輯。"""
    url = f"{NOTION_API_BASE}/databases/{db_id}/query"
    payload = {
        "filter": {
            "property": "記錄日期",
            "title":    {"equals": title},
        },
        "page_size": 1,
    }
    try:
        r = requests.post(url, headers=_notion_headers(),
                          json=payload, timeout=NOTION_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    ⚠️  Notion query 失敗({title}):{e}")
        return None

    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")


def update_notion_llm_summary(scan_date: str, stock_id: str,
                                summary_text: str) -> bool:
    """
    更新 Notion daily_picks DB 的「LLM 摘要」欄位。
    
    流程:
      1. 用 Title `YYYY-MM-DD_股票代號` query 取 page_id
      2. PATCH page 寫入 LLM 摘要(Rich Text,最多 2000 字)
    
    回傳:True 成功 / False 失敗
    
    前置:Notion DB 必須先手動加 Rich Text 欄位「LLM 摘要」
    """
    db_id = os.getenv("NOTION_DB_ID", "")
    token = os.getenv("NOTION_TOKEN", "")
    if not db_id or not token:
        print(f"    ⚠️  NOTION_DB_ID 或 NOTION_TOKEN 未設定,無法寫入 {stock_id}")
        return False

    title = f"{scan_date}_{stock_id}"
    page_id = _query_page_id_by_title(db_id, title)
    if not page_id:
        print(f"    ⚠️  Notion 找不到 page({title}),可能護欄擋下未寫入或 schema 異常")
        return False

    properties = {
        Config.LLM_SUMMARY_COLUMN: {
            "rich_text": [{"text": {"content": summary_text[:2000]}}]
        }
    }
    url = f"{NOTION_API_BASE}/pages/{page_id}"

    try:
        r = requests.patch(url, headers=_notion_headers(),
                           json={"properties": properties},
                           timeout=NOTION_TIMEOUT)
        if r.status_code != 200:
            print(f"    ⚠️  Notion PATCH 失敗({title}):HTTP {r.status_code}")
            print(f"        body:{r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"    ⚠️  Notion PATCH 例外({title}):{e}")
        return False


# ============================================================
# 4. 主入口:一站式 enrichment
# ============================================================
def run_llm_enrichment_phase(picks: list, scan_date: str) -> dict:
    """
    對精選清單跑完整 LLM enrichment 流程(主入口)。
    
    參數:
      picks: list of dict, 每個 dict 必須含:
        - "stock_code" 或 "ticker"(優先取 stock_code,fallback ticker)
        - "stock_name"(公司英文名,可選;留空則自 Config.COMPANY_NAMES 解析)
      scan_date: "YYYY-MM-DD"(用於組 Notion Title)
    
    回傳統計 dict:
      {
        "total":      5,
        "success":    4,    # Gemini + Notion 雙重成功
        "news_fail":  1,    # Tavily 失敗(空新聞仍會跑 Gemini)
        "llm_fail":   0,    # Gemini 失敗
        "notion_fail":0,    # Notion 寫入失敗
        "elapsed_sec": 87.3,
        "details": [
          {"stock_id": "NVDA", "ok": True,  "summary_text": "..."},
          {"stock_id": "AMD",  "ok": False, "err": "..."},
          ...
        ]
      }
    
    呼叫端應:
      from llm_enrichment import run_llm_enrichment_phase
      stats = run_llm_enrichment_phase(picks_list, scan_date_str)
      print(f"  LLM enrichment 完成:{stats['success']}/{stats['total']}")
    """
    print("\n" + "=" * 65)
    print(f"🤖 V1.3 P7.5 LLM 精選報告 enrichment(美股新聞源)")
    print(f"   執行時間 :{_now_et_str()}")
    print(f"   掃描日期 :{scan_date}")
    print(f"   標的數量 :{len(picks)} 檔")
    print(f"   LLM 模型 :{Config.LLM_MODEL}")
    print(f"   新聞時效 :≤ {NEWS_MAX_AGE_DAYS} 天(V13.11.2 硬過濾)")
    print("=" * 65)

    if not _is_enabled():
        print("  ⏸  LLM enrichment 已關閉(Config 或環境變數)")
        return {"total": len(picks), "success": 0, "skipped": True,
                "elapsed_sec": 0, "details": []}

    if not picks:
        print("  ℹ️  picks 為空,無需 enrichment")
        return {"total": 0, "success": 0, "elapsed_sec": 0, "details": []}

    stats = {
        "total":       len(picks),
        "success":     0,
        "news_fail":   0,
        "llm_fail":    0,
        "notion_fail": 0,
        "details":     [],
    }

    t_start = time.time()

    for idx, pick in enumerate(picks, 1):
        stock_id   = pick.get("stock_code") or pick.get("ticker", "")
        stock_name = pick.get("stock_name", "")
        if not stock_id:
            print(f"  [{idx}/{len(picks)}] ⚠️  缺少 stock_code,跳過")
            stats["details"].append({"ok": False, "err": "缺少 stock_code"})
            continue

        # 呼叫端(main.py)不帶公司名;此處解析只為 log 可讀,實際檢索與
        # prompt 各自再解析一次,行為一致。
        resolved_name = _resolve_company_name(stock_id, stock_name)
        print(f"\n  [{idx}/{len(picks)}] 🔍 {stock_id} {resolved_name or '(無公司名,以純 ticker 檢索)'}")

        # 整段超時護欄(避免單一階段 hang 住整支主程式)
        if time.time() - t_start > Config.LLM_ENRICHMENT_TOTAL_TIMEOUT:
            print(f"    ⏰ 已超過總時限 {Config.LLM_ENRICHMENT_TOTAL_TIMEOUT}s,後續跳過")
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": "整段超時"})
            continue

        # Step 1: Tavily 抓新聞
        news_result = get_news_for_stock_tavily(
            stock_id, stock_name,
            days        = Config.LLM_NEWS_DAYS,
            max_results = Config.LLM_NEWS_MAX_RESULTS,
        )
        if not news_result["ok"]:
            print(f"    ⚠️  Tavily 失敗:{news_result.get('err', '')},仍嘗試以空新聞跑 Gemini")
            stats["news_fail"] += 1
            news_list = []
        else:
            news_list = news_result.get("news", [])
            note = news_result.get("note", "")
            print(f"    📰 Tavily 取得 {len(news_list)} 筆新聞{('  '+note) if note else ''}")

        # Step 2: Gemini 摘要
        enrich = enrich_pick_with_gemini(stock_id, stock_name, news_list,
                                          days=Config.LLM_NEWS_DAYS)
        if not enrich["ok"]:
            print(f"    ❌ Gemini 失敗:{enrich.get('err', '')}")
            stats["llm_fail"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": enrich.get("err", "")})
            continue

        summary_text = enrich["summary_text"]
        print(f"    🤖 Gemini 摘要:")
        for line in summary_text.split("\n"):
            print(f"        {line}")

        # Step 3: Notion 寫入
        notion_ok = update_notion_llm_summary(scan_date, stock_id, summary_text)
        if notion_ok:
            print(f"    ✅ Notion 寫入成功")
            stats["success"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": True,
                                      "summary_text": summary_text})
        else:
            stats["notion_fail"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": "Notion 寫入失敗",
                                      "summary_text": summary_text})

    stats["elapsed_sec"] = round(time.time() - t_start, 1)

    print("\n" + "-" * 65)
    print(f"  📊 LLM enrichment 完成 / 耗時 {stats['elapsed_sec']}s")
    print(f"     成功     :{stats['success']}/{stats['total']}")
    print(f"     Tavily 失敗:{stats['news_fail']}")
    print(f"     Gemini 失敗:{stats['llm_fail']}")
    print(f"     Notion 失敗:{stats['notion_fail']}")
    print("=" * 65)

    return stats
