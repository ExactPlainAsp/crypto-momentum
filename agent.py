#!/usr/bin/env python3
"""Free-tier momentum observer. Python 3.12+, standard library only.

Never signs transactions, logs secrets, falls back to FDV, or upgrades an account.
All live signal alerts are explicitly labelled research observations.
"""
from __future__ import annotations

import argparse
import hmac
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo

from signals import (canonical, confirmed, evaluate, normalise_tweet, number,
                     security_checks, select_market, social_metrics, timestamp,
                     trade_sample)

ROOT = Path(__file__).resolve().parent
CHAIN_IDS = {"base": "8453", "bsc": "56", "monad": "143"}
GECKO_IDS = {"solana": "solana", "base": "base", "bsc": "bsc", "monad": "monad"}
KNOWN_SOL = "So11111111111111111111111111111111111111112"


class Stop(Exception):
    """Safe, user-facing stop reason; never contains a raw request URL."""


class ActorFinishedError(Stop):
    """A terminal run may be cleared without restarting it in the same slot."""


class HTTP:
    def json(self, url, method="GET", payload=None, token=None, timeout=20):
        headers = {"User-Agent": "CryptoMomentumObserver/0.1", "Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            # No POST retry: repeating a paid Actor start could charge twice.
            with urllib.request.urlopen(request, timeout=timeout) as r:
                raw = r.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise Stop("Antwort über der zulässigen Grösse")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise Stop(f"Dienst {urllib.parse.urlsplit(url).hostname}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Stop(f"Dienst {urllib.parse.urlsplit(url).hostname} nicht erreichbar") from None
        except (ValueError, UnicodeDecodeError):
            raise Stop(f"Dienst {urllib.parse.urlsplit(url).hostname}: ungültige Daten") from None


def secret(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise Stop("GitHub-Secret fehlt: " + name)
    return value


def validate_free_plan(user):
    plan = user.get("plan") or {}
    is_free = str(plan.get("tier") or plan.get("id") or "").upper() == "FREE"
    if not is_free or number(plan.get("monthlyBasePriceUsd")) != 0 or user.get("isPaying") is True:
        raise Stop("Nullkosten-Schutz: Apify-Free-Tarif nicht eindeutig bestätigt")
    credits = number(plan.get("monthlyUsageCreditsUsd"))
    if credits is None or credits <= 0:
        raise Stop("Monatliches Gratisguthaben unbekannt")
    return credits


def row_price(actor, now, config):
    active = [p for p in actor.get("pricingInfos", []) if (timestamp(p.get("startedAt")) or float("inf")) <= now]
    if not active:
        raise Stop("Aktueller X-Datenpreis nicht nachprüfbar")
    current = max(active, key=lambda p: timestamp(p["startedAt"]))
    if current.get("pricingModel") != "PAY_PER_EVENT":
        raise Stop("Preismodell wurde geändert")
    events = (current.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
    prices = []
    for e in events.values():
        p = number((e.get("eventTieredPricingUsd", {}).get("FREE") or {}).get("tieredEventPriceUsd", e.get("eventPriceUsd")))
        if p is None:
            raise Stop("Preis für den Free-Tarif fehlt")
        if p > 0 and not e.get("isPrimaryEvent"):
            raise Stop("Zusätzliche Actor-Gebühr erkannt; Preis erneut prüfen")
        if e.get("isPrimaryEvent"):
            prices.append(p)
    if len(prices) != 1 or not 0 < prices[0] <= config["max_row_price_usd"]:
        raise Stop("X-Datenpreis über der freigegebenen Gratisbudget-Kalkulation")
    return prices[0]


class Apify:
    def __init__(self, http, token, config):
        self.http, self.token, self.config = http, token, config
        self.store_id = None

    def call(self, path, method="GET", payload=None, timeout=20):
        return self.http.json("https://api.apify.com/v2/" + path, method, payload, self.token, timeout)

    def budget(self):
        credits = validate_free_plan(self.call("users/me")["data"])
        data = self.call("users/me/usage/monthly")["data"]
        used = number(data.get("totalUsageCreditsUsdAfterVolumeDiscount"))
        cycle = data.get("usageCycle") or {}
        if used is None or not cycle.get("endAt") or not cycle.get("startAt"):
            raise Stop("Guthabenverbrauch oder Abrechnungszyklus unbekannt")
        limit = min(credits, self.config["monthly_credit_cutoff_usd"])
        remaining = limit - used
        return {"used": used, "remaining": remaining, "cycle": cycle}

    def init_store(self):
        name = urllib.parse.quote(self.config["apify_store_name"], safe="")
        self.store_id = self.call("key-value-stores?name=" + name, "POST")["data"]["id"]

    def load(self):
        if not self.store_id:
            self.init_store()
        # List first, because a missing record is a valid empty initial state.
        keys = self.call(f"key-value-stores/{self.store_id}/keys?limit=100")["data"]
        if not any(i.get("key") == "STATE" for i in keys.get("items", [])):
            return {"schema": 1, "tweets": {}, "markets": {}, "watch": {}, "alerts": [], "audit": []}
        state = self.call(f"key-value-stores/{self.store_id}/records/STATE")
        if state.get("schema") != 1:
            raise Stop("Unbekannte Zustandsversion; kein automatischer Neustart")
        return state

    def save(self, state):
        if len(json.dumps(state).encode()) > 2_000_000:
            raise Stop("Zustand zu gross; Aufbewahrung prüfen")
        self.call(f"key-value-stores/{self.store_id}/records/STATE", "PUT", state)

    def start_x(self, inputs, now):
        price = row_price(self.call("acts/" + self.config["apify_actor"])["data"], now, self.config)
        # Fractional-cent limits stopped the public one-row probe as budget_limited.
        # Round the spending ceiling up to cents; maxItems still caps result rows.
        cap = (Decimal(inputs["maxItems"]) * Decimal(str(price))).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        args = urllib.parse.urlencode({"timeout": 45, "memory": 256, "maxTotalChargeUsd": f"{cap:.2f}"})
        return self.call("acts/" + self.config["apify_actor"] + "/runs?" + args, "POST", inputs)["data"]["id"]

    def finish_x(self, run_id):
        for _ in range(27):
            run = self.call("actor-runs/" + run_id)["data"]
            status = run.get("status")
            if status in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
                if status != "SUCCEEDED":
                    raise ActorFinishedError("X-Abfrage endete mit " + str(status) + "; kein Neustart im selben Zeitfenster")
                dataset = run.get("defaultDatasetId")
                if not dataset:
                    raise Stop("X-Abfrage liefert kein Dataset")
                return self.call(f"datasets/{dataset}/items?clean=true&limit=100")
            time.sleep(2)
        raise Stop("X-Abfrage läuft noch; beim nächsten Durchlauf nur Status abrufen")


class Telegram:
    def __init__(self, http, token):
        self.http, self.token = http, token

    def call(self, method, data=None):
        result = self.http.json("https://api.telegram.org/bot" + self.token + "/" + method, "POST", data or {})
        if not result.get("ok"):
            raise Stop("Telegram-Anfrage fehlgeschlagen")
        return result["result"]

    def send(self, chat_id, text):
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                         "link_preview_options": {"is_disabled": True}})


def pairing_candidates(updates, code, bot_username, now):
    """Find a private chat using the exact secret, allowing command whitespace.

    Return only counts as diagnostics. Never return received text or secret parts.
    """
    matches = set()
    counts = {"private_messages": 0, "bare_start": 0, "start_with_code": 0}
    commands = {"/start", "/start@" + bot_username.lower()}
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        date = number(message.get("date"))
        sender = message.get("from") or {}
        if (chat.get("type") != "private" or sender.get("is_bot") or date is None
            or not now - 86400 < date <= now + 60 or chat.get("id") is None):
            continue
        counts["private_messages"] += 1
        text = message.get("text")
        if not isinstance(text, str):
            continue
        parts = text.strip().split(maxsplit=1)
        if not parts or parts[0].lower() not in commands:
            continue
        if len(parts) == 1:
            counts["bare_start"] += 1
            continue
        counts["start_with_code"] += 1
        supplied = parts[1].strip()
        if hmac.compare_digest(supplied.encode("utf-8"), code.encode("utf-8")):
            matches.add(chat["id"])
    return matches, counts


def pair_telegram(tg, state, apify, now):
    code = secret("TELEGRAM_PAIR_CODE")
    if len(code) < 20:
        raise Stop("Kopplungscode muss mindestens 20 Zeichen lang sein")
    bot = tg.call("getMe")
    username = bot.get("username") or ""
    print("Erreichbarer Telegram-Bot: @" + username)
    if (tg.call("getWebhookInfo") or {}).get("url"):
        raise Stop("Dieser Bot hat bereits einen Webhook; bitte einen eigenen neuen Bot verwenden")
    # This dedicated new bot has no other consumer. Read its latest pending
    # messages so old updates cannot hide a recent valid pairing command.
    messages = tg.call("getUpdates", {"offset": -100, "limit": 100, "timeout": 0, "allowed_updates": ["message"]})
    matches, counts = pairing_candidates(messages, code, username, now)
    print("Aktuelle private Nachrichten: " + str(counts["private_messages"]))
    print("Startbefehle mit Code: " + str(counts["start_with_code"]))
    print("Startbefehle ohne Code: " + str(counts["bare_start"]))
    if not matches:
        if counts["start_with_code"]:
            reason = "Der empfangene Startcode stimmt nicht mit TELEGRAM_PAIR_CODE überein."
        elif counts["private_messages"]:
            reason = "Es liegt keine Startnachricht mit Kopplungscode vor. Der Startknopf allein genügt nicht."
        else:
            reason = "Der Bot hat noch keine aktuelle private Nachricht erhalten."
        raise Stop(reason + " Sende /start gefolgt von deinem geheimen Kopplungscode an @" + username + ".")
    if len(matches) != 1:
        raise Stop("Derselbe Kopplungscode wurde in mehreren Chats verwendet. Bitte einen neuen Code setzen und nur deinem eigenen Bot senden")
    chat_id = matches.pop()
    state["telegram"] = {"chat_id": chat_id, "bot_id": bot["id"], "paired_at": now}
    apify.save(state)
    tg.send(chat_id, "Verbindung hergestellt. Der Momentum-Wächter ist vorbereitet. Die Überwachung wird erst nach dem Testlauf aktiviert.")
    print("Telegram erfolgreich gekoppelt. Chat-ID wird ausschliesslich im privaten Zustand gespeichert.")


def dex_pairs(http, address):
    data = http.json("https://api.dexscreener.com/latest/dex/tokens/" + urllib.parse.quote(address, safe=""))
    if not isinstance(data.get("pairs"), list):
        return []
    return data["pairs"]


def goplus(http, market):
    address, chain = market["address"], market["chain"]
    if chain == "solana":
        path = "solana/token_security"
    elif chain in CHAIN_IDS:
        path = "token_security/" + CHAIN_IDS[chain]
    else:
        raise Stop("Chain ohne unterstützte Sicherheitsprüfung")
    data = http.json("https://api.gopluslabs.io/api/v1/" + path + "?contract_addresses=" + urllib.parse.quote(address, safe=""))
    if data.get("code") != 1:
        raise Stop("Sicherheitsdaten nicht verfügbar")
    entries = data.get("result") or {}
    for key, value in entries.items():
        if canonical(key) == canonical(address):
            return value
    raise Stop("Sicherheitsdaten zum Contract fehlen")


def gecko_trades(http, market):
    network = GECKO_IDS[market["chain"]]
    url = "https://api.geckoterminal.com/api/v2/networks/" + network + "/pools/" + urllib.parse.quote(market["pair"], safe="") + "/trades"
    data = http.json(url)
    if not isinstance(data.get("data"), list):
        raise Stop("Transaktionsstichprobe fehlt")
    # Orient buys/sells by the exact token, not by another provider's pool order.
    result = []
    for row in data["data"]:
        a = row.get("attributes") or {}
        to = canonical(str(a.get("to_token_address") or ""))
        fr = canonical(str(a.get("from_token_address") or ""))
        if a.get("is_anomalous") is True:
            continue
        if to == canonical(market["address"]):
            a["kind"] = "buy"
        elif fr == canonical(market["address"]):
            a["kind"] = "sell"
        else:
            continue
        result.append(row)
    return result


def x_input(state, now, config, sample=False):
    # Alternate focused discovery with contract-specific follow-up. All reads,
    # including repeated metric refreshes, count toward the same small quota.
    phase = int(now // 1800) % 3
    since = int(now - 7200)
    seeds = "(" + " OR ".join("from:" + a for a in config["seed_accounts"]) + ")"
    watch = sorted((w for w in state["watch"].values() if w.get("last_post_at", 0) >= now - 7200),
                   key=lambda w: w.get("last_post_at", 0), reverse=True)
    if sample:
        terms = ["(solana OR memecoin) lang:en -filter:nativeretweets since_time:" + str(since)]
    elif phase == 1 and watch:
        terms = ["(" + " OR ".join('"' + w["address"] + '"' for w in watch[:3]) + ") lang:en since_time:" + str(since)]
    elif phase == 2:
        terms = ["(solana OR memecoin OR base OR monad OR bsc) (CA OR contract) lang:en -filter:nativeretweets since_time:" + str(since)]
    else:
        terms = [seeds + " lang:en -filter:nativeretweets since_time:" + str(since)]
    return {"mode": "search", "searchTerms": terms, "queryType": "Latest", "lang": config["language"],
            "maxItems": 1 if sample else config["max_rows_per_scan"], "outputVariant": "rich", "fieldStyle": "camelCase"}


def ingest(state, rows, now, config):
    if not isinstance(rows, list):
        raise Stop("X-Dataset hat ein unbekanntes Format")
    count = 0
    for row in rows:
        t = normalise_tweet(row, now)
        if t is None or t["created"] < now - config["retention_hours"] * 3600:
            continue
        old = state["tweets"].get(t["id"])
        if old and old["observed"] < now - 300:
            t["previous"] = {k: old[k] for k in ("observed", "reply_count", "retweet_count")}
        elif old and old.get("previous"):
            t["previous"] = old["previous"]
        state["tweets"][t["id"]] = t
        count += 1
        for address in t["addresses"]:
            watch = state["watch"].setdefault(address, {"address": address, "first_seen": now})
            watch["last_seen"] = now
            watch["last_post_at"] = max(watch.get("last_post_at", 0), t["created"])
    cutoff = now - config["retention_hours"] * 3600
    state["tweets"] = {k: v for k, v in state["tweets"].items() if v["created"] >= cutoff}
    state["watch"] = {k: v for k, v in state["watch"].items() if v["last_seen"] >= cutoff}
    state["markets"] = {k: v for k, v in state["markets"].items() if v.get("observed", 0) >= cutoff}
    state["alerts"] = [a for a in state["alerts"] if a["at"] >= now - 30 * 86400]
    return count


def message(m, s, t, score, config):
    esc = lambda x: html.escape(str(x))
    portfolio = number(os.environ.get("PORTFOLIO_CHF"))
    position = f"CHF {portfolio * config['position_fraction']:.2f}" if portfolio is not None and portfolio > 0 else "10 % deines aktuellen Portfolios"
    links = "\n".join('<a href="' + esc(url) + '">X-Quelle ' + str(i + 1) + "</a>" for i, url in enumerate(s["urls"]))
    return (f"<b>{esc(config['name'])}: {esc(m['symbol'])}</b>\n"
            f"Forschungsbeobachtung, Score {score:.1f}/100\n"
            f"Chain: {esc(m['chain'])}\nContract: <code>{esc(m['address'])}</code>\n"
            f"Marktkapitalisierung: USD {m['market_cap']:,.0f}\nLiquidität: USD {m['liquidity']:,.0f}\n"
            f"Volumen 1 h: USD {m['volume_h1']:,.0f}\n"
            f"X-Stichprobe: {s['authors']} geeignete Autoren; Replies/Reposts +{s['engagement_delta']:.0f}\n"
            f"Trades-Stichprobe: {t['wallets']} Wallets, {t['buyers']} Käufer\n"
            f"Positionsrahmen: {esc(position)}\n"
            "In Fomo den Contract suchen und den ausführbaren Kauf-/Verkaufskurs samt Gebühren prüfen.\n"
            "Bot- und Insiderfilter sind Indizien. Fomo-Handelbarkeit und Nettoziel +30 % in 6 h sind nicht bestätigt.\n"
            f'<a href="{esc(m["url"])}">DexScreener</a>\n' + links)


def alert_slots(state, now, config):
    tz = ZoneInfo(config["timezone"])
    today = datetime.fromtimestamp(now, tz).date()
    used = sum(datetime.fromtimestamp(a["at"], tz).date() == today for a in state["alerts"])
    return max(0, config["max_signals_per_day"] - used)


def scan(http, api, tg, state, now, config, mode):
    budget = api.budget()
    if budget["remaining"] <= config["credit_reserve_usd"]:
        raise Stop("Gratisbudget-Grenze erreicht. X-Abfragen pausieren bis zur Erneuerung des Guthabens")
    sample = mode == "sample"
    bucket = int(now // 1800)
    pending = state.get("pending_run")
    if pending:
        try:
            rows = api.finish_x(pending["id"])
        except ActorFinishedError:
            state.pop("pending_run", None)
            api.save(state)
            raise
        state.pop("pending_run", None)
    else:
        if state.get("reserved_bucket") == bucket:
            raise Stop("Dieses 30-Minuten-Fenster wurde bereits verwendet")
        state["reserved_bucket"] = bucket
        state.setdefault("started_at", now)
        api.save(state)  # Reserve BEFORE starting any billable Actor.
        run_id = api.start_x(x_input(state, now, config, sample), now)
        state["pending_run"] = {"id": run_id, "started": now}
        api.save(state)
        try:
            rows = api.finish_x(run_id)
        except ActorFinishedError:
            state.pop("pending_run", None)
            api.save(state)
            raise
        state.pop("pending_run", None)
    ingested = ingest(state, rows, now, config)
    api.save(state)
    if sample:
        if not ingested:
            tg.send(state["telegram"]["chat_id"], "X-Test noch nicht bestanden: Der Abruf hat keine auswertbaren Posts geliefert. Der Zeitplan bleibt ausgeschaltet. Dies ist eine technische Testmeldung.")
            raise Stop("X-Test ohne auswertbare Posts. Keine erfolgreiche Datenabnahme")
        tg.send(state["telegram"]["chat_id"], f"Datenabruf-Test abgeschlossen: {ingested} gültige X-Datensätze. Dies ist keine Signalmeldung. Der Zeitplan bleibt bis zur Aktivierung ausgeschaltet.")
        print("X-Test erfolgreich. Kein Handelssignal erzeugt.")
        return
    reasons = Counter()
    ready = []
    candidates = sorted((w for w in state["watch"].values() if w.get("last_post_at", 0) >= now - 7200),
                        key=lambda w: w.get("last_post_at", 0), reverse=True)[:config["max_scan_candidates"]]
    for item in candidates:
        address = item["address"]
        failures = []
        try:
            market, failures = select_market(dex_pairs(http, address), address, config, now)
            social, social_failures = social_metrics(list(state["tweets"].values()), address, now, config)
            failures.extend(social_failures)
            key = market["chain"] + ":" + address if market else address
            old = state["markets"].get(key)
            if market is not None:
                state["markets"][key] = {**market, "observed": now}
            # Minimise calls. Missing social confirmation is a hard stop and is
            # logged before further security or transaction reads.
            if not failures and market:
                failures.extend(security_checks(goplus(http, market), market["chain"], config, market["pool_addresses"]))
                if not failures:
                    trades, trade_failures = trade_sample(gecko_trades(http, market), now, config)
                    failures.extend(trade_failures)
                    score, market_failures = evaluate(market, social, trades, config, old)
                    failures.extend(market_failures)
                    if score < config["min_score"]:
                        failures.append("Score unter Meldeschwelle")
                    if now - state.get("started_at", now) < config["warmup_minutes"] * 60:
                        failures.append("Aufwärmphase läuft")
                    if confirmed(item, not failures, now, config):
                        ready.append((score, key, market, social, trades))
            if failures:
                confirmed(item, False, now, config)
                reasons.update(set(failures))
        except Stop as e:
            confirmed(item, False, now, config)
            reasons.update([str(e)])
    state["audit"].append({"at": now, "rows": ingested, "candidates": len(candidates), "ready": len(ready), "rejections": dict(reasons), "mode": mode})
    state["audit"] = state["audit"][-336:]
    api.save(state)
    if mode == "signals":
        for score, key, m, s, t in sorted(ready, key=lambda v: v[0], reverse=True):
            send_at = time.time()
            if alert_slots(state, send_at, config) == 0:
                break
            if any(a["key"] == key and send_at - a["at"] < 86400 for a in state["alerts"]):
                continue
            # A token can cross USD 1m while other data is being checked.
            try:
                fresh, final_failures = select_market(dex_pairs(http, m["address"]), m["address"], config, send_at)
            except Stop:
                continue
            if final_failures or not fresh or fresh["pair"] != m["pair"] or fresh["liquidity"] < m["liquidity"] * .85:
                continue
            m = fresh
            # Reserve before sending. A network timeout must never create an
            # automatic duplicate or breach the maximum of two daily signals.
            state["alerts"].append({"at": send_at, "key": key, "status": "reserved", "score": score,
                                     "net_outcome": "unverified", "reference_price": m["price"]})
            api.save(state)
            tg.send(state["telegram"]["chat_id"], message(m, s, t, score, config))
            state["alerts"][-1]["status"] = "sent"
            api.save(state)
    print(f"Prüfung abgeschlossen: {ingested} X-Datensätze, {len(candidates)} Kandidaten, {len(ready)} bestätigte Beobachtungen. Modus: {mode}.")
    for reason, count in reasons.most_common(6):
        print(f"  {count} × {reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pair", "check", "sample", "observe", "signals"])
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    # A public repository must use a free standard runner. Never silently run on
    # a private repository with potentially exhausted included minutes.
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("REPOSITORY_VISIBILITY") != "public":
        raise Stop("Nullkosten-Schutz: Repository muss für den vorgesehenen GitHub-Gratisbetrieb öffentlich sein")
    if args.mode == "signals" and os.environ.get("SIGNAL_ALERTS_ENABLED") != "true":
        raise Stop("Signalmeldungen sind noch nicht aktiviert")
    http, now = HTTP(), time.time()
    api = Apify(http, secret("APIFY_TOKEN"), config)
    tg = Telegram(http, secret("TELEGRAM_BOT_TOKEN"))
    budget = api.budget()  # Free-only guard BEFORE persistent writes or Actor starts.
    state = api.load()
    if args.mode == "pair":
        pair_telegram(tg, state, api, now)
        return
    link = state.get("telegram") or {}
    bot = tg.call("getMe")
    if not link.get("chat_id") or link.get("bot_id") != bot.get("id"):
        raise Stop("Telegram noch nicht gekoppelt. Zuerst pair starten")
    if args.mode == "check":
        pairs = dex_pairs(http, KNOWN_SOL)
        if not pairs:
            raise Stop("DexScreener-Verbindungstest lieferte keine Daten")
        proof = goplus(http, {"chain": "solana", "address": KNOWN_SOL})
        if not proof:
            raise Stop("Sicherheitsdienst-Verbindungstest fehlgeschlagen")
        tg.send(link["chat_id"], "Verbindungstest erfolgreich: Telegram, Apify-Free, DexScreener und Sicherheitsdienst erreichbar. X-Abruf wird separat mit sample getestet. Keine Überwachung aktiviert.")
        print(f"Verbindungstest erfolgreich. Gratisguthaben bisher beansprucht: USD {budget['used']:.3f}.")
        return
    scan(http, api, tg, state, now, config, args.mode)


if __name__ == "__main__":
    try:
        main()
    except Stop as e:
        print("GESTOPPT: " + str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Raw exceptions may include URLs or response bodies with credentials.
        print("GESTOPPT: Unerwartetes Datenformat oder Konfigurationsproblem (" + type(e).__name__ + "). Keine Signalmeldung.", file=sys.stderr)
        sys.exit(1)
