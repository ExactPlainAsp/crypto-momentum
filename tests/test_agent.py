"""Regression tests for financial filters, cost guards and notification limits."""
import copy
import json
import math
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from agent import (ActorFinishedError, Apify, Stop, alert_slots, ingest, row_price,
                   pairing_candidates, scan, validate_free_plan)
from signals import (addresses, confirmed, normalise_tweet, number,
                     security_checks, select_market, social_metrics, trade_sample)

C = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())
NOW = datetime.fromisoformat("2026-10-30T12:00:00+00:00").timestamp()
CA = "0x1234567890123456789012345678901234567890"
SOL_CA = "So11111111111111111111111111111111111111112"


def pair():
    return {"chainId": "base", "pairAddress": "pool", "baseToken": {"address": CA, "symbol": "TEST"},
            "marketCap": 400000, "fdv": 400000, "priceUsd": "0.01", "pairCreatedAt": (NOW - 7200) * 1000,
            "liquidity": {"usd": 75000}, "volume": {"m5": 3000, "h1": 12000},
            "priceChange": {"h1": 15, "m5": 3}, "txns": {"h1": {"buys": 100, "sells": 50}}}


def security():
    d = {k: "0" for k in ("is_proxy", "is_honeypot", "cannot_sell_all", "cannot_buy", "transfer_pausable", "is_blacklisted", "slippage_modifiable", "personal_slippage_modifiable", "is_mintable", "buy_tax", "sell_tax")}
    d.update({"is_open_source": "1", "creator_percent": "0.01", "holder_count": "500",
              "holders": [{"address": f"holder-{i}", "percent": "0.02"} for i in range(10)]})
    return d


def tweets():
    d = []
    texts = ["A research note describes the launch and contract ",
             "Liquidity has expanded while traders discuss this address ",
             "My observations on the new protocol and its trading activity "]
    for i in range(3):
        d.append({"id": str(100 + i), "text": texts[i] + CA, "created": NOW - 300 - i,
                  "author": f"writer{i}", "author_created": NOW - 400 * 86400,
                  "followers": 2000, "following": 100, "lang": "en", "is_retweet": False,
                  "addresses": [CA], "url": f"https://x.com/writer{i}/status/{100+i}",
                  "reply_count": 20, "retweet_count": 20,
                  "previous": {"observed": NOW - 300, "reply_count": 1, "retweet_count": 1}})
    return d


def trades():
    return [{"id": str(i), "attributes": {"tx_hash": f"tx{i}", "block_timestamp": NOW - 60,
             "volume_in_usd": "100", "tx_from_address": f"wallet{i}", "kind": "buy" if i < 9 else "sell"}}
            for i in range(12)]


class Filters(unittest.TestCase):
    def test_rich_tweet_native_dates_and_expanded_contract_link(self):
        row = {"id": "123", "text": "Research link", "createdAt": "Fri Oct 30 11:00:00 +0000 2026",
               "author": {"username": "researcher", "createdAt": "Tue Jun 02 20:12:29 +0000 2009",
                          "followers": 2000, "following": 50},
               "replyCount": 0, "retweetCount": 3,
               "entities": {"urls": [{"expanded_url": "https://example.com/token/" + CA}]}}
        t = normalise_tweet(row, NOW)
        self.assertIn(CA, t["addresses"])
        self.assertIsNotNone(t["author_created"])
        self.assertEqual(t["reply_count"], 0)
        self.assertEqual(t["retweet_count"], 3)

    def test_provider_unknown_counts_never_become_real_zero(self):
        row = {"id": "123", "text": "Research", "createdAt": NOW-60,
               "author": {"username": "researcher"}, "replyCount": 0, "retweetCount": 0,
               "unknownCounts": ["replyCount", "retweetCount"]}
        t = normalise_tweet(row, NOW)
        self.assertIsNone(t["reply_count"])
        self.assertIsNone(t["retweet_count"])

    def test_market_cap_missing_never_uses_fdv(self):
        p = pair(); p.pop("marketCap")
        m, f = select_market([p], CA, C, NOW)
        self.assertIsNone(m["market_cap"])
        self.assertTrue(f)

    def test_one_million_is_excluded(self):
        p = pair(); p["marketCap"] = 1000000
        self.assertTrue(select_market([p], CA, C, NOW)[1])

    def test_young_or_unknown_pool_cannot_prove_age(self):
        for created in [None, (NOW - 3599) * 1000, (NOW + 1) * 1000]:
            p = pair(); p["pairCreatedAt"] = created
            self.assertTrue(select_market([p], CA, C, NOW)[1])

    def test_older_pool_proves_lower_bound_for_same_token(self):
        old = pair(); young = pair(); young["pairCreatedAt"] = (NOW - 600) * 1000
        young["liquidity"]["usd"] = 100000
        m, f = select_market([young, old], CA, C, NOW)
        self.assertFalse(f); self.assertGreaterEqual(m["age_seconds_lower_bound"], 7200)

    def test_ambiguous_chain_excluded(self):
        p = pair(); other = pair(); other["chainId"] = "bsc"
        self.assertIsNone(select_market([p, other], CA, C, NOW)[0])

    def test_diverging_liquid_pool_prices_excluded(self):
        p = pair(); q = pair(); q["marketCap"] = 600000
        self.assertTrue(select_market([p, q], CA, C, NOW)[1])

    def test_non_finite_or_boolean_values_are_unknown(self):
        for v in [None, "", "nan", "inf", True, math.inf]:
            self.assertIsNone(number(v))

    def test_solana_case_preserved(self):
        self.assertIn(SOL_CA, addresses("CA: " + SOL_CA))
        self.assertNotIn(SOL_CA.lower(), addresses("CA: " + SOL_CA))

    def test_only_exact_base_token_matches(self):
        p = pair(); p["baseToken"]["address"] = "0x9999999999999999999999999999999999999999"
        self.assertIsNone(select_market([p], CA, C, NOW)[0])

    def test_known_security_passes(self):
        self.assertEqual(security_checks(security(), "base", C), [])

    def test_unknown_safety_does_not_become_zero(self):
        for value in [None, "", "1"]:
            d = security(); d["is_honeypot"] = value
            self.assertTrue(security_checks(d, "base", C))

    def test_missing_holders_or_creator_excluded(self):
        for key in ["holders", "creator_percent"]:
            d = security(); d.pop(key)
            self.assertTrue(security_checks(d, "base", C))

    def test_concentrated_ownership_excluded(self):
        d = security(); d["holders"][0]["percent"] = "0.50"
        self.assertTrue(security_checks(d, "base", C))

    def test_holder_accounts_are_aggregated(self):
        d = security()
        for h in d["holders"][:6]: h["address"] = "same-owner"
        self.assertTrue(security_checks(d, "base", C))

    def test_duplicate_trades_do_not_increase_wallet_count(self):
        rows = trades()
        r, f = trade_sample(rows + rows, NOW, C)
        self.assertFalse(f); self.assertEqual(r["wallets"], 12); self.assertEqual(r["volume"], 1200)

    def test_one_wallet_cannot_manufacture_confirmation(self):
        rows = trades()
        for row in rows: row["attributes"]["tx_from_address"] = "same"
        self.assertTrue(trade_sample(rows, NOW, C)[1])

    def test_old_or_unverifiable_trades_are_rejected(self):
        for mutate in [lambda a: a.pop("tx_hash"), lambda a: a.update(block_timestamp=NOW-3600)]:
            rows = trades()
            for row in rows: mutate(row["attributes"])
            self.assertTrue(trade_sample(rows, NOW, C)[1])

    def test_social_growth_measured_with_real_previous_snapshot(self):
        m, failures = social_metrics(tweets(), CA, NOW, C)
        self.assertFalse(failures); self.assertEqual(m["authors"], 3)
        self.assertEqual(m["engagement_delta"], 114)

    def test_no_previous_metrics_means_no_confirmed_velocity(self):
        rows = tweets()
        for t in rows: t.pop("previous")
        self.assertTrue(social_metrics(rows, CA, NOW, C)[1])

    def test_impossible_snapshot_before_post_creation_is_not_growth(self):
        rows = tweets()
        for t in rows: t["previous"]["observed"] = NOW - 1800
        self.assertEqual(social_metrics(rows, CA, NOW, C)[0]["engagement_delta"], 0)

    def test_copied_ads_and_warnings_are_excluded(self):
        for mutate in [lambda t: t.update(text="This is a repeated promotional script with contract " + CA),
                       lambda t: t.update(text=t["text"] + " #sponsored"),
                       lambda t: t.update(text="Warning: honeypot " + CA)]:
            rows = tweets()
            for t in rows: mutate(t)
            self.assertTrue(social_metrics(rows, CA, NOW, C)[1])

    def test_low_quality_or_unknown_profiles_do_not_count(self):
        for key, value in [("author_created", None), ("followers", 50), ("following", None)]:
            rows = tweets()
            for t in rows: t[key] = value
            m, f = social_metrics(rows, CA, NOW, C)
            self.assertEqual(m["authors"], 0); self.assertTrue(f)

    def test_manual_repeat_cannot_create_two_confirmations(self):
        entry = {}
        self.assertFalse(confirmed(entry, True, NOW, C))
        self.assertFalse(confirmed(entry, True, NOW+1, C))
        self.assertTrue(confirmed(entry, True, NOW+1800, C))
        self.assertFalse(confirmed(entry, False, NOW+3600, C))

    def test_gap_resets_confirmation(self):
        entry = {}; confirmed(entry, True, NOW, C)
        self.assertFalse(confirmed(entry, True, NOW+3600, C))

    def test_daily_alert_limit_uses_swiss_midnight_and_counts_reservations(self):
        before = datetime.fromisoformat("2026-10-30T22:59:00+00:00").timestamp()
        state = {"alerts": [{"at": before, "status": "reserved"}, {"at": before, "status": "sent"}]}
        self.assertEqual(alert_slots(state, before, C), 0)
        self.assertEqual(alert_slots(state, before+120, C), 2)


class CostGuards(unittest.TestCase):
    def test_only_confirmed_free_plan_is_accepted(self):
        user = {"isPaying": False, "plan": {"tier": "FREE", "monthlyBasePriceUsd": 0, "monthlyUsageCreditsUsd": 5}}
        self.assertEqual(validate_free_plan(user), 5)
        for field, value in [("tier", "BRONZE"), ("monthlyBasePriceUsd", 19), ("monthlyUsageCreditsUsd", None)]:
            u = copy.deepcopy(user); u["plan"][field] = value
            with self.assertRaises(Stop): validate_free_plan(u)

    def test_current_free_price_checked_and_future_price_not_used(self):
        def pricing(at, price):
            return {"startedAt": at, "pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
                "tweet": {"isPrimaryEvent": True, "eventTieredPricingUsd": {"FREE": {"tieredEventPriceUsd": price}}}}}}
        actor = {"pricingInfos": [pricing(NOW-1, .00015), pricing(NOW+3600, .05)]}
        self.assertEqual(row_price(actor, NOW, C), .00015)
        with self.assertRaises(Stop): row_price(actor, NOW+7200, C)

    def test_unknown_price_and_new_fees_stop(self):
        with self.assertRaises(Stop): row_price({}, NOW, C)
        actor = {"pricingInfos": [{"startedAt": NOW-1, "pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
            "tweet": {"eventPriceUsd": .00015, "isPrimaryEvent": True}, "start": {"eventPriceUsd": .1}}}}]}
        with self.assertRaises(Stop): row_price(actor, NOW, C)

    def test_failed_run_is_cleared_without_restarting_actor(self):
        class API:
            def budget(self): return {"remaining": 2}
            def finish_x(self, run): raise ActorFinishedError("FAILED")
            def save(self, state): self.saved = copy.deepcopy(state)
            def start_x(self, *args): raise AssertionError("Must not restart")
        api = API(); state = {"pending_run": {"id": "run"}, "reserved_bucket": int(NOW//1800)}
        with self.assertRaises(ActorFinishedError): scan(None, api, None, state, NOW, C, "observe")
        self.assertNotIn("pending_run", state)

    def test_exhausted_budget_never_starts_actor(self):
        class API:
            def budget(self): return {"remaining": .01}
            def start_x(self, *args): raise AssertionError("Must not start")
        with self.assertRaises(Stop): scan(None, API(), None, {}, NOW, C, "observe")

    def test_empty_sample_cannot_report_success(self):
        class API:
            def budget(self): return {"remaining": 2}
            def finish_x(self, run): return []
            def save(self, state): pass
        class TG:
            def send(self, chat, text):
                self.message = text
        state = {"schema": 1, "pending_run": {"id": "run"}, "tweets": {}, "watch": {},
                 "markets": {}, "alerts": [], "audit": [], "telegram": {"chat_id": 10}}
        tg = TG()
        with self.assertRaises(Stop): scan(None, API(), tg, state, NOW, C, "sample")
        self.assertIn("noch nicht bestanden", tg.message)

    def test_cap_crossing_before_delivery_prevents_notification(self):
        class API:
            def budget(self): return {"remaining": 2}
            def finish_x(self, run): return []
            def save(self, state): pass
        class TG:
            def send(self, *args): raise AssertionError("Must not notify token over the cap")
        market, _ = select_market([pair()], CA, C, NOW)
        state = {"schema": 1, "pending_run": {"id": "run"}, "tweets": {}, "alerts": [], "audit": [],
                 "markets": {"base:"+CA: {**market, "observed": NOW-1800}}, "started_at": NOW-7200,
                 "watch": {CA: {"address": CA, "last_seen": NOW, "last_post_at": NOW-10,
                                 "confirmations": 1, "last_eligible_at": NOW-1800}}}
        over = pair(); over["marketCap"] = 1000000
        social = {"authors": 5, "velocity": 3, "engagement_delta": 30, "urls": []}
        with (patch("agent.dex_pairs", side_effect=[[pair()], [over]]),
              patch("agent.social_metrics", return_value=(social, [])),
              patch("agent.goplus", return_value=security()),
              patch("agent.gecko_trades", return_value=trades()),
              patch("agent.time.time", return_value=NOW)):
            scan(None, API(), TG(), state, NOW, C, "signals")
        self.assertEqual(state["alerts"], [])


class SpendingLimit(unittest.TestCase):
    def test_cent_ceiling_preserves_requested_result_limit(self):
        from urllib.parse import parse_qs, urlsplit
        for count in [1, 16]:
            calls = []
            api = Apify(None, "unused-test-token", C)
            def call(path, method="GET", payload=None):
                calls.append((path, method, payload))
                if method == "GET":
                    return {"data": {"pricingInfos": [{"startedAt": NOW-1, "pricingModel": "PAY_PER_EVENT",
                        "pricingPerEvent": {"actorChargeEvents": {"tweet": {
                        "eventPriceUsd": .00015, "isPrimaryEvent": True}}}}]}}
                return {"data": {"id": "test-run"}}
            api.call = call
            self.assertEqual(api.start_x({"maxItems": count}, NOW), "test-run")
            path, method, payload = calls[-1]
            params = parse_qs(urlsplit(path).query)
            self.assertEqual(params["maxTotalChargeUsd"], ["0.01"])
            self.assertEqual(payload["maxItems"], count)
            self.assertEqual(params["timeout"], ["45"])

    def test_price_increase_still_blocks_start(self):
        api = Apify(None, "unused-test-token", C)
        def call(path, method="GET", payload=None):
            self.assertEqual(method, "GET", "Price guard must stop before a new run")
            return {"data": {"pricingInfos": [{"startedAt": NOW-1, "pricingModel": "PAY_PER_EVENT",
                "pricingPerEvent": {"actorChargeEvents": {"tweet": {
                "eventPriceUsd": .00016, "isPrimaryEvent": True}}}}]}}
        api.call = call
        with self.assertRaises(Stop):
            api.start_x({"maxItems": 16}, NOW)


class Pairing(unittest.TestCase):
    CODE = "a-private-test-code-123456789"

    def update(self, text, chat=10, **changes):
        m = {"chat": {"id": chat, "type": "private"}, "from": {"id": chat, "is_bot": False},
             "date": NOW-5, "text": text}
        m.update(changes)
        return {"message": m}

    def test_whitespace_and_qualified_start_are_supported(self):
        for command in ["/start "+self.CODE, "  /start   "+self.CODE+"\n", "/start@MyBot\n"+self.CODE]:
            found, counts = pairing_candidates([self.update(command)], self.CODE, "MyBot", NOW)
            self.assertEqual(found, {10}); self.assertEqual(counts["start_with_code"], 1)

    def test_bare_start_and_wrong_secret_do_not_pair(self):
        rows = [self.update("/start"), self.update("/start incorrect-code-123456789")]
        found, counts = pairing_candidates(rows, self.CODE, "MyBot", NOW)
        self.assertFalse(found); self.assertEqual(counts["bare_start"], 1)
        self.assertEqual(counts["start_with_code"], 1)
        self.assertNotIn(self.CODE, str(counts))

    def test_different_bot_and_old_or_group_messages_do_not_pair(self):
        rows = [self.update("/start@OtherBot "+self.CODE),
                self.update("/start "+self.CODE, date=NOW-90000),
                self.update("/start "+self.CODE, chat={"id": 10, "type": "group"})]
        # Build the group row explicitly: chat= is the helper's identifier argument.
        rows[2]["message"]["chat"] = {"id": 10, "type": "group"}
        found, _ = pairing_candidates(rows, self.CODE, "MyBot", NOW)
        self.assertFalse(found)

    def test_unicode_secret_is_supported_but_remains_case_sensitive(self):
        code = "Mein-geheimer-Wächter-Code-2026"
        found, _ = pairing_candidates([self.update("/start "+code)], code, "MyBot", NOW)
        self.assertEqual(found, {10})
        found, _ = pairing_candidates([self.update("/start "+code.lower())], code, "MyBot", NOW)
        self.assertFalse(found)

    def test_multiple_matching_chats_remain_ambiguous(self):
        found, _ = pairing_candidates([self.update("/start "+self.CODE, chat=10),
                                      self.update("/start "+self.CODE, chat=20)], self.CODE, "MyBot", NOW)
        self.assertEqual(found, {10,20})


if __name__ == "__main__":
    unittest.main()
