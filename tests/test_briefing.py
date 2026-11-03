import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from briefing.generator import (
    _split_sections,
    _news_url_allowed,
    format_briefing_html,
    format_briefing_telegram,
)
from briefing.mailer import send_briefing_email


class BriefingFormattingTests(unittest.TestCase):
    def test_news_article_stays_together_across_chunk_boundary(self):
        headline = "• Important market headline"
        published = "  Published: 2026-11-02 20:00 UTC | Article: live | Feed: live"
        url = "  https://example.com/article"

        chunks = _split_sections(
            ["x" * 2500, f"{headline}\n{published}\n{url}"],
            max_len=2600,
        )

        self.assertEqual(len(chunks), 2)
        article_chunk = next(chunk for chunk in chunks if headline in chunk)
        self.assertIn(published, article_chunk)
        self.assertIn(url, article_chunk)
        self.assertLessEqual(max(map(len, chunks)), 2600)

    def test_telegram_chunks_respect_limit(self):
        data = {
            "generated_at": datetime.now(timezone.utc),
            "sections": {
                "news": {
                    "Global": [
                        SimpleNamespace(
                            title=f"Headline {i} " + ("x" * 300),
                            link="https://example.com/article",
                            source="Example",
                            published=None,
                            freshness=SimpleNamespace(value="live"),
                            fetch_freshness=SimpleNamespace(value="live"),
                        )
                        for i in range(30)
                    ]
                }
            },
        }
        chunks = format_briefing_telegram(data)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 4096 for chunk in chunks))

    def test_html_escapes_untrusted_headline(self):
        item = SimpleNamespace(
            title='<script>alert("x")</script>',
            link="https://example.com/article",
            source="Example",
            published=None,
            freshness=SimpleNamespace(value="live"),
            fetch_freshness=SimpleNamespace(value="live"),
        )
        data = {
            "generated_at": datetime.now(timezone.utc),
            "sections": {"news": {"Global": [item]}},
        }
        result = format_briefing_html(data)
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_telegram_escapes_dynamic_news_text_and_preserves_url(self):
        item = SimpleNamespace(
            title="Markets rise [test] *today* _again_",
            link="https://example.com/story?id=1&ref=test",
            source="Example_Source",
            published=None,
            freshness=SimpleNamespace(value="live"),
            fetch_freshness=SimpleNamespace(value="cached"),
        )
        data = {
            "generated_at": datetime.now(timezone.utc),
            "sections": {"news": {"Global_Markets": [item]}},
        }

        result = "\\n".join(format_briefing_telegram(data))

        self.assertIn("*Business, Economics, Markets & Geopolitics*", result)
        self.assertIn("Global Markets", result)
        self.assertNotIn("_Global Markets", result)
        self.assertNotIn("Global_Markets", result)
        self.assertIn(r"\[test]", result)
        self.assertIn(r"\*today\*", result)
        self.assertIn(r"\_again\_", result)
        self.assertIn(r"Example\_Source", result)
        self.assertIn("https://example.com/story?id=1&ref=test", result)

    def test_only_absolute_https_links_are_allowed(self):
        self.assertTrue(_news_url_allowed("https://example.com/story"))
        self.assertFalse(_news_url_allowed("http://example.com/story"))
        self.assertFalse(_news_url_allowed("javascript:alert(1)"))
        self.assertFalse(_news_url_allowed("https://user:pass@example.com/story"))
        self.assertFalse(_news_url_allowed("not-a-url"))


class BriefingMailerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_email_does_not_make_request(self):
        settings = SimpleNamespace(brevo_enabled=False)
        with patch("briefing.mailer.httpx.AsyncClient") as client:
            result = await send_briefing_email(
                settings, "Test", "<p>Test</p>", "Test"
            )
        self.assertFalse(result)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class BriefingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_source_is_fetched_once(self):
        from briefing.generator import build_briefing_data
        from data_sources.base import DataUnavailable

        settings = SimpleNamespace(
            enable_oil=True,
            enable_crypto=False,
            enable_stocks=False,
            enable_bonds=False,
            enable_forex=False,
            enable_economic=False,
            enable_weather=False,
            enable_news=False,
            zone=timezone.utc,
        )

        with patch(
            "briefing.generator.oil.get_oil",
            new_callable=__import__("unittest").mock.AsyncMock,
        ) as get_oil:
            get_oil.return_value = {"brent": "mock quote"}
            result = await build_briefing_data(settings)

        self.assertEqual(get_oil.await_count, 1)
        self.assertEqual(result["sections"]["oil"]["brent"], "mock quote")

    async def test_scheduler_sends_email_only_and_builds_once(self):
        from scheduler.daily import _deliver_briefing

        generated = datetime.now(timezone.utc)
        data = {"generated_at": generated, "sections": {}}
        settings = SimpleNamespace(brevo_enabled=True)
        app = SimpleNamespace()

        with (
            patch("scheduler.daily.build_briefing_data", new_callable=__import__("unittest").mock.AsyncMock, return_value=data) as build,
            patch("scheduler.daily.format_briefing_html", return_value="<p>test</p>"),
            patch("scheduler.daily.format_briefing_text", return_value="test"),
            patch("scheduler.daily.send_briefing_email", new_callable=__import__("unittest").mock.AsyncMock, return_value=True) as send_email,
        ):
            await _deliver_briefing(app, settings)

        build.assert_awaited_once_with(settings)
        send_email.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()


class EmailPresentationTests(unittest.TestCase):
    def test_email_uses_full_crypto_etf_bond_and_forex_labels(self):
        from data_sources.base import Freshness
        from data_sources.bonds import CurveSpread, YieldPoint
        from data_sources.crypto import CryptoQuote
        from data_sources.forex import FxRate

        now = datetime.now(timezone.utc)
        live = Freshness.LIVE
        curve = {
            tenor: YieldPoint(
                tenor=tenor,
                country="US",
                rate=4.0,
                change_bp=2.5,
                as_of=now,
                freshness=live,
            )
            for tenor in ("2Y", "3Y", "5Y", "7Y", "10Y")
        }
        canada_curve = {
            tenor: YieldPoint(
                tenor=tenor,
                country="CA",
                rate=3.0,
                change_bp=-1.5,
                as_of=now,
                freshness=live,
            )
            for tenor in ("2Y", "3Y", "5Y", "7Y", "10Y")
        }
        data = {
            "generated_at": now,
            "sections": {
                "crypto": {
                    "BTC": CryptoQuote("BTC", "Bitcoin", 90000, 1.2),
                },
                "etfs": {},
                "bonds": {
                    "us_curve": curve,
                    "canada_curve": canada_curve,
                    "spread": CurveSpread("US 2s10s", "2Y", "10Y", -15.0, True),
                    "canada_spread": CurveSpread(
                        "Canada 2s10s", "2Y", "10Y", 25.0, False
                    ),
                },
                "forex": {
                    "rates": {
                        "CAD/CHF": FxRate(
                            "CAD/CHF", "CAD", "CHF", 0.62, 0.3
                        )
                    }
                },
            },
        }

        result = format_briefing_html(data)

        self.assertIn("<h2", result)
        self.assertIn("Cryptocurrencies", result)
        self.assertIn("BTC – Bitcoin", result)
        self.assertNotIn("BTC – BTC", result)
        self.assertIn("<i>iShares Core Equity ETF Portfolio</i>", result)
        self.assertIn("2-Year Treasury Yield", result)
        self.assertIn("10-Year Treasury Yield", result)
        self.assertIn("2-Year Government of Canada Yield", result)
        self.assertIn("+2.5 bps", result)
        self.assertIn("-1.5 bps", result)
        self.assertIn("Inverted", result)
        self.assertIn("Normal", result)
        self.assertIn("#43252d", result)
        self.assertIn("CAD/CHF <i>(Canadian dollar / Swiss franc)</i>", result)

    def test_telegram_keeps_existing_digital_assets_and_short_bond_labels(self):
        from data_sources.bonds import CurveSpread, YieldPoint
        from data_sources.crypto import CryptoQuote

        now = datetime.now(timezone.utc)
        curve = {
            tenor: YieldPoint(tenor, "US", 4.0, 1.0, now)
            for tenor in ("2Y", "3Y", "5Y", "7Y", "10Y")
        }
        data = {
            "generated_at": now,
            "sections": {
                "crypto": {"BTC": CryptoQuote("BTC", "Bitcoin", 90000, 1.2)},
                "bonds": {
                    "us_curve": curve,
                    "canada_curve": {},
                    "spread": CurveSpread("US 2s10s", "2Y", "10Y", 20, False),
                },
            },
        }

        result = "\n".join(format_briefing_telegram(data))

        self.assertIn("Digital Assets", result)
        self.assertIn("2Y:", result)
        self.assertIn("10Y:", result)


class EconomicCalculationTests(unittest.IsolatedAsyncioTestCase):
    async def test_payroll_change_compares_monthly_gains(self):
        from data_sources import economic
        from data_sources.base import Freshness

        observations = {
            "observations": [
                {"date": "2026-09-01", "value": "200000"},
                {"date": "2026-08-01", "value": "199800"},
                {"date": "2026-07-01", "value": "199500"},
            ]
        }

        with patch(
            "data_sources.economic.fetch_json",
            new_callable=__import__("unittest").mock.AsyncMock,
            return_value=(observations, Freshness.LIVE),
        ):
            result = await economic._fetch_us_payrolls("test-key")

        self.assertEqual(result.value, 200.0)
        self.assertEqual(result.change, -100.0)
        self.assertEqual(
            result.change_label, "thousand jobs vs previous month"
        )

    async def test_eurozone_gdp_compares_with_previous_quarter(self):
        from data_sources import economic
        from data_sources.base import Freshness

        rows = [
            ("2026-Q1", 1.8),
            ("2026-Q2", 2.0),
        ]

        with patch(
            "data_sources.economic._fetch_eurostat_rows",
            new_callable=__import__("unittest").mock.AsyncMock,
            return_value=(rows, Freshness.LIVE),
        ):
            result = await economic._fetch_europe_gdp()

        self.assertEqual(result.label, "GDP growth (YoY)")
        self.assertEqual(result.value, 2.0)
        self.assertEqual(result.change, 0.2)
        self.assertEqual(result.change_label, "pp vs previous quarter")

    async def test_fred_yoy_change_compares_with_previous_period(self):
        from data_sources import economic
        from data_sources.base import Freshness

        observations = {
            "observations": [
                {"date": "2026-09-01", "value": "110"},
                {"date": "2026-08-01", "value": "108"},
                {"date": "2025-09-01", "value": "100"},
                {"date": "2025-08-01", "value": "99"},
            ]
        }

        with patch(
            "data_sources.economic.fetch_json",
            new_callable=__import__("unittest").mock.AsyncMock,
            return_value=(observations, Freshness.LIVE),
        ):
            result = await economic._fetch_fred_yoy(
                "TEST_SERIES", "test-key", "Test CPI"
            )

        self.assertEqual(result.value, 10.0)
        self.assertAlmostEqual(result.change, 0.909, places=3)
        self.assertEqual(result.change_label, "pp vs previous month")

    async def test_fred_yoy_does_not_invent_change_without_comparison_data(self):
        from data_sources import economic
        from data_sources.base import Freshness

        observations = {
            "observations": [
                {"date": "2026-09-01", "value": "110"},
                {"date": "2026-08-01", "value": "108"},
                {"date": "2025-09-01", "value": "100"},
            ]
        }

        with patch(
            "data_sources.economic.fetch_json",
            new_callable=__import__("unittest").mock.AsyncMock,
            return_value=(observations, Freshness.LIVE),
        ):
            result = await economic._fetch_fred_yoy(
                "TEST_SERIES", "test-key", "Test CPI"
            )

        self.assertEqual(result.value, 10.0)
        self.assertIsNone(result.change)


class ManualBriefingDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def _run_manual_briefing(self, *, brevo_enabled=True, email_side_effect=None):
        from unittest.mock import AsyncMock

        from telegram_bot.commands import cmd_briefing

        data = {"generated_at": datetime.now(timezone.utc), "sections": {}}
        settings = SimpleNamespace(brevo_enabled=brevo_enabled)
        reply_text = AsyncMock()
        update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
        context = SimpleNamespace(bot_data={"settings": settings})

        with (
            patch("telegram_bot.commands.is_authorized", return_value=True),
            patch("briefing.generator.build_briefing_data", new_callable=AsyncMock, return_value=data) as build,
            patch("briefing.generator.format_briefing_html", return_value="<p>email report</p>"),
            patch("briefing.generator.format_briefing_text", return_value="email report"),
            patch("briefing.mailer.send_briefing_email", new_callable=AsyncMock, side_effect=email_side_effect, return_value=True) as send_email,
        ):
            await cmd_briefing(update, context)

        return reply_text, build, send_email

    async def test_manual_briefing_builds_once_sends_email_and_confirms_on_telegram(self):
        reply, build, send_email = await self._run_manual_briefing()
        build.assert_awaited_once()
        send_email.assert_awaited_once()
        reply.assert_awaited_once_with(
            "📧 Your full daily financial briefing has been sent to your email."
        )

    async def test_email_failure_reports_error_on_telegram(self):
        reply, build, send_email = await self._run_manual_briefing(
            email_side_effect=RuntimeError("Email unavailable")
        )
        build.assert_awaited_once()
        send_email.assert_awaited_once()
        reply.assert_awaited_once_with(
            "⚠️ An error occurred while sending the briefing email."
        )

    async def test_email_returning_false_reports_failure_on_telegram(self):
        from unittest.mock import AsyncMock

        from telegram_bot.commands import cmd_briefing

        data = {"generated_at": datetime.now(timezone.utc), "sections": {}}
        settings = SimpleNamespace(brevo_enabled=True)
        reply = AsyncMock()
        update = SimpleNamespace(message=SimpleNamespace(reply_text=reply))
        context = SimpleNamespace(bot_data={"settings": settings})

        with (
            patch("telegram_bot.commands.is_authorized", return_value=True),
            patch("briefing.generator.build_briefing_data", new_callable=AsyncMock, return_value=data),
            patch("briefing.generator.format_briefing_html", return_value="<p>email report</p>"),
            patch("briefing.generator.format_briefing_text", return_value="email report"),
            patch("briefing.mailer.send_briefing_email", new_callable=AsyncMock, return_value=False) as send_email,
        ):
            await cmd_briefing(update, context)

        send_email.assert_awaited_once()
        reply.assert_awaited_once_with(
            "⚠️ The briefing email could not be sent."
        )

    async def test_disabled_brevo_reports_email_disabled_without_sending(self):
        reply, build, send_email = await self._run_manual_briefing(brevo_enabled=False)
        build.assert_awaited_once()
        send_email.assert_not_awaited()
        reply.assert_awaited_once_with(
            "Email delivery is disabled. The briefing was not sent."
        )
