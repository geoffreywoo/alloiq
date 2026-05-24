# Design System - AlloIQ

## Product Context

- **What this is:** AlloIQ is a read-only public markets intelligence app for AI-forward investing. It tracks Geoffrey Woo Portfolio weights, hedge fund 13F signals, daily catalysts, macro regime, and explicit portfolio-weight research moves.
- **Who it is for:** Geoffrey Woo and investors with similar public-equity, AI-max, researcher-heavy workflows.
- **Space:** Public markets intelligence, portfolio operating console, hedge fund signal tracker.
- **Project type:** Data-dense web app and public dashboard. It is not a marketing site.
- **Primary question:** What should I do with my portfolio today, and what evidence changed?

## Design Promise

AlloIQ should feel like an investor operating console.

Not a generic finance dashboard. Not a SaaS landing page. Not a vibe board for "AI investing."

The product earns trust by being specific:

- It shows portfolio weights, not private dollar amounts.
- It makes recommendations as explicit percentage deltas.
- It separates AI Thesis Core manager signal from Manager Context Bench comparison signal.
- It labels 13F data as delayed public filings.
- It keeps the user close to evidence.

## Aesthetic Direction

- **Direction:** Investor OS.
- **Decoration level:** Intentional, restrained.
- **Mood:** Calm, sharp, evidence-first. The app should feel like an operating console for one AI public-equity workflow, with the confidence of a portfolio analytics tool and the source discipline of a research product.
- **Design posture:** Dense but readable. Quiet until a decision needs attention.

Safe choices:

- Left rail navigation for repeated daily use.
- Neutral paper and white surfaces for long reading sessions.
- Semantic color only where it carries meaning.
- Compact rows, tags, and evidence visualizations instead of ornamental graphics.

Risks worth taking:

- The Action Queue is allowed to feel more like an order blotter than a report card.
- AI Thesis Core manager signal gets product prominence, because that is the user's edge.
- Copy can be direct and investor-native. Avoid overexplaining every market term.

## Information Hierarchy

The first screen must answer in this order:

1. How is the Geoffrey Woo Portfolio doing?
2. What action is recommended?
3. What changed in the evidence?
4. How does this compare to AI Thesis Core and Manager Context Bench managers?
5. What macro or news guardrails matter?

Dashboard hierarchy:

```text
Topbar
  Brand
  Report date
  Privacy badge

Rail
  Dashboard
  Moves
  Managers
  Macro
  News

Dashboard
  KPI strip
  Action Queue
  Return Benchmarks
  Exposure Gaps
  Study Queue
  Geoffrey Woo Portfolio context
```

If only three things fit above the fold:

1. Portfolio current-weight price proxy versus AI Thesis Core peer proxies.
2. Action Queue with explicit sizing.
3. Macro regime gate.

## Typography

### Target System

- **Display:** Geist Sans, semibold to bold. Used for page title and major numbers.
- **Body:** Geist Sans. Used for app copy, rows, labels, nav, buttons.
- **Data:** Geist Sans with `font-variant-numeric: tabular-nums`. Use for percentages, returns, weights, and scores.
- **Code / command text:** Geist Mono.
- **Fallback:** `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.

### Current Implementation Note

The public app loads Geist and Geist Mono from Google Fonts. Keep them as the default until there is a reason to self-host.

### Type Scale

Use fixed sizes. Do not scale font size with viewport width except the top page title already constrained by `clamp`.

| Token | Size | Use |
|---|---:|---|
| `text-xs` | 11px | Dense metric labels, uppercase captions |
| `text-sm` | 12px | Tags, metadata, compact status text |
| `text-md` | 14px | Row body, secondary copy |
| `text-base` | 16px | Base body text |
| `text-lg` | 18px | Strong row values |
| `text-xl` | 22px | Section headers when needed |
| `text-2xl` | 28px | Primary app heading |
| `text-3xl` | 40px | Large dashboard title only |

Rules:

- Letter spacing is `0` by default.
- Uppercase labels may use normal letter spacing, not wide tracking.
- Numbers in KPI, action deltas, and benchmark rows should use tabular numerals.
- Long names wrap unless they are issuer names inside top-position mini rows, where ellipsis is allowed.

## Color

### Approach

Restrained. One primary accent, semantic colors, warm paper, no decorative gradients.

### Core Tokens

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#0b1117` | Primary text, brand mark, high-confidence values |
| `--muted` | `#586674` | Secondary copy, metadata, labels |
| `--paper` | `#f4f6f1` | App background |
| `--surface` | `#ffffff` | Panels, controls, primary cards |
| `--surface-alt` | `#f9faf5` | Rows, nested cards, lower-emphasis surfaces |
| `--line` | `#d4dbd0` | Borders and dividers |
| `--track` | `#e5ebe1` | Bar tracks and inactive fills |
| `--blue` | `#1d5f9f` | Primary accent, active nav, links |
| `--green` | `#14765a` | Positive values, constructive signals |
| `--red` | `#ad3f45` | Negative values, risk states |
| `--amber` | `#b16a19` | Warning, energy/power bucket |
| `--plum` | `#594879` | Software / secondary category color |
| `--teal` | `#1a7178` | Macro / infrastructure accent |
| `--terminal` | `#101820` | Brand mark and high-priority decision cards |
| `--electric` | `#d9f24f` | Thin proof-of-life accent only, not a dominant color |

### Semantic Usage

- Positive return or add signal: green.
- Negative return or trim signal: red.
- Warning, financing, crowding, or uncertainty: amber.
- Active navigation, links, selected states: blue.
- Bucket colors are allowed, but only to support scanning. Do not make the whole page a rainbow.

### Forbidden Color Moves

- Purple-blue gradients.
- Neon AI palettes.
- Decorative bokeh, blobs, or orbs.
- Red/green as the only cue for action. Signed text must carry the meaning.

## Spacing

- **Base unit:** 4px.
- **Density:** Compact but breathable.
- **Default section gap:** 16px.
- **Panel padding:** 16px desktop, 12px mobile.
- **Row padding:** 12px.
- **Tag gap:** 6px.
- **Grid gap:** 10px to 16px depending on density.

Scale:

| Token | Value | Use |
|---|---:|---|
| `space-2xs` | 2px | Micro alignment |
| `space-xs` | 4px | Tight label/value gaps |
| `space-sm` | 8px | Controls, internal row gaps |
| `space-md` | 12px | Row padding |
| `space-lg` | 16px | Panels and grid gaps |
| `space-xl` | 24px | Page section separation |
| `space-2xl` | 36px | Page padding upper bound |

## Layout

- **Approach:** Grid-disciplined app UI.
- **Desktop:** Sticky topbar, sticky left rail, content grid.
- **Tablet:** Rail becomes horizontal, dashboard stacks to one column.
- **Mobile:** One-column app, horizontal rail, left-aligned action sizing.
- **Minimum width:** 320px.
- **Content width:** No fixed max width. This is an app workspace, not an article page.

Breakpoints:

| Breakpoint | Behavior |
|---|---|
| `>1080px` | Left rail, two-column dashboard, sticky navigation |
| `760px-1080px` | Horizontal rail, one-column dashboard, preserve dense rows |
| `<760px` | Single column, action sizing aligns left, all cards full width |

Rules:

- Page sections are not floating cards.
- Cards are for repeated units, rows, modals, and framed tools.
- Do not nest cards inside cards unless the inner card is a data row.
- All fixed-format UI needs stable dimensions. No hover or dynamic content should shift layout.

## Radius, Borders, Elevation

| Token | Value | Use |
|---|---:|---|
| `radius-sm` | 4px | Tiny indicators, inner controls |
| `radius-md` | 6px | Buttons, search input, brand mark, charts |
| `radius-lg` | 8px | Panels, rows, cards |
| `radius-full` | 999px | Tags and badges only |

Borders:

- Use `1px solid var(--line)` as the default.
- Active nav may use `rgba(36, 88, 166, 0.25)`.
- Empty states use dashed borders.

Elevation:

- Use one shadow token only: `0 18px 50px rgba(16, 24, 32, 0.08)`.
- Do not add stacked shadows, glow, or glassmorphism.

## Components

### Topbar

Purpose: identity, report recency, privacy status.

Rules:

- Keep brand compact.
- Report date and privacy badge stay visible.
- Do not add marketing nav.

### Rail

Purpose: view switching.

Rules:

- Active state must be obvious.
- Active button should expose `aria-current` or equivalent.
- On tablet/mobile, rail becomes horizontal scroll.
- Buttons must have at least 40px height now, target 44px for mobile.

### KPI Cards

Purpose: fast orientation.

Rules:

- Keep labels short.
- Use large number first, detail second.
- Avoid more than 5 KPI cards on Dashboard.
- Do not turn KPI cards into explanatory paragraphs.

### Action Queue Rows

Purpose: answer "what should I do?"

Each row must show:

- Symbol.
- Large signed recommended delta.
- Post-action weight.
- Target weight if different.
- Hedge budget if present.
- `trade_action`: add, trim, hold, hold-hedge.
- One reason.
- Evidence tags.

Rules:

- The action delta is the loudest element in the row.
- Use signed percentages. `+3.0%` is better than "increase exposure."
- Do not show dollar values.
- "Consider" is banned as the primary verb.
- Research disclaimer belongs in surrounding copy, not in every row.

### Benchmark Rows

Purpose: compare portfolio against market, AI beta, and peer proxies.

Rules:

- Show benchmark return and active spread.
- AI Thesis Core median proxy must be easy to find.
- 13F proxy rows should remain labeled as proxy data.
- Portfolio return rows must be labeled as current-weight price proxies until daily account equity and cash-flow history are imported.

### Manager Cards

Purpose: explain focus-manager signal quality.

Rules:

- AI Thesis Core appears before Manager Context Bench.
- AI Thesis Core contains Situational Awareness, Altimeter, Dragoneer.
- Manager Context Bench contains all other tracked managers.
- Manager cards show coverage, watchlist overlap, portfolio overlap, top-10 concentration.
- Top positions show fund weight and Geoffrey Woo Portfolio weight only.
- Long manager names wrap. Long issuer names may ellipsize.

### Tags

Purpose: compact evidence metadata.

Rules:

- Tags are secondary. They should never overpower row action.
- Tags use `radius-full`.
- Tags should not become a substitute for hierarchy.

### Empty States

Empty states are product states, not placeholders.

Rules:

- Say what is missing.
- Say why it matters.
- Give the next useful action when possible.
- Avoid "No items found" by itself.

Example:

> No portfolio-weight changes triggered today. Re-run the postmarket brief after filings, prices, and news update.

## Data Visualization

AlloIQ should use more visual evidence than a normal dashboard, but every chart must answer a portfolio question.

Required visualization set:

- Today's Decision Stack: four compact tiles for top action, active spread, macro gate, and evidence score.
- Return curve: current-weight portfolio price proxy across 5D, 1M, 3M, YTD, and 1Y when data exists.
- Action sizing map: signed add/trim/hold-hedge portfolio-weight deltas.
- Signal map: signal score versus consensus manager count.
- Attribution waterfall: top 5-day contribution drivers, split into lift and drag.
- Peer gap chart: current portfolio weight versus peer average and target/post-action weight.
- Portfolio bars: bucket and symbol weights.
- Manager overlap bars or cards: coverage, watchlist, portfolio overlap, top-10 concentration.
- Macro heat map: cross-asset 5-day moves.
- Future candidate: catalyst timeline by symbol.

Visual grammar:

- Horizontal bars for weights.
- Diverging bars for add versus trim deltas.
- Diverging bars for attribution lift versus drag.
- Scatter plots for signal strength versus manager consensus.
- Line charts for horizon return curves.
- Tiles for macro market map.
- Signed numbers for deltas.
- Tags for evidence families.

Rules:

- Do not use decorative charts.
- Do not use pie charts for portfolio exposure.
- Do not animate charts unless motion improves comprehension.
- Every chart needs visible labels and numeric values.
- Every visualization must map back to an action, benchmark, exposure, manager signal, or catalyst.
- If a visualization cannot explain a decision faster than a row of text, cut it.

## Motion

- **Approach:** Minimal-functional.
- **Duration:** 100ms to 180ms for hover/focus, 150ms to 250ms for view transitions.
- **Easing:** `ease-out` for entry, `ease-in` for exit, `ease-in-out` for movement.

Allowed:

- Hover/focus transitions on rail buttons and links.
- Subtle row state changes.
- View transition fade if it does not delay reading.

Forbidden:

- Decorative scroll animation.
- Loading animations that shift layout.
- Motion that distracts from data.

## Responsive Rules

Desktop:

- Left rail remains sticky.
- Dashboard can use two columns.
- Action Queue has enough width for delta and post-action metrics.
- Manager groups use responsive grids.

Tablet:

- Rail becomes horizontal.
- Dashboard stacks.
- Action Queue remains above Exposure Gaps and Study Queue.

Mobile:

- Rail is horizontal scroll.
- Topbar metadata stacks.
- All dashboard panels are one column.
- Action size block aligns left below symbol.
- Touch targets should be at least 44px.
- No text should overflow its parent except intentional issuer ellipsis.

## Accessibility

Minimum requirements:

- Semantic landmarks: `header`, `nav`, `main`.
- Rail buttons have visible focus and active state.
- Active rail button should expose `aria-current` or equivalent.
- Search input has an accessible label.
- Links have visible focus and hover states.
- Color contrast meets WCAG AA.
- Signed values include `+` or `-`, not color only.
- Touch targets target 44px on mobile.
- Data refresh must not steal focus.

Keyboard path:

1. Brand link.
2. Rail buttons.
3. Search input.
4. Visible row links and controls.

## Privacy and Trust Rules

Public site may show:

- Symbol weights.
- Bucket weights.
- Manager disclosed fund weights.
- Portfolio overlap by percentage.
- Recommended portfolio-weight deltas.

Public site must not show:

- Share counts.
- Dollar values.
- Broker account names.
- IBKR or Vanguard account identifiers.
- Transaction details.
- Raw private portfolio rows.

Trust copy:

- Keep the top notice short.
- Label recommendations as research proposals.
- Label 13F data as delayed public filings.
- Make evidence inspectable through manager and news views.

## Copy Rules

Voice:

- Direct.
- Specific.
- Investor-native.
- Calm.

Use:

- "Add +1.0% starter."
- "Trim 2.3% to 9.2%."
- "Hold at 7.9%; hedge budget 1.0%."
- "AI Thesis Core median proxy."
- "Priced coverage."

Avoid:

- "Unlock."
- "Revolutionize."
- "All-in-one."
- "AI-powered insights."
- "Consider taking action."
- "No items found."

## AI Slop Rejection Rules

Reject any UI change that adds:

- Marketing hero above the working dashboard.
- Purple or indigo gradient backgrounds.
- Three-column feature grid.
- Icons in colored circles as decoration.
- Centered marketing copy as the primary structure.
- Decorative blobs, orbs, wavy dividers, or bokeh.
- Generic stock imagery.
- Emoji as section markers.
- Cards that do not carry an action, evidence unit, or repeated item.
- Multiple sections repeating the same mood statement.

Litmus checks:

1. Can the user answer "what changed and what should I do?" in 5 seconds?
2. Is the Action Queue visibly more important than secondary evidence?
3. Is AI Thesis Core manager signal easy to find?
4. Are public privacy boundaries preserved?
5. Would the UI still feel premium if every decorative shadow were removed?

## Implementation Checklist

Before shipping visual UI changes:

- Read this file.
- Confirm the UI remains app-first, not landing-page-first.
- Check desktop, tablet, and mobile.
- Check text wrapping for long manager names and long issuer names.
- Check keyboard focus.
- Check public JSON and UI for private data leakage.
- Check Action Queue rows still show explicit deltas.
- Run local browser QA for overflow and visible states.

Suggested browser QA targets:

- Dashboard at 1280px.
- Dashboard at 390px.
- Managers view at 1280px.
- Managers view at 390px.
- Search with zero results.
- Missing or empty data state when available.

## Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-24 | AlloIQ is an app UI, not a landing page | Users come for daily decisions and evidence, not conversion copy |
| 2026-05-24 | Industrial / utilitarian aesthetic | The product needs trust, density, and speed |
| 2026-05-24 | Action Queue is the primary workspace | The highest-value output is explicit portfolio-weight sizing |
| 2026-05-24 | AI Thesis Core manager signal receives first-class treatment | The product thesis depends on elite AI/growth investor comparison |
| 2026-05-24 | Public site stays weights-only | Privacy boundary is part of the product trust model |
| 2026-05-24 | Target typography moves from Inter to Geist Sans | Inter works, but it is too generic for the long-term product identity |
