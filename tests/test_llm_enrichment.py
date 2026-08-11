"""V1.3 P0:LLM enrichment 美股化回歸測試。

守住三件事:
  1. 新聞檢索確實打美股(無「台股」、無台灣網域、公司名解析與 fallback)。
  2. 摘要 prompt 是美股口徑,時效基準為 ET。
  3. 選股中性 —— 本模組不得影響 strategy_config_hash / universe_version。

全程 mock,不打任何網路。
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_enrichment
from config import Config


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _capture_tavily(payload_results):
    """回傳 (mock_post, captured) — captured 收下實際送出的 Tavily payload。"""
    captured = {}

    def _post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse({"results": payload_results})

    return _post, captured


class TavilyQueryIsUSMarket(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _run(self, ticker, stock_name=""):
        post, captured = _capture_tavily([])
        with mock.patch.object(llm_enrichment.requests, "post", post):
            result = llm_enrichment.get_news_for_stock_tavily(ticker, stock_name)
        return result, captured["payload"]

    def test_query_has_no_taiwan_market_token(self):
        """原版硬加「台股」是本模組被停用的直接原因。"""
        _, payload = self._run("NVDA")
        self.assertNotIn("台股", payload["query"])

    def test_query_uses_company_name_and_ticker(self):
        _, payload = self._run("NVDA")
        self.assertIn("NVIDIA", payload["query"])
        self.assertIn("NVDA", payload["query"])
        self.assertIn("stock news", payload["query"])

    def test_ambiguous_ticker_gets_company_name(self):
        """APP/EA/ARM/MU/STX 純 ticker 搜尋在美股歧義過高。"""
        for ticker, expected in [
            ("APP", "AppLovin"),
            ("EA", "Electronic Arts"),
            ("ARM", "Arm Holdings"),
            ("MU", "Micron Technology"),
            ("STX", "Seagate Technology"),
        ]:
            with self.subTest(ticker=ticker):
                _, payload = self._run(ticker)
                self.assertIn(expected, payload["query"])

    def test_unknown_ticker_falls_back_to_bare_ticker(self):
        """成分股異動而 COMPANY_NAMES 未同步時不得拋錯,只降級為純 ticker。"""
        _, payload = self._run("ZZZZ")
        self.assertEqual(payload["query"], "ZZZZ stock news")

    def test_explicit_stock_name_overrides_config(self):
        _, payload = self._run("NVDA", stock_name="Nvidia Corp")
        self.assertIn("Nvidia Corp", payload["query"])

    def test_lowercase_ticker_still_resolves(self):
        _, payload = self._run("nvda")
        self.assertIn("NVIDIA", payload["query"])
        self.assertIn("(NVDA)", payload["query"])

    def test_include_domains_are_us_only(self):
        _, payload = self._run("NVDA")
        domains = payload["include_domains"]
        self.assertTrue(domains, "include_domains 不可為空,否則會撈到內容農場")
        for domain in domains:
            self.assertFalse(
                domain.endswith(".tw"),
                f"{domain} 是台灣網域,美股掃描不應保留",
            )
        for taiwan_media in ("cnyes.com", "moneydj.com", "udn.com", "ettoday.net"):
            self.assertNotIn(taiwan_media, domains)
        for us_source in ("reuters.com", "cnbc.com", "businesswire.com", "sec.gov"):
            self.assertIn(us_source, domains)

    def test_missing_api_key_degrades_gracefully(self):
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            result = llm_enrichment.get_news_for_stock_tavily("NVDA")
        self.assertFalse(result["ok"])
        self.assertEqual(result["news"], [])


class NewsFreshnessUsesEasternTime(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_old_news_is_hard_filtered(self):
        now_et = datetime.now(llm_enrichment.ET_TZ)
        fresh = (now_et - timedelta(days=1)).isoformat()
        stale = (now_et - timedelta(days=llm_enrichment.NEWS_MAX_AGE_DAYS + 5)).isoformat()
        post, _ = _capture_tavily([
            {"title": "fresh", "content": "c", "url": "u", "published_date": fresh},
            {"title": "stale", "content": "c", "url": "u", "published_date": stale},
        ])
        with mock.patch.object(llm_enrichment.requests, "post", post):
            result = llm_enrichment.get_news_for_stock_tavily("NVDA")

        self.assertTrue(result["ok"])
        self.assertEqual([n["title"] for n in result["news"]], ["fresh"])

    def test_naive_published_date_is_treated_as_eastern(self):
        """無時區的 published_date 應視為 ET,而非 UTC+8。"""
        naive = (datetime.now(llm_enrichment.ET_TZ) - timedelta(days=1)).replace(
            tzinfo=None
        ).isoformat()
        post, _ = _capture_tavily(
            [{"title": "naive", "content": "c", "url": "u", "published_date": naive}]
        )
        with mock.patch.object(llm_enrichment.requests, "post", post):
            result = llm_enrichment.get_news_for_stock_tavily("NVDA")

        self.assertEqual([n["title"] for n in result["news"]], ["naive"])

    def test_missing_published_date_is_kept(self):
        post, _ = _capture_tavily(
            [{"title": "no-date", "content": "c", "url": "u"}]
        )
        with mock.patch.object(llm_enrichment.requests, "post", post):
            result = llm_enrichment.get_news_for_stock_tavily("NVDA")

        self.assertEqual([n["title"] for n in result["news"]], ["no-date"])

    def test_utc_published_date_is_compared_correctly(self):
        stale_utc = (
            datetime.now(timezone.utc)
            - timedelta(days=llm_enrichment.NEWS_MAX_AGE_DAYS + 2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        post, _ = _capture_tavily(
            [{"title": "stale", "content": "c", "url": "u", "published_date": stale_utc}]
        )
        with mock.patch.object(llm_enrichment.requests, "post", post):
            result = llm_enrichment.get_news_for_stock_tavily("NVDA")

        self.assertTrue(result["ok"])
        self.assertEqual(result["news"], [])


class PromptIsUSMarket(unittest.TestCase):
    def _build_prompt(self, ticker="NVDA", stock_name="", news=None):
        captured = {}

        class _FakeModels:
            def generate_content(self, model=None, contents=None, config=None):
                captured["prompt"] = contents
                return mock.Mock(
                    text='{"risk":[],"catalyst":[],"highlight":"test"}'
                )

        class _FakeClient:
            def __init__(self, api_key=None):
                self.models = _FakeModels()

        fake_genai = mock.Mock(Client=_FakeClient)
        fake_types = mock.Mock()
        modules = {
            "google": mock.Mock(genai=fake_genai),
            "google.genai": fake_genai,
            "google.genai.types": fake_types,
        }
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                mock.patch.dict(sys.modules, modules):
            result = llm_enrichment.enrich_pick_with_gemini(
                ticker, stock_name, news or []
            )
        return result, captured.get("prompt", "")

    def test_persona_is_us_market(self):
        _, prompt = self._build_prompt()
        self.assertIn("美股研究助手", prompt)
        self.assertNotIn("台股研究助手", prompt)

    def test_prompt_states_english_input_chinese_output(self):
        _, prompt = self._build_prompt()
        self.assertIn("繁體中文", prompt)

    def test_prompt_carries_company_name(self):
        _, prompt = self._build_prompt("NVDA")
        self.assertIn("NVIDIA", prompt)

    def test_prompt_dates_use_eastern_time(self):
        _, prompt = self._build_prompt()
        now_et = datetime.now(llm_enrichment.ET_TZ)
        cutoff = now_et - timedelta(days=llm_enrichment.NEWS_MAX_AGE_DAYS)
        self.assertIn(now_et.strftime("%Y-%m-%d"), prompt)
        self.assertIn(cutoff.strftime("%Y-%m-%d"), prompt)

    def test_summary_text_shape_is_unchanged(self):
        """Notion 欄位格式不得因本次改動而變 —— 三段式 emoji 前綴。"""
        result, _ = self._build_prompt()
        self.assertTrue(result["ok"])
        lines = result["summary_text"].split("\n")
        self.assertTrue(lines[0].startswith("🚨"))
        self.assertTrue(lines[1].startswith("✨"))
        self.assertTrue(lines[2].startswith("📢"))

    def test_missing_api_key_degrades_gracefully(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            result = llm_enrichment.enrich_pick_with_gemini("NVDA", "", [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["summary_text"], "")


class CompanyNameCoverage(unittest.TestCase):
    def test_every_scan_pool_ticker_has_a_company_name(self):
        missing = [t for t in Config.SCAN_POOL if t not in Config.COMPANY_NAMES]
        self.assertEqual(missing, [], f"SCAN_POOL 缺公司名:{missing}")

    def test_no_orphan_company_names(self):
        """反向:COMPANY_NAMES 不該留下已被剔除的成分股。"""
        orphans = [t for t in Config.COMPANY_NAMES if t not in Config.SCAN_POOL]
        self.assertEqual(orphans, [], f"COMPANY_NAMES 有多餘代號:{orphans}")

    def test_names_are_non_empty(self):
        blank = [t for t, name in Config.COMPANY_NAMES.items() if not name.strip()]
        self.assertEqual(blank, [])


class EnrichmentIsSelectionNeutral(unittest.TestCase):
    def test_llm_settings_do_not_enter_cohort_identity(self):
        """LLM 設定若進了 ConfigHash,會把 v1.2.1 cohort 一刀切斷。"""
        from trade_plan import strategy_config_hash, universe_version

        baseline_hash = strategy_config_hash()
        baseline_universe = universe_version()

        with mock.patch.object(Config, "LLM_ENRICHMENT_ENABLED", False), \
                mock.patch.object(Config, "LLM_MODEL", "some-other-model"), \
                mock.patch.object(Config, "LLM_NEWS_DAYS", 99), \
                mock.patch.object(Config, "LLM_ENRICHMENT_TOTAL_TIMEOUT", 1), \
                mock.patch.object(Config, "COMPANY_NAMES", {}):
            self.assertEqual(strategy_config_hash(), baseline_hash)
            self.assertEqual(universe_version(), baseline_universe)


class EnablementSwitch(unittest.TestCase):
    def test_enabled_by_default_after_us_migration(self):
        self.assertTrue(Config.LLM_ENRICHMENT_ENABLED)

    def test_env_can_kill_switch_without_code_change(self):
        for value in ("false", "0", "no", "FALSE"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ,
                                     {"LLM_ENRICHMENT_ENABLED": value}):
                    self.assertFalse(llm_enrichment._is_enabled())

    def test_config_off_beats_env_on(self):
        with mock.patch.object(Config, "LLM_ENRICHMENT_ENABLED", False), \
                mock.patch.dict(os.environ, {"LLM_ENRICHMENT_ENABLED": "true"}):
            self.assertFalse(llm_enrichment._is_enabled())

    def test_disabled_phase_returns_skipped_without_network(self):
        with mock.patch.object(Config, "LLM_ENRICHMENT_ENABLED", False):
            stats = llm_enrichment.run_llm_enrichment_phase(
                [{"stock_code": "NVDA"}], "2026-08-06"
            )
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["success"], 0)


class JsonExtraction(unittest.TestCase):
    def test_plain_json(self):
        parsed = llm_enrichment._extract_json_from_response(
            '{"risk":[],"catalyst":[],"highlight":"a"}'
        )
        self.assertEqual(parsed["highlight"], "a")

    def test_markdown_fenced_json(self):
        parsed = llm_enrichment._extract_json_from_response(
            '```json\n{"risk":[],"catalyst":[],"highlight":"a"}\n```'
        )
        self.assertEqual(parsed["highlight"], "a")

    def test_json_with_surrounding_prose(self):
        parsed = llm_enrichment._extract_json_from_response(
            'Here you go: {"risk":[],"catalyst":[],"highlight":"a"} done.'
        )
        self.assertEqual(parsed["highlight"], "a")

    def test_unparseable_returns_none(self):
        self.assertIsNone(llm_enrichment._extract_json_from_response("not json"))
        self.assertIsNone(llm_enrichment._extract_json_from_response(""))


class GeminiRetriesTransientFailures(unittest.TestCase):
    """2026-08-10 唯一一筆缺漏是 503 UNAVAILABLE —— 可重試錯誤被當成永久失敗。"""

    def test_transient_errors_are_retryable(self):
        for message in (
            "503 UNAVAILABLE. This model is currently experiencing high demand.",
            "429 RESOURCE_EXHAUSTED",
            "500 Internal error",
            "Read timed out",
            "Connection aborted",
            "The service is temporarily overloaded",
        ):
            with self.subTest(message=message):
                self.assertTrue(llm_enrichment._is_retryable(Exception(message)))

    def test_permanent_errors_are_not_retryable(self):
        """重試金鑰或格式錯誤只會白吃掉整段時間預算。"""
        for message in (
            "401 API key not valid",
            "400 INVALID_ARGUMENT",
            "403 PERMISSION_DENIED",
            "429 Quota exceeded for this project",
        ):
            with self.subTest(message=message):
                self.assertFalse(llm_enrichment._is_retryable(Exception(message)))

    def test_retry_eventually_succeeds(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return "ok"

        with mock.patch.object(llm_enrichment.time, "sleep"):
            result = llm_enrichment._call_with_retry(flaky, label="TEST")

        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 3)

    def test_permanent_error_is_not_retried(self):
        attempts = []

        def broken():
            attempts.append(1)
            raise RuntimeError("401 API key not valid")

        with mock.patch.object(llm_enrichment.time, "sleep"), \
                self.assertRaises(RuntimeError):
            llm_enrichment._call_with_retry(broken, label="TEST")

        self.assertEqual(len(attempts), 1)

    def test_retry_gives_up_after_max_attempts(self):
        attempts = []

        def always_down():
            attempts.append(1)
            raise RuntimeError("503 UNAVAILABLE")

        with mock.patch.object(llm_enrichment.time, "sleep"), \
                self.assertRaises(RuntimeError):
            llm_enrichment._call_with_retry(always_down, label="TEST")

        self.assertEqual(len(attempts), llm_enrichment.GEMINI_MAX_ATTEMPTS)


class PromptExcludesNonEvents(unittest.TestCase):
    """8/7~8/10 抽查發現摘要把純價格波動與平台上架當成風險/催化。"""

    def test_price_action_is_excluded(self):
        self.assertIn("純價格波動", llm_enrichment.PROMPT_TEMPLATE)

    def test_tokenised_stock_listings_are_excluded(self):
        prompt = llm_enrichment.PROMPT_TEMPLATE
        self.assertIn("代幣化", prompt)
        self.assertIn("ETF 成分調整", prompt)

    def test_content_farm_headlines_are_excluded(self):
        self.assertIn("內容農場", llm_enrichment.PROMPT_TEMPLATE)

    def test_all_excluded_still_yields_a_valid_answer(self):
        """全被排除時要說「無相關新聞」,不是硬湊。"""
        self.assertIn("全被上條排除", llm_enrichment.PROMPT_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
