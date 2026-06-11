# BETrack — Betfair Exchange Ingestion

A focused build brief for adding **Betfair Exchange** as a third data source alongside the Stoiximan and Novibet ingestion already shipped (see [BUILD_PROMPT.md](BUILD_PROMPT.md)). This document is Betfair-specific; everything else (canonical model, SQLite store layout, FastAPI/React layers, polling architecture) stays as the existing build brief specifies.

## Your Role

You are a **senior Python backend engineer** comfortable with asyncio, Pydantic, and reverse-engineering private REST APIs. Read `CLAUDE.md` first — same conventions as the wider build (minimal scope, no comments unless the *why* is non-obvious, no error-handling for cases that can't happen, no test suite/lint/CI unless asked).

## Mission

Add **Betfair Exchange** as a third bookmaker the system can fetch live odds from. Betfair is the canonical "sharp" reference market that the original product spec called for — its back/lay/liquidity data is what makes value-edge and stale-line detection mathematically meaningful (vs comparing two retail books against each other).

**Scope of this build:** the ingestion layer only — a `BetfairClient` that can list in-play events and fetch order-book data on demand. The normalization layer (mapping Betfair's exchange shape into the canonical model) and the storage / dashboard integration are **deferred** to the wider build's later phases. This build delivers a verified, self-contained client we can wire in once the storage refactor lands.

## Why Betfair is different (and why it matters)

Stoiximan and Novibet are traditional sportsbooks — each outcome has **one** price set by the bookmaker. Betfair Exchange is a peer-to-peer marketplace — each outcome has:

- **Back prices** — what someone is offering to pay you if you bet that the outcome happens. Multiple price levels with sizes (an order book).
- **Lay prices** — what someone is offering to pay if you bet *against* the outcome. Multiple price levels with sizes.
- **Last traded price** — the most recent matched bet.
- **Total matched volume** — how much money has changed hands on this market.
- **Available volume** — how much is sitting in the book unmatched.

This shape is **richer** than the sportsbooks. The canonical `OddsQuote` model will need optional fields for `lay_price`, `back_size`, `lay_size`, and `total_matched` when the storage layer is refactored. Until then, the `BetfairClient` returns raw Betfair JSON exactly as the API serves it.

## Operational constraints (READ THIS FIRST)

1. **Betfair geo-blocks Greek IPs at the application layer.** Confirmed empirically: requests from a Greek IP (Cosmote OTE / similar) return `302 → /gr` then `403 Forbidden`. This is *not* a Cloudflare bot-detection issue — Cloudflare lets the request through, then Betfair's app refuses based on country.

2. **The host machine running the BetfairClient MUST have a UK / Ireland / Malta / Italy / Spain / Germany IP.** Use any of:
   - A UK VPN (ProtonVPN UK, Mullvad UK, NordVPN UK — confirmed working with TUN-mode/system-wide routing).
   - A UK VPS or cloud VM (Oracle Cloud Free Tier London, Hetzner UK, Contabo UK, DigitalOcean LON, etc.). **Datacenter IPs are accepted** by Betfair — confirmed with `212.102.63.101` (CDN77 London).
   - Verified: the user's main dev machine has a Greek IP. Production deployment needs to consider this — the Betfair-fetching process likely lives on a separate UK host while Stoiximan/Novibet stay on the Greek host. Cross-host coordination is **out of scope** for this build (a separate phase).

3. **Cloudflare TLS-fingerprint bot detection still applies.** Like Stoiximan and Novibet, plain `aiohttp` / `requests` / `httpx` will get blocked. **MUST** use `curl_cffi.requests.AsyncSession(impersonate="chrome")`. `curl_cffi` is already in `requirements.txt`.

4. **`_ak` query parameter behaves differently per host** (confirmed empirically):
   - `ero.betfair.com` (order-book data) and `ips.betfair.com` (in-play state) **ignore `_ak` entirely** — any value or no value works.
   - `scan-inbf.betfair.com` (the navigation/listing endpoint) **validates `_ak`** — fake values like `_ak=foo` return `DSC-0034` faults. **An empty `_ak=` works**, as does a real captured key. The client uses `_ak=` (empty) for scan-inbf and omits it for the other hosts.
   - No actual authentication (no login, no real session token, no Betfair Developer Application Key). The `_ak` is best understood as a soft anti-replay token that scan-inbf alone enforces format-strictly.

5. **No cookies, no login, no Betfair Developer Application Key.** Everything in this build hits the same public web-app API the unauthenticated browser uses. We are NOT using `api.betfair.com` (the official Betting API), which requires an app key + login session — overkill for read-only odds data.

## Endpoints

Four hosts are involved. All use HTTPS, JSON responses (some compressed in transit; `curl_cffi` handles decompression).

### 1. Event listing — `scan-inbf.betfair.com`

The navigation/facet search endpoint that discovers in-play events.

```
POST https://scan-inbf.betfair.com/www/sports/navigation/facet/v1/search
     ?alt=json
     [&_ak=<anything>]              optional
Content-Type: application/json

Body:
{
  "filter": {
    "marketBettingTypes": ["ODDS", "ASIAN_HANDICAP_SINGLE_LINE", "ASIAN_HANDICAP_DOUBLE_LINE", "LINE"],
    "exchangeIds": [1],
    "productTypes": ["EXCHANGE"],
    "eventTypeIds": [1],            // 1=Soccer, 2=Tennis, 7522=Basketball (well-known Betfair IDs)
    "inPlay": true,
    "contentGroup": { "language": "en", "regionCode": "UK" },
    "selectBy": "RANK",
    "maxResults": 100
  },
  "facets": [
    { "type": "EVENT_TYPE", "maxValues": 10, "skipValues": 0, "applyNextTo": 0 },
    { "type": "EVENT",      "maxValues": 100, "skipValues": 0, "applyNextTo": 0 }
  ],
  "currencyCode": "GBP",
  "locale": "en_GB"
}
```

Response shape: `{ "facets": [...], "results": [...], "attachments": {...} }`. The `attachments` field hangs the actual event/market metadata off the facet values. The exact attachment shape needs runtime exploration if we want richer data — for an event LIST we can read event IDs out of `facets[?].values[?].key.eventId`.

**Important quirk:** scan-inbf's `inPlay: true` filter surfaces both *currently-playing* markets AND *ante-post markets on imminent fixtures + outrights* (Top Goalscorer, Tournament Winner, etc.) — all of which Betfair considers "in-play / immediately bettable". To restrict to actually-live matches, you have two options:
- Filter client-side using `event.openDate` (kept-alive ~3 hours after kickoff for football), OR
- Filter at the market level after fetching with `bymarket`: each `marketNode.state.inplay == true` flags truly in-progress markets.

The client currently returns the raw scan-inbf result — filtering policy belongs in the eventual mapper / pipeline layer.

A minimum-viable variant (just counts per sport, verified verbatim from the HAR) is:

```json
{ "filter": { "marketBettingTypes":["ASIAN_HANDICAP_SINGLE_LINE","ASIAN_HANDICAP_DOUBLE_LINE","ODDS","LINE"],
              "exchangeIds":[1], "productTypes":["EXCHANGE"],
              "contentGroup":{"language":"en","regionCode":"UK"},
              "selectBy":"RANK", "maxResults":0 },
  "textQuery": null,
  "facets":[{"type":"EVENT_TYPE","maxValues":7,"skipValues":0,"applyNextTo":0}],
  "currencyCode":"GBP", "locale":"en_GB" }
```

### 2. Order-book data — `ero.betfair.com` (the gold)

```
GET https://ero.betfair.com/www/sports/exchange/readonly/v1/bymarket
    ?_ak=<anything>
    &alt=json
    &currencyCode=GBP
    &locale=en_GB
    &marketIds=1.258344075,1.258344185,1.258344295,...   ← comma-separated, up to ~25 per call
    &rollupLimit=10                                       ← order-book depth (max levels per side)
    &rollupModel=STAKE
    &types=MARKET_STATE,MARKET_RATES,MARKET_DESCRIPTION,EVENT,
           RUNNER_DESCRIPTION,RUNNER_STATE,
           RUNNER_EXCHANGE_PRICES_BEST,                   ← the back/lay/liquidity field
           RUNNER_METADATA,MARKET_LICENCE,MARKET_LINE_RANGE_INFO
```

Response shape (one slice; full doc in the HAR sample under `_bf_bymarket.json` if it still exists):

```json
{
  "currencyCode": "GBP",
  "eventTypes": [{
    "eventTypeId": 1,
    "eventNodes": [{
      "eventId": 35624108,
      "event": {
        "eventName": "Shamrock Rovers v St Patricks",
        "countryCode": "IE",
        "timezone": "GMT",
        "openDate": "2026-05-29T19:00:00.000Z"
      },
      "marketNodes": [{
        "marketId": "1.258344075",
        "state": {
          "betDelay": 0,
          "inplay": false,
          "numberOfWinners": 1,
          "numberOfRunners": 3,
          "totalMatched": 13381.60,         ← matched volume on this market
          "totalAvailable": 19688.32,        ← unmatched book depth
          "lastMatchTime": "2026-05-29T18:50:16.387Z",
          "status": "OPEN",
          "version": 7425720753
        },
        "description": {
          "marketName": "Match Odds",
          "marketType": "MATCH_ODDS",
          "marketTime": "2026-05-29T19:00:00.000Z",
          "bettingType": "ODDS",
          "priceLadderDescription": {"type": "CLASSIC"}
        },
        "rates": { "marketBaseRate": 5.0, "discountAllowed": true },
        "runners": [{
          "selectionId": 184325,
          "handicap": 0.0,
          "description": { "runnerName": "Shamrock Rovers" },
          "state": {
            "lastPriceTraded": 2.54,
            "totalMatched": 0.0,
            "status": "ACTIVE"
          },
          "exchange": {
            "availableToBack": [
              { "price": 2.54, "size": 667.35 },           ← order book, descending price
              { "price": 2.52, "size": 173.19 },
              { "price": 2.50, "size": 344.90 }
            ],
            "availableToLay": [
              { "price": 2.58, "size": 621.43 },           ← ascending price
              { "price": 2.62, "size": 123.02 },
              { "price": 2.64, "size": 570.97 }
            ]
          }
        }, ... more runners ...]
      }]
    }]
  }]
}
```

**Conventions to remember:**
- `marketType: "MATCH_ODDS"` is the canonical 1X2 / Match Winner market name (used across sports — for tennis & basketball it's the head-to-head outcome too, since those sports don't have a draw, the runners list will just be 2 entries).
- Common other `marketType` values that map cleanly to our canonical model: `OVER_UNDER_25`, `OVER_UNDER_15`, `BOTH_TEAMS_TO_SCORE`, `DOUBLE_CHANCE`, `DRAW_NO_BET`, `HALF_TIME`, `HALF_TIME_FULL_TIME`, `CORRECT_SCORE`, `ASIAN_HANDICAP`. (Many more exist; map adaptively.)
- The "back price" (best from a punter's perspective) is `runner.exchange.availableToBack[0].price`. The "lay price" is `runner.exchange.availableToLay[0].price`. Fair price approximation = `(back + lay) / 2`.
- `runner.state.status == "ACTIVE"` means the runner is open for bets. `WINNER`/`LOSER` mean settled. `REMOVED` means scratched.
- `market.state.status == "OPEN"` means active. `SUSPENDED` means temporarily paused (frequent during in-play). `CLOSED` means settled.

### 3. Per-event drill-down — `ero.betfair.com/byevent`

```
GET https://ero.betfair.com/www/sports/exchange/readonly/v1/byevent
    ?_ak=<anything>
    &currencyCode=GBP
    &eventIds=35654074
    &locale=en_GB
    &rollupLimit=10
    &rollupModel=STAKE
    &types=MARKET_STATE,EVENT,MARKET_DESCRIPTION
```

Returns the same `eventTypes → eventNodes → marketNodes` shape but **scoped to specific event IDs**, with whichever `types` you request. Used to get the full market list for one event without already knowing every market ID. Pair this with a subsequent `bymarket` call (with `RUNNER_EXCHANGE_PRICES_BEST` in types) to actually pull the prices.

### 4. Per-event live state — `ips.betfair.com`

Three optional endpoints that sit outside the order-book API and provide in-play context:

```
GET https://ips.betfair.com/inplayservice/v1/scoresAndBroadcast
    ?_ak=<anything>&alt=json&eventIds=35654074,35654039,...&locale=en_GB&regionCode=UK
        → current scores + streaming/broadcast info

GET https://ips.betfair.com/inplayservice/v1/eventTimelines
    ?_ak=<anything>&alt=json&eventIds=...&locale=en_GB
        → goal/card/substitution events as a timeline

GET https://ips.betfair.com/inplayservice/v1/eventDetails
    ?_ak=<anything>&alt=json&eventIds=...&locale=en_GB&productType=EXCHANGE&regionCode=UK
        → per-event metadata (start time, teams, etc.)
```

These are nice-to-have for richer UI but **not required** for the core price ingestion. Defer wiring them until the dashboard needs them.

### 5. Per-event market categorization — `apieds.betfair.com`

```
POST https://apieds.betfair.com/api/eds/multimarkets/v4?_ak=<anything>
```

Returns markets grouped into UI tabs (Popular / Over-Under / Half Time / Asian Handicap / etc.) for the event implied by referer. We don't need this — `ero/byevent` gives us the same market list in a cleaner form. **Skip.**

## Required headers

In addition to `curl_cffi`'s default Chrome impersonation, send:

```
accept: application/json, text/plain, */*
accept-language: en-GB,en;q=0.9
origin: https://www.betfair.com
referer: https://www.betfair.com/exchange/plus/
user-agent: (curl_cffi's chrome UA is fine; explicitly setting it is harmless)
```

The `POST` to `scan-inbf` also needs `content-type: application/json`. Don't send cookies. Don't send any `x-application` header — that's only required by the *Betfair Betting API* (the paid one), which we are not using.

## Sport IDs (Betfair `eventTypeId`)

Well-known values for our target sports — verified against the `scan-inbf` facet response we captured:

| Sport | `eventTypeId` |
|---|---|
| Soccer (Football) | **1** |
| Tennis | **2** |
| Basketball | **7522** |

Other commonly seen ones (not in our scope but useful to know): Horse Racing = 7, Cricket = 4, Boxing = 6, MMA = 26420387, Esports = 27454571.

To discover unknown sport IDs at runtime, call `scan-inbf` with a `facets:[{"type":"EVENT_TYPE",...}]` request and read the `key.eventTypeId` from each facet value. Each value also carries a `cardinality` (market count) so we can rank by liquidity.

## Cross-bookmaker matching

**Betfair does NOT expose a Sportradar match ID in any of the endpoints above** — checked across `bymarket`, `byevent`, `eventDetails`, `scoresAndBroadcast`. Betfair uses StatsPerform / Opta as their stats provider (visible in HAR via `*.performfeeds.com` and `betfair.cpp.statsperform.com` calls), but the Opta `fixtureId` is also not surfaced on the betting endpoints we'd actually use.

This means cross-matching Betfair events with Stoiximan/Novibet ones requires **fuzzy matching**:

- **Primary keys:** `home_team` + `away_team` (normalized) + `start_time` (±5 minutes).
- **Tie-breakers:** competition / country if available.
- **Team-name normalization is required** — Betfair often uses different spellings than Stoiximan/Novibet (e.g., "Man Utd" vs "Manchester United"). Reuse and extend the `TEAM_ALIASES` table from `betrack/normalization/mapper.py`.

This matching layer is **out of scope** for this build — it belongs with the storage refactor (when we have a unified `events` table) and the cross-book detection re-enablement. The `BetfairClient` should expose Betfair's native event/market IDs and the event name + start time; the matching is a downstream concern.

## Implementation plan (this build)

Deliver **one file** under `betrack/ingestion/`:

```
betrack/ingestion/betfair.py
```

A `BetfairClient` class mirroring `StoiximanClient` / `NovibetClient`. Async context manager, `curl_cffi.requests.AsyncSession` underneath, methods that return raw JSON (no normalization). Specifically:

```python
class BetfairClient:
    async def __aenter__(self) -> "BetfairClient": ...
    async def __aexit__(self, *args) -> None: ...

    async def list_in_play(self, sport_id: int, max_results: int = 100) -> dict:
        # POST to scan-inbf; returns the facet/search response.

    async def fetch_markets(self, market_ids: list[str], rollup_limit: int = 10) -> dict:
        # GET ero/bymarket; returns the full bymarket response with EVENT + RUNNER_EXCHANGE_PRICES_BEST.

    async def fetch_event_markets(self, event_ids: list[int]) -> dict:
        # GET ero/byevent; returns the per-event market list.

    async def fetch_scores(self, event_ids: list[int]) -> dict:
        # GET ips/scoresAndBroadcast; optional helper for live state.
```

**Constants** in the same module: base URLs per host, the canonical `types=` projection used for `bymarket`, the standard headers dict, the sport ID enum / constants.

A `if __name__ == "__main__":` block at the bottom that runs a small async smoke test: list in-play football events, fetch order books for the first few, print a one-line summary per market. Used as `python -m betrack.ingestion.betfair`.

### SOCKS5 proxy support (Pattern 2 — wired)

`BetfairClient.__init__` accepts an optional `proxy: str | None`. When omitted it reads the **`BETRACK_BETFAIR_PROXY`** environment variable; if set, every request goes through that proxy (passed to `curl_cffi.AsyncSession` as `proxies={"all": <url>}`). Typical usage from a Greek dev machine with an SSH SOCKS tunnel to a UK VPS:

```powershell
# In one window: open the tunnel and leave it running
ssh -i "$HOME\.ssh\betrack-vps.key" -D 1080 -N opc@<vps-public-ip>

# In another: tell BetfairClient to route through it
$env:BETRACK_BETFAIR_PROXY = "socks5h://127.0.0.1:1080"
python -m betrack.ingestion.betfair
```

`socks5h://` (not just `socks5://`) is critical — the `h` makes the client send DNS through the tunnel too, so the laptop's local DNS doesn't leak a lookup for `betfair.com`. Without it, some setups end up doing DNS locally then connecting via tunnel — works for IPv4-only but flaky on dual-stack networks.

Stoiximan and Novibet clients should **not** use this proxy — they require a Greek IP. The env var is per-client (Betfair-only) by design.

## Out of scope for this build

- **`betrack/normalization/betfair_mapper.py`** — mapping Betfair's exchange shape to `CanonicalEvent / CanonicalMarket / CanonicalOutcome / OddsQuote`. Comes with the storage refactor.
- **Extending `OddsQuote`** with `lay_price`, `back_size`, `lay_size`, `total_matched` fields. Comes with the storage refactor.
- **Wiring Betfair into `pipeline.py`** alongside the Stoiximan + Novibet polling. Comes after the mapper.
- **Cross-host coordination** (running Betfair fetching on a UK host while the rest runs on the Greek dev machine). Operational concern; comes when we deploy.
- **SignalR / streaming** alternatives. Betfair has an Exchange Stream API but it requires an SSO app key and is the documented commercial path — we are explicitly *not* going there. REST polling at 5-10s intervals is sufficient.

## Smoke-testing this build

After `BetfairClient` is implemented, with a UK VPN active, run:

```powershell
python -m betrack.ingestion.betfair
```

Expected output: a list of in-play football events from `scan-inbf`, then sample back/lay prices for the first 2-3 markets pulled via `bymarket`. If you see a 403, the host's outbound IP is not in the UK — fix that before debugging anything else.

## Polling cadence (when wired in later)

Betfair's frontend itself polls `bymarket` every ~1-2 seconds during active in-play. For our purposes 5-10 seconds is plenty (we're not market-making). At ~30 live football events with 5-10 markets each, batching marketIds across requests keeps the rate well under any reasonable limit. There's no documented per-IP rate limit on the unauthenticated web API, but be polite: spread requests, cap concurrency, don't pull more than the dashboard actually displays.
