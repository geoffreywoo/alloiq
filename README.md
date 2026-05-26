# AlloIQ

AlloIQ (`alloiq.com`) is a read-only AI-forward public markets intelligence
system. It tracks designated hedge fund 13F filings, daily news and price
movement, macro conditions, and publishes recommended research moves to a public
static website.

It does not execute orders, store broker login passwords, or present regulated
financial advice. The output is intended to organize information for your own
investment decisions.

## Quick Start

```bash
python3 -m invest init
python3 -m invest filings --manager all --max-filings 2
python3 -m invest sync --broker ibkr
python3 -m invest brief --session premarket
python3 -m invest brief --session postmarket
python3 -m invest site build --privacy public
```

Reports are written to `reports/YYYY-MM-DD-premarket.md` and
`reports/YYYY-MM-DD-postmarket.md`, with JSON sidecars beside them.

The public website is built into `web/`. In public mode, broker transactions,
accounts, quantities, costs, and dollar values are redacted. IBKR positions and
configured manual sleeves are published only as aggregate symbol and bucket
weights so recommended research moves can be framed around the Geoffrey Woo
Portfolio without exposing absolute size.

## Public Snapshot Model

This repository is designed to be safe as a public GitHub project. The source
code and sanitized static site snapshot are public; private broker records are
not.

Committed public artifacts:

- `web/index.html`, `web/styles.css`, and `web/app.js`
- `web/data/latest.json`
- `web/data/reports.json`

Excluded private artifacts:

- `.env`
- `config/invest.toml`
- `data/invest.db`
- `data/raw/`
- `reports/*.json`
- `reports/*.md`
- `.vercel/`

`npm run build` can run from a public clone because `invest.site` falls back to
the committed sanitized snapshot when private reports are absent. A private
operator can still regenerate the snapshot locally after importing broker data
and running a fresh brief.

Run tests with:

```bash
python3 -m unittest discover -s tests
node --check web/app.js
npm run build
```

## Configuration

Run `python3 -m invest init` to create `config/invest.toml` if it is missing.
The default config uses:

- IBKR Flex Web Service credentials from environment variables.
- SQLite database at `data/invest.db`.
- A 22-manager public-equity 13F universe including Situational Awareness LP /
  Leopold Aschenbrenner, Duquesne / Stanley Druckenmiller, Pershing Square /
  Bill Ackman, TCI / Chris Hohn, Durable / Henry Ellenbogen, Atreides / Gavin
  Baker, Berkshire Hathaway, Appaloosa / David Tepper, Baupost / Seth Klarman,
  Light Street / Glen Kacher, Valley Forge / Dev Kantesaria, Dorsey / Pat
  Dorsey, Akre / Chuck Akre, Altimeter, Coatue, Dragoneer, Greenoaks, Tiger
  Global, D1, Whale Rock, Viking, and Lone Pine.
- A focus-manager list for tracking percentages: Situational Awareness,
  Duquesne, Pershing Square, TCI, Durable, Atreides, Berkshire, Appaloosa,
  Baupost, Light Street, Valley Forge, Dorsey, Akre, Altimeter, Dragoneer,
  Coatue, Greenoaks, Tiger Global, D1, Whale Rock, Viking, and Lone Pine.
  The Tier 1 AI Thesis Core is Situational Awareness / Leopold, Altimeter, and
  Dragoneer; D1 is tracked in the broader manager context bench.

For IBKR, create Flex queries in Account Management, then add the values to
`.env`:

```bash
IBKR_FLEX_TOKEN=
IBKR_FLEX_ACTIVITY_QUERY_ID=
```

Then validate the connection without importing anything:

```bash
python3 -m invest ibkr status
python3 -m invest ibkr validate
```

If the validation summary looks right, import it:

```bash
python3 -m invest ibkr validate --import
```

For manual recovery or one-off backfills, import a downloaded Flex XML file:

```bash
python3 -m invest ibkr import-file /path/to/flex.xml
```

Manually maintained sleeves can be added under `[[portfolio.manual_positions]]`
in `config/invest.toml`. The report values those shares from latest quote data
and combines them with IBKR before calculating Geoffrey Woo Portfolio weights.
Cash reserves can be added under `[[portfolio.cash_reserves]]` with either a
private amount or a target `weight`. Public snapshots publish only the cash
weight, while portfolio comparisons, return proxies, manager overlap context,
and public equity weights are normalized ex-cash against the invested equity
sleeve. The sizing engine can draw from cash for high-conviction adds, bounded
by `max_cash_deploy_weight`.

## Commands

```bash
python3 -m invest sync --broker ibkr
python3 -m invest ibkr status
python3 -m invest ibkr validate --import
python3 -m invest filings --manager all --max-filings 2
python3 -m invest filings --manager situational-awareness --backfill
python3 -m invest brief --session premarket|postmarket
python3 -m invest site build --privacy public
python3 -m invest backtest run
python3 -m invest backtest-signal --signal ai-infra-momentum
```

## Daily Run Pipeline

The AlloIQ daily pipeline is intentionally read-only:

```bash
python3 -m invest filings --manager all --max-filings 2
python3 -m invest sync --broker ibkr
python3 -m invest brief --session premarket
python3 -m invest site build --privacy public
```

Use `postmarket` for the end-of-day report.

## Scheduled Live Updates

The GitHub Actions scheduler runs the private pipeline and commits only the
sanitized public data snapshot in `web/data/`:

```bash
python3 -m invest pipeline --kind premarket --privacy public
python3 -m invest pipeline --kind postmarket --privacy public
python3 -m invest pipeline --kind weekly --privacy public
```

Schedules are defined in `.github/workflows/scheduled-reports.yml`:

- Premarket: 8:00 AM ET on NYSE trading days.
- Post-close: 4:30 PM ET on NYSE trading days.
- Weekly idea research: Sunday morning ET.

The workflow uses duplicate UTC cron windows for daylight saving time and lets
the Python scheduler skip the non-matching duplicate. Manual runs can use
`workflow_dispatch` with `force=true`.

Required GitHub repository secrets:

- `ALLOIQ_CONFIG_TOML`: full private `config/invest.toml`.
- `IBKR_FLEX_TOKEN`
- `IBKR_FLEX_ACTIVITY_QUERY_ID`
- `DATABASE_URL`: Vercel-managed Neon Postgres connection string for private run history.

Optional data-source secrets:

- `ALPHA_VANTAGE_API_KEY`: enables Alpha Vantage's free `EARNINGS_CALENDAR`
  provider for 3/6/12 month expected earnings dates. Without it, AlloIQ still
  uses manual dates, company IR RSS/Atom feeds, Nasdaq's public earnings
  calendar fallback, SEC result markers, and news-derived catalyst detection.

Optional briefing delivery secrets:

- `ALLOIQ_TELEGRAM_BOT_TOKEN`: Telegram bot token from BotFather.
- `ALLOIQ_TELEGRAM_CHAT_ID`: destination chat id after the user sends the bot
  an initial message.

Telegram delivery runs after the scheduled report if both secrets are present:

```bash
python3 -m invest notify --session premarket --channel telegram --dry-run
python3 -m invest notify --session postmarket --channel telegram
python3 -m invest notify --session weekly --channel telegram
```

The message is generated from the latest private report JSON and includes only
weights, add/trim deltas, expected-return estimates, catalysts, constraints,
data health, and an AlloIQ link. It does not publish quantities, account values,
broker names, cost basis, raw account ids, or tokens.

The workflow never commits `.env`, `config/invest.toml`, `data/`, or `reports/`.
It runs tests, builds the public site, scans for private fields, and commits only
`web/data/latest.json` and `web/data/reports.json` when public output changes.

Private warehouse commands:

```bash
python3 -m invest warehouse health
python3 -m invest warehouse migrate
python3 -m invest decisions list --status open
python3 -m invest decisions record --ticket-id TICKET --decision approved --notes "Reviewed"
python3 -m invest tickets export --format markdown
```

The warehouse stores private pipeline runs, position snapshots, signal snapshots,
research snapshots, approval tickets, decision history, attribution, training
examples, backtest runs, backtest outcomes, and earnings markers. Public site
data remains sanitized weights-only research.

## Signal Stack

Daily reports synthesize five independent signal families:

- Macro regime: AI beta, risk appetite, rates, dollar, volatility, credit, energy.
- FRED macro stress: no-key FRED CSV series for yield-curve shape, high-yield
  spreads, liquidity pressure, and energy input costs.
- Portfolio context: IBKR symbol and bucket weights only in the public build.
- Manager signal: focus-manager 13F overlap, consensus, adds, reductions, and option tilt.
- Catalyst signal: classified news events such as capex signals, contract wins,
  financing risk, supply constraints, regulatory risk, and valuation resets.
- Price action: 5-day move used for entry discipline, not as proof of thesis.
- Earnings and filing window: manual earnings dates, company IR RSS/Atom feeds,
  Alpha Vantage/Nasdaq expected-date providers, SEC company filing markers, and
  news-driven guidance/revision signals.

Ideas are promoted when at least two independent families confirm, unless a risk
override forces a hedge or sizing review. This keeps the report from treating a
single headline, a stale 13F, or a price move as enough evidence by itself.

## Research Engine

AlloIQ now builds a deterministic, ML-ready research spine on every brief:

- `company_underwriting`: bottom-up ticker rows for thesis, core KPIs,
  bull/base/bear company setup, valuation support, revisions/guidance, margin
  trajectory, cash generation, balance-sheet/financing risk, customer
  concentration, catalyst clock, falsifiers, source quality, review status,
  and add/trim eligibility.
- `sector_underwriting`: AI-max sector rows for sector-specific KPIs, sector
  setup score, headwind/tailwind state, macro/power pressure, risk flags, and
  target-weight modifier.
- `feature_matrix`: versioned ticker rows for portfolio weight, peer weight,
  company underwriting, sector setup, Tier 1 manager ownership, manager flows,
  catalyst score, source quality, price-return windows, ETF macro regime, FRED
  credit/liquidity/yield-curve stress, valuation support, timing, evidence
  quality, and drawdown risk.
- `research_book`: bull/base/bear 12-month scenarios, probability-weighted
  return, risk-adjusted expected return, company reason, sector reason,
  tertiary 13F/macro summary, catalyst clock, verdict, risk, and falsifier.
- `portfolio_benchmark.sizing_plan`: model target weights, max allowed weights,
  cash/trim-funded add/trim deltas, funding source, active constraints, and
  sizing rationale.
- `recommendation_training_examples`: immutable recommendation examples with
  pending 5D/1M/3M/6M/12M forward-return labels for later model training and
  fast diagnostics.
- `backtest`: daily recommendation trials labelled against 5D/1M/3M/6M/12M
  forward price returns as those horizons mature, including hit rate,
  average decision return, confidence curves, and calibration buckets.
- `instrumentation_audit`: invariant checks that verify counts, symbol linkage,
  action math, target semantics, engine/ticket wiring, return windows, and
  backtest counts before the public snapshot is published.
- `outcome_diagnostics`: hit rate, forward return, signal-family diagnostics,
  and expected-vs-realized calibration once outcomes mature.

The first policy is deterministic and auditable. Later ML models should consume
the same feature and outcome rows, then write predictions back into the same
engine and sizing payload shape.

Decision priority is explicit: 60% company underwriting, 20% sector setup, 10%
manager/13F confirmation, and 10% macro/timing/risk. A strong 13F signal cannot
create an `Add` without bottom-up company evidence. Company deterioration can
force a trim even when delayed manager filings still look supportive.

Backtesting is self-contained:

```bash
python3 -m invest backtest run
python3 -m invest backtest run --format json --out reports/backtest-latest.json
```

Each saved report supplies immutable trials. Public price history supplies entry
and exit marks. Adds are scored by forward return; trims are scored by the
negative of forward return. The public `/backtest` page exposes aggregate
confidence only; private warehouse tables preserve the raw trial and outcome
ledger for later ML training.

Target fields are intentionally split:

- `target_weight`: immediate post-action weight for the approval ticket.
- `post_action_weight` / `trade_target_weight`: aliases for the same immediate
  post-action weight.
- `model_target_weight`: normalized full model portfolio target after hard
  constraints. Model targets do not exceed the current public-equity sleeve plus
  the capped cash draw; trims may intentionally leave residual cash.
- `cash_reserve_weight`: starting cash reserve weight.
- `cash_deployed_weight`: cash used by the current queue when adds exceed trims.
- `post_trade_cash_weight`: cash reserve weight after the current queue.

The daily instrumentation audit fails if those fields drift, if action turnover
exceeds configured limits, if adds are not funded by trims plus the capped cash
draw, if cash plus equity weights stop matching the total portfolio denominator,
if zero-delta actions still claim to add or trim, or if public counts no longer
match the generated rows.

Risk limits live under `[risk]` in `config/invest.toml`. They cap single-name
weight, bucket weight, daily turnover, one-ticket delta, cash deployment,
minimum signal-family count, earnings blackout windows, and no-add/watch-only
symbols. AlloIQ only creates approval tickets; it does not place broker orders.

The default news pull uses Google News RSS queries aimed at primary/company
events, AI capex, data-center financing, power/grid catalysts, macro conditions,
and focus-manager filings. The preferred upgrade path is structured ingestion
from SEC EDGAR, company IR feeds, transcript providers, Benzinga/Polygon/Finnhub
or Alpha Vantage news APIs, FRED, Treasury rates, and EIA energy data.

## Website

Run a local preview:

```bash
npm run build
npm run dev
```

Then open `http://localhost:4173`.

Deploying to Vercel:

```bash
npm run build
vercel deploy --prod
```

This repo includes `vercel.json` with `outputDirectory` set to `web`.

For `alloiq.com` on Namecheap, add the domain to the Vercel project, then point
Namecheap DNS to the records Vercel gives you. The common setup is:

- `A` record for `@` to Vercel's apex IP.
- `CNAME` record for `www` to Vercel's CNAME target.

Use the exact records shown in the Vercel domain screen if they differ.

## Notes

- 13F filings are delayed. The system labels them as public filing signals, not
  real-time manager trades.
- 13F values are normalized against the SEC primary document table total because
  electronic filings can differ in whether the information table value field is
  already dollar-denominated.
- News comes from linked RSS search results and is stored with source URLs.

## License

MIT. See `LICENSE`.
