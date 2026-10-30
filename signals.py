"""Conservative, deterministic filters. A score is a rank, not a probability."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

EVM = re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{40}(?![a-fA-F0-9])")
SOL = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")
AD = re.compile(r"(?:#ad\b|#sponsored\b|paid partnership|paid promo|sponsored post|dm (?:me )?for promo|guaranteed (?:profit|returns))", re.I)
NEGATIVE = re.compile(r"\b(?:honeypot|scam|rugged|do not buy|don't buy|avoid this|rug pull|warning)\b", re.I)
BURN = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead", "11111111111111111111111111111111"}


def number(value):
    """Unknown and non-finite values remain unknown, never a healthy zero."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    n = number(value)
    if n is not None:
        return n / 1000 if n > 10**11 else n
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.replace(tzinfo=d.tzinfo or timezone.utc).timestamp()
    except ValueError:
        try:
            return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").timestamp()
        except ValueError:
            return None


def canonical(address):
    return address.lower() if address.startswith("0x") else address


def addresses(text):
    # Preserve the case of Solana addresses. Discard ordinary Base58-looking words
    # by requiring a 32-byte decoded public key.
    result = set(EVM.findall(text or ""))
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    for a in SOL.findall(text or ""):
        if a.startswith("0x"):
            continue
        n = 0
        for char in a:
            n = n * 58 + alphabet.index(char)
        size = (n.bit_length() + 7) // 8 + (len(a) - len(a.lstrip("1")))
        if size == 32:
            result.add(a)
    return {canonical(a) for a in result}


def normalise_tweet(row, now):
    if not isinstance(row, dict) or row.get("type") in {"error", "diagnostic", "diagnostics", "run-report"}:
        return None
    tweet_id = str(row.get("id") or row.get("tweetId") or "")
    text = row.get("text") or row.get("fullText") or ""
    created = timestamp(row.get("createdAt") or row.get("created_at") or row.get("createdAtIso"))
    author = row.get("author") or {}
    username = author.get("userName") or author.get("username") or author.get("screen_name")
    if not tweet_id.isdigit() or not text or not username or created is None or created > now + 60:
        return None
    links = list(row.get("links") or row.get("urls") or [])
    links.extend((row.get("entities") or {}).get("urls") or [])
    expanded = []
    for link in links:
        if isinstance(link, str):
            expanded.append(link)
        elif isinstance(link, dict):
            expanded.extend(str(link[k]) for k in ("expandedUrl", "expanded_url", "url") if link.get(k))
    stats = row.get("stats") or {}
    unknown_counts = {re.sub(r"[^a-z]", "", str(key).lower())
                      for key in (row.get("unknownCounts") or row.get("unknown_counts") or [])}
    def metric(*keys):
        if any(re.sub(r"[^a-z]", "", key.lower()) in unknown_counts for key in keys):
            return None
        for key in keys:
            val = number(row.get(key, stats.get(key)))
            if val is not None:
                return val
        return None
    # Public profile signals are heuristics, not proof of human followers.
    return {
        "id": tweet_id, "text": str(text)[:12000], "created": created,
        "author": str(username).lstrip("@").lower(),
        "author_created": timestamp(author.get("createdAt") or author.get("created_at")),
        "followers": number(author.get("followers", author.get("followers_count"))),
        "following": number(author.get("following", author.get("following_count"))),
        "lang": row.get("lang"), "is_retweet": bool(row.get("isRetweet") or row.get("is_retweet")),
        "addresses": sorted(addresses(str(text) + " " + " ".join(expanded))),
        "url": "https://x.com/" + str(username).lstrip("@") + "/status/" + tweet_id,
        "reply_count": metric("replyCount", "replies", "reply_count"),
        "retweet_count": metric("retweetCount", "retweets", "retweet_count"),
        "observed": now,
    }


def select_market(pairs, address, config, now):
    matched = [p for p in pairs if p.get("chainId") in config["chains"]
               and canonical(str((p.get("baseToken") or {}).get("address", ""))) == canonical(address)]
    chains = {p["chainId"] for p in matched}
    if len(chains) != 1:
        return None, ["Contract keiner eindeutigen unterstützten Chain zugeordnet"]
    matched.sort(key=lambda p: number((p.get("liquidity") or {}).get("usd")) or 0, reverse=True)
    p = matched[0]
    mc = number(p.get("marketCap"))  # Intentionally NO fallback to FDV.
    times = [timestamp(x.get("pairCreatedAt")) for x in matched]
    times = [t for t in times if t is not None and 0 < t <= now]
    age = now - min(times) if times else None
    liq = number((p.get("liquidity") or {}).get("usd"))
    vol = p.get("volume") or {}
    tx = (p.get("txns") or {}).get("h1") or {}
    changes = p.get("priceChange") or {}
    market = {
        "chain": p["chainId"], "address": address, "pair": p.get("pairAddress"),
        "symbol": str((p.get("baseToken") or {}).get("symbol") or "?")[:24],
        "market_cap": mc, "liquidity": liq, "age_seconds_lower_bound": age,
        "volume_h1": number(vol.get("h1")), "volume_m5": number(vol.get("m5")),
        "buys_h1": number(tx.get("buys")), "sells_h1": number(tx.get("sells")),
        "change_h1": number(changes.get("h1")), "change_m5": number(changes.get("m5")),
        "price": number(p.get("priceUsd")),
        "url": "https://dexscreener.com/" + p["chainId"] + "/" + str(p.get("pairAddress", "")),
        "pool_addresses": [str(x.get("pairAddress")) for x in matched if x.get("pairAddress")],
    }
    failures = []
    if mc is None or mc <= 0 or mc >= config["max_market_cap_usd"]:
        failures.append("Marktkapitalisierung fehlt oder liegt nicht unter USD 1 Mio.")
    if age is None or age < config["min_age_minutes"] * 60:
        failures.append("Mindestalter von einer Stunde nicht belegt")
    if liq is None or liq < config["min_liquidity_usd"]:
        failures.append("Liquidität unter Mindestwert oder unbekannt")
    if market["price"] is None or market["price"] <= 0 or not market["pair"]:
        failures.append("Preis oder Pool fehlt")
    # Only compare sufficiently liquid pools. Never sum duplicated pool volume.
    comparable = [number(x.get("marketCap")) for x in matched
                  if (number((x.get("liquidity") or {}).get("usd")) or 0) >= config["min_liquidity_usd"]]
    comparable = [x for x in comparable if x is not None and x > 0]
    if comparable and max(comparable) / min(comparable) > 1.15:
        failures.append("Widersprüchliche Bewertungen zwischen liquiden Pools")
    return market, failures


def security_checks(data, chain, config, pool_addresses=()):
    failures = []
    if not isinstance(data, dict) or not data:
        return ["Sicherheitsdaten fehlen"]
    def require(key, wanted="0", nested=False):
        value = data.get(key)
        if nested:
            value = value.get("status") if isinstance(value, dict) else None
        if value is None or value == "":
            failures.append("Sicherheitsfeld unbekannt: " + key)
        elif str(value) != wanted:
            failures.append("Sicherheitsfilter: " + key)
    if chain == "solana":
        for key in ("freezable", "mintable", "balance_mutable_authority", "closable", "transfer_hook_upgradable", "transfer_fee_upgradable"):
            require(key, nested=True)
        require("non_transferable")
        require("default_account_state", "1")
        if data.get("transfer_hook"):
            failures.append("Transfer-Hook vorhanden")
        creators = data.get("creators")
        if not isinstance(creators, list) or not creators:
            failures.append("Erstellerdaten unbekannt")
        else:
            for creator in creators:
                share = number(creator.get("percent"))
                if share is None:
                    failures.append("Erstelleranteil unbekannt")
                elif not 0 <= share <= config["max_creator_share"]:
                    failures.append("Erstelleranteil zu hoch")
        fee = data.get("transfer_fee")
        if fee is None:
            failures.append("Transfergebühr unbekannt")
        elif fee:
            values = [number(fee[k]) for k in ("fee_rate", "maximum_fee", "transfer_fee_basis_points") if k in fee]
            if not values or any(v is None or v != 0 for v in values):
                failures.append("Transfergebühr vorhanden oder unbekannt")
    else:
        require("is_open_source", "1")
        for key in ("is_proxy", "is_honeypot", "cannot_sell_all", "cannot_buy", "transfer_pausable", "is_blacklisted", "slippage_modifiable", "personal_slippage_modifiable", "is_mintable"):
            require(key)
        for key in ("buy_tax", "sell_tax"):
            if number(data.get(key)) != 0:
                failures.append("Handelssteuer vorhanden oder unbekannt: " + key)
        creator = number(data.get("creator_percent"))
        if creator is None or not 0 <= creator <= config["max_creator_share"]:
            failures.append("Erstelleranteil zu hoch oder unbekannt")
    holder_count = number(data.get("holder_count"))
    if holder_count is None or holder_count < 100:
        failures.append("Weniger als 100 belegte Halter")
    holders = data.get("holders")
    excluded = {canonical(a) for a in pool_addresses} | BURN
    shares = defaultdict(float)
    if not isinstance(holders, list) or len(holders) < 10:
        failures.append("Halterverteilung unvollständig")
    else:
        for h in holders:
            address = canonical(str(h.get("address") or h.get("account") or ""))
            if not address or address in excluded:
                continue
            share = number(h.get("percent"))
            if share is None or not 0 <= share <= 1:
                failures.append("Halteranteil unbekannt oder ungültig")
            else:
                shares[address] += share
        top = sorted(shares.values(), reverse=True)[:10]
        if not top or sum(top) <= 0:
            failures.append("Halteranteile nicht belastbar")
        elif max(top) > config["max_single_holder_share"] or sum(top) > config["max_top_ten_holder_share"]:
            failures.append("Konzentrierter Halterbesitz")
    return sorted(set(failures))


def trade_sample(rows, now, config):
    wallets, buyers = defaultdict(float), set()
    buy_volume = total = 0.0
    hashes = set()
    for row in rows:
        a = row.get("attributes") or {}
        ts = timestamp(a.get("block_timestamp"))
        volume = number(a.get("volume_in_usd"))
        wallet = a.get("tx_from_address")
        # Each executed swap is keyed by transaction AND event id. One transaction
        # may contain several valid swaps; an identical row must not be duplicated.
        event_id = row.get("id") or a.get("tx_hash")
        if ts is None or not now - 1800 <= ts <= now + 60 or volume is None or volume <= 0 or not wallet or not a.get("tx_hash") or event_id in hashes:
            continue
        hashes.add(event_id)
        wallets[str(wallet)] += volume
        total += volume
        if a.get("kind") == "buy":
            buyers.add(str(wallet))
            buy_volume += volume
    failures = []
    if len(wallets) < config["min_sample_wallets"] or len(buyers) < config["min_sample_buyers"]:
        failures.append("Zu wenige verschiedene Wallets in der Transaktionsstichprobe")
    if total <= 0 or max(wallets.values(), default=0) / max(total, 1e-12) > config["max_wallet_volume_share"]:
        failures.append("Volumenstichprobe fehlt oder wird von einer Wallet dominiert")
    if total > 0 and buy_volume / total < 0.55:
        failures.append("Kein bestätigter Kaufüberhang in der Stichprobe")
    return {"wallets": len(wallets), "buyers": len(buyers), "volume": total,
            "buy_share": buy_volume / total if total else None}, failures


def social_metrics(tweets, address, now, config):
    relevant = [t for t in tweets if canonical(address) in t["addresses"] and now - 7200 <= t["created"] <= now]
    failures, valid, authors = [], [], set()
    for t in relevant:
        if AD.search(t["text"]):
            failures.append("Erkennbare bezahlte Werbung")
        if NEGATIVE.search(t["text"]):
            failures.append("Negativer Sicherheitshinweis in der X-Stichprobe")
        created, followers, following = t.get("author_created"), t.get("followers"), t.get("following")
        if (t.get("lang") != config["language"] or t.get("is_retweet") or created is None
            or now - created < config["min_author_age_days"] * 86400
            or followers is None or followers < config["min_author_followers"]
            or following is None or following > max(1000, followers * 2)):
            continue
        valid.append(t)
        authors.add(t["author"])
    texts = []
    for t in valid:
        s = EVM.sub("CONTRACT", t["text"].lower())
        s = re.sub(r"https?://\S+|@[\w_]+|\$[\w_]+", "", s)
        s = " ".join(s.split())
        for old, author in texts:
            if t["author"] != author and len(s) > 30 and SequenceMatcher(None, s, old).ratio() > 0.90:
                failures.append("Nahezu identische Texte verschiedener Accounts")
        texts.append((s, t["author"]))
    recent = sum(t["created"] >= now - 1800 for t in valid)
    older = len(valid) - recent
    # Counts describe ONLY the collected sample; no inference of total X volume.
    velocity = recent / max(1, older / 3)
    engagement_delta = 0
    refreshed = 0
    for t in valid:
        old = t.get("previous")
        if not old or not 300 <= now - old.get("observed", now) <= 5400 or old.get("observed", 0) < t["created"]:
            continue
        for key in ("reply_count", "retweet_count"):
            if t.get(key) is not None and old.get(key) is not None:
                engagement_delta += max(0, t[key] - old[key])
        refreshed += 1
    if len(authors) < config["min_independent_authors"]:
        failures.append("Weniger als drei geeignete, verschiedene Autoren")
    if recent < 2 or velocity < 1.5:
        failures.append("Social Velocity in der Stichprobe nicht bestätigt")
    if refreshed == 0 or engagement_delta < 5:
        failures.append("Reply-/Retweet-Zuwachs noch nicht gemessen")
    return {"authors": len(authors), "mentions_30m": recent, "velocity": velocity,
            "engagement_delta": engagement_delta, "refreshed": refreshed,
            "urls": list(dict.fromkeys(t["url"] for t in valid))[:3]}, sorted(set(failures))


def evaluate(market, social, trades, config, previous_market=None):
    failures = []
    v1, v5 = market.get("volume_h1"), market.get("volume_m5")
    buys, sells = market.get("buys_h1"), market.get("sells_h1")
    if v1 is None or v1 < config["min_hourly_volume_usd"]:
        failures.append("Stundenvolumen fehlt oder ist zu gering")
    if buys is None or sells is None or buys + sells < config["min_hourly_transactions"] or sells < 5:
        failures.append("Zu wenig belegte Handelsaktivität inklusive Verkäufen")
    h1, m5 = market.get("change_h1"), market.get("change_m5")
    if h1 is None or not 0 < h1 <= config["max_hourly_price_increase_pct"] or m5 is None or m5 <= 0:
        failures.append("Preismomentum fehlt, ist negativ oder bereits überdehnt")
    acceleration = 12 * v5 / v1 if v1 and v5 is not None else 0
    if acceleration < 1.5:
        failures.append("Kurzfristiges Handelsvolumen beschleunigt sich nicht ausreichend")
    if previous_market is None:
        failures.append("Zweiter Marktdatenpunkt fehlt")
    elif market["liquidity"] < (previous_market.get("liquidity") or math.inf) * 0.85:
        failures.append("Liquidität ist seit der letzten Prüfung stark gefallen")
    score = (min(social["authors"] / 5, 1) * 12
             + min(social["velocity"] / 3, 1) * 12
             + min(social["engagement_delta"] / 30, 1) * 11
             + min(acceleration / 3, 1) * 15
             + min((trades["buy_share"] or 0) / 0.7, 1) * 10
             + min(trades["wallets"] / 20, 1) * 10
             + min(market["liquidity"] / 75000, 1) * 15
             + 15)  # The final 15 are only usable after security checks pass.
    return round(score, 1), failures


def confirmed(entry, eligible, now, config):
    old_time = entry.get("last_eligible_at", 0)
    # Repeating a manual run immediately cannot create a second confirmation.
    same_scan = now - old_time < 1200
    if not eligible:
        entry["confirmations"] = 0
        entry.pop("last_eligible_at", None)
        return False
    if same_scan:
        return entry.get("confirmations", 0) >= config["consecutive_confirmations"]
    if now - old_time > config["max_confirmation_gap_minutes"] * 60:
        entry["confirmations"] = 0
    entry["confirmations"] = entry.get("confirmations", 0) + 1
    entry["last_eligible_at"] = now
    return entry["confirmations"] >= config["consecutive_confirmations"]
