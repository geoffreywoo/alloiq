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
  Coatue, and Greenoaks.

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

## Commands

```bash
python3 -m invest sync --broker ibkr
python3 -m invest ibkr status
python3 -m invest ibkr validate --import
python3 -m invest filings --manager all --max-filings 2
python3 -m invest filings --manager situational-awareness --backfill
python3 -m invest brief --session premarket|postmarket
python3 -m invest site build --privacy public
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

## Signal Stack

Daily reports synthesize five independent signal families:

- Macro regime: AI beta, risk appetite, rates, dollar, volatility, credit, energy.
- Portfolio context: IBKR symbol and bucket weights only in the public build.
- Manager signal: focus-manager 13F overlap, consensus, adds, reductions, and option tilt.
- Catalyst signal: classified news events such as capex signals, contract wins,
  financing risk, supply constraints, regulatory risk, and valuation resets.
- Price action: 5-day move used for entry discipline, not as proof of thesis.

Ideas are promoted when at least two independent families confirm, unless a risk
override forces a hedge or sizing review. This keeps the report from treating a
single headline, a stale 13F, or a price move as enough evidence by itself.

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
