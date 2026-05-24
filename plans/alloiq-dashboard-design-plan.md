# AlloIQ Dashboard Design Plan

## Goal

Make AlloIQ feel like an investor operating console, not a generic finance dashboard.

The core user is Geoffrey Woo or an investor with a similar AI-max worldview. They arrive with one question:

> What should I do with my portfolio today, and what evidence changed?

The public site must answer that using only redacted portfolio weights, delayed 13F data, price movement, macro regime, and linked news. It must not expose share counts, broker account details, market values, or private notes.

## Product Boundaries

- Public research only. No order execution.
- Portfolio context is weights only.
- 13F data is delayed and must be labeled as public filing signal, not live manager behavior.
- Recommended moves are portfolio-weight research proposals, not trading instructions.
- The app should optimize for repeat daily use, not marketing conversion.

## App Classifier

AlloIQ is an app UI, not a landing page.

Design implications:

- Dense but readable.
- Calm surface hierarchy.
- Clear action hierarchy.
- Minimal decoration.
- Cards only when the card is the unit of work.
- Utility copy, not aspirational copy.

## Primary User Jobs

1. See the Geoffrey Woo Portfolio current-weight price proxy and active proxy spread against relevant benchmarks.
2. See explicit portfolio-weight action proposals.
3. Understand why each action exists.
4. Compare portfolio exposure against Tier 1 and Tier 2 manager signals.
5. Check macro regime and catalyst tape before acting.
6. Drill into managers, consensus positions, and linked news when evidence needs auditing.

## Information Architecture

The first screen should prioritize action, then evidence, then context.

```text
Top Bar
  AlloIQ brand
  Report date
  Privacy badge

Left Rail
  Dashboard
  Moves
  Managers
  Macro
  News

Dashboard
  1. KPI strip
     - Geoffrey Woo Portfolio primary-horizon return
     - Vs Nasdaq 100
     - Vs Tier 1 peers
     - Action queue count
     - Macro regime
  2. Action Queue
     - Explicit add / trim / hold-hedge deltas
     - Current weight, post-action weight, target weight, hedge budget
     - Evidence and risk rationale
  3. Visualization Layer
     - Return curve across available horizons
     - Signal map: score vs manager consensus
     - Action sizing map: signed portfolio-weight deltas
  4. Return Benchmarks
     - Multi-horizon portfolio returns
     - Market and peer proxies
  5. Exposure Gaps
     - Portfolio underweights, risk reviews, concentration checks
  6. Study Queue
     - Research questions tied to position evolution
  7. Geoffrey Woo Portfolio
     - Bucket weights
     - Top symbol weights

Moves
  Public-signal research actions sorted by conviction.

Managers
  Tier 1 group first: Situational Awareness, Altimeter, Dragoneer, D1.
  Tier 2 group second: all other tracked managers.
  Consensus positions and manager filing status below.

Macro
  Regime scores and cross-asset market map.

News
  Linked catalyst and market sources.
```

If only three things can be shown above the fold, they are:

1. Portfolio performance versus Tier 1 peers.
2. Action Queue with explicit sizing.
3. Macro regime gate.

## Dashboard Screen Specification

### KPI Strip

The KPI strip orients the user in under 5 seconds.

- Primary portfolio return proxy: show the selected horizon label, return, priced coverage, and current-weight methodology.
- Vs Nasdaq 100: show active spread in percentage points.
- Vs Tier 1 peers: show active spread versus Tier 1 median 13F proxy.
- Action queue: show count and top action symbol.
- Macro regime: show current regime and AI/risk momentum detail.

### Action Queue

Action rows are the primary workspace.

Each action row must show:

- Symbol.
- `trade_action`: add, trim, hold, hold-hedge.
- Recommended delta as a large signed portfolio-weight number.
- Post-action weight.
- Target weight if different from post-action weight.
- Hedge budget if present.
- Reason in one sentence.
- Evidence tags: current weight, peer average, 5-day move, contribution, priority, signal count.

Copy rules:

- Use explicit verbs: Add, Trim, Hold, Hedge.
- Use percentages, not dollar values.
- Do not say "consider" as the primary action.
- Keep legal/research language in the notice and sizing basis, not inside every row.

### Return Benchmarks

Benchmarks explain whether performance is skill, beta, or crowding.

Required rows:

- S&P 500.
- Nasdaq 100.
- Semiconductors.
- Software.
- AI beta basket.
- Focus-manager median proxy.
- Tier 1 median proxy.
- Tier 2 median proxy.
- Focus-manager best proxy.

Each benchmark row shows benchmark return and portfolio active spread.

### Visualization Layer

The dashboard should include more visual evidence, but only where the chart accelerates a decision.

Required visualizations:

- Return curve: plot Geoffrey Woo Portfolio current-weight price proxy across available horizons.
- Signal map: plot decision cards by signal score and consensus manager count.
- Action sizing map: show signed add/trim/hold-hedge portfolio-weight deltas as diverging bars.
- Portfolio bars: keep bucket and top-symbol weight bars.
- Macro heat map: keep cross-asset 5-day movement tiles.

Visualization rules:

- Every chart must have visible labels and numeric values.
- No decorative charts.
- No pie charts.
- No chart should hide the explicit Action Queue. Charts support the decision, they do not replace it.
- On mobile, charts stack below the Action Queue and remain readable at 320px.

### Exposure Gaps

Exposure gaps explain why the action queue exists.

Each row shows:

- Symbol and bucket.
- Gap type: risk review, white space, underweight vs focus, concentration check.
- Current portfolio weight.
- Peer average weight.
- Score.
- Signal count.
- One reason.

### Study Queue

Study Queue is where non-actionable but important questions go.

Examples:

- "Is the drawdown thesis violation, macro beta, or better entry?"
- "Did the gain improve forward expected return, or reduce margin of safety?"

Study Queue must not compete visually with the Action Queue.

## Manager Screen Specification

Tier 1 gets its own visual group above Tier 2.

Tier 1:

- Situational Awareness / Leopold Aschenbrenner.
- Altimeter.
- Dragoneer.
- D1 Capital.

Tier 2:

- Duquesne / Druckenmiller.
- Pershing Square / Ackman.
- TCI.
- Durable.
- Atreides.
- Berkshire.
- Appaloosa.
- Baupost.
- Light Street.
- Valley Forge.
- Dorsey.
- Akre.
- Coatue.
- Greenoaks.
- Tiger Global.
- Whale Rock.
- Viking.
- Lone Pine.

Each manager card shows:

- Manager name.
- Filing status date.
- Tier badge.
- Coverage percent.
- Watchlist overlap.
- Geoffrey Woo Portfolio overlap.
- Top-10 concentration.
- Top positions by fund weight with Geoffrey Woo Portfolio overlap shown only as a weight.

Long manager names must wrap cleanly. Long issuer names in top positions may ellipsize.

## Interaction State Coverage

| Feature | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| Global report payload | Show "Loading report" in data regions, keep layout stable | Show "No report available yet" with command hint `python3 -m invest brief --session postmarket` | Show "Report failed to load" with retry affordance | Populate all views | If sections missing, show available sections and label missing data |
| KPI strip | Skeleton KPI boxes with labels preserved | Show unavailable state per KPI | Show error text inside affected KPI only | Show return and active metrics | Show `n/a` with priced coverage if partial |
| Action Queue | Reserve row height, show loading rows | Explain no actions: "No portfolio-weight changes triggered today" | Show "Action generation unavailable" | Show explicit add/trim/hold rows | Show actions with `n/a` fields where price data is missing |
| Return Benchmarks | Skeleton benchmark rows | "No benchmark data for selected horizon" | "Benchmark fetch failed" | Show benchmark returns and active spreads | Show coverage and omit unavailable rows |
| Exposure Gaps | Skeleton rows | "No exposure gaps found" | "Exposure comparison unavailable" | Show sorted gaps | Show available peer averages, mark missing peer data |
| Study Queue | Skeleton rows | "No study items today" | "Study queue unavailable" | Show questions | Show only symbols with enough data |
| Portfolio Context | Skeleton bars | "No Geoffrey Woo Portfolio weights available" | "Portfolio weights unavailable" | Show bucket and symbol weights | Show weights for priced symbols only |
| Manager Groups | Skeleton manager cards | "No focus manager tracking available" | "Manager filings unavailable" | Show Tier 1 and Tier 2 groups | Show missing filing cards with clear status |
| Macro | Skeleton scores/map | "No macro tape available" | "Macro data unavailable" | Show regime and market map | Show subset of available macro symbols |
| News | Skeleton source rows | "No linked catalyst news found" | "News feed unavailable" | Show linked sources | Show only available linked items |
| Search | Input remains enabled | "No matching results" with clear query echo | Search never hard-errors | Filter visible rows | Keep section headings visible when results are partial |

## User Journey Storyboard

| Step | User Does | User Feels | Plan Support |
|---|---|---|---|
| 1 | Opens AlloIQ in the morning | Wants signal, not noise | KPI strip and Action Queue appear first |
| 2 | Reads top action row | Needs specificity | Row shows add/trim/hold percentage and post-action weight |
| 3 | Checks why | Needs trust | Evidence tags and reason sit inside the same row |
| 4 | Compares against Tier 1 | Wants to know if elite peers agree | Tier 1 median proxy appears in KPI/benchmarks |
| 5 | Audits manager evidence | Skeptical | Manager screen separates Tier 1 and Tier 2 |
| 6 | Checks macro/news | Wants guardrails | Macro and News tabs provide regime/catalyst context |
| 7 | Returns tomorrow | Wants continuity | Same structure, fresh date, stable ranking |

Time horizon:

- First 5 seconds: user sees portfolio return, Tier 1 comparison, and explicit action sizing.
- First 5 minutes: user can audit evidence behind the top action.
- Five-year relationship: user trusts the system because it is consistent, redacted, and explicit about delayed data.

## Visual System

Current implementation uses:

- Paper background: `#f7f8f4`.
- Surface: `#ffffff`.
- Ink: `#101820`.
- Muted: `#5b6673`.
- Accent blue: `#2458a6`.
- Green/red/amber/plum/teal for semantic buckets.
- 6-8px radius.
- Thin borders.
- Light shadow only on panels.

Design requirements:

- Keep a quiet operating-console feel.
- Avoid purple-blue gradient SaaS styling.
- Avoid icon-in-circle feature grids.
- Avoid decorative blobs, oversized hero sections, and marketing copy.
- Use consistent radius: 6px controls, 8px panels/cards.
- Use one primary accent per state.
- Preserve dense layout, but never let text overlap or clip except intentional top-position ellipsis.

Font note:

The current CSS uses `Inter, ui-sans-serif, system-ui`. This is serviceable but generic. A 10/10 design system should choose a deliberate type pairing or commit to a documented utility font choice.

## Responsive Specification

Desktop, 1080px and up:

- Left rail remains sticky.
- Dashboard uses two-column benchmark/action grid.
- Action Queue gets enough width for delta and post-action metrics.
- Manager Tier 1 and Tier 2 groups use responsive inner grids.

Tablet, 760px to 1080px:

- Rail becomes horizontal.
- Dashboard stacks to one column.
- Action Queue remains above Exposure Gaps and Study Queue.
- Portfolio context remains two columns if width allows.

Mobile, 320px to 760px:

- Rail is horizontal scroll.
- Topbar metadata stacks.
- KPI, dashboard panels, manager groups, portfolio context all single-column.
- Action row sizing block moves below symbol and aligns left.
- Minimum touch target is 44px for buttons and search input.
- No viewport-width font scaling.

## Accessibility Specification

- Maintain semantic landmarks: header, nav, main.
- Rail buttons must expose active state visually and through `aria-current` or equivalent.
- Search input must keep an accessible label.
- Color contrast must meet WCAG AA for all body text and controls.
- Action deltas cannot rely only on color. Signed value must include `+` or `-`.
- Keyboard users must be able to tab through rail, search, and linked news.
- Focus states must be visible on buttons, links, and input.
- Touch targets should be at least 44px on mobile.
- Live data refresh should not steal focus.

## AI Slop Rejection Rules

Reject any implementation that adds:

- Generic SaaS hero above the working dashboard.
- Decorative cards that do not carry an action or evidence unit.
- Three-column feature grid.
- Purple/indigo gradients.
- Centered marketing copy as the main information structure.
- Icons in colored circles as section decoration.
- Emoji as UI markers.
- Repeated sections with the same emotional tone.

## Not In Scope

- Broker order entry: AlloIQ remains read-only.
- Absolute dollar exposure on the public site: privacy boundary.
- Personal notes or private memo publishing: avoid accidental leakage.
- Full mobile-native app shell: current target is responsive web app.
- Real-time hedge-fund tracking: 13F data is delayed.
- Authenticated private dashboard: future product decision.

## What Already Exists

- Static app shell in `web/index.html`.
- App state and rendering in `web/app.js`.
- Visual system tokens and responsive layout in `web/styles.css`.
- Public data redaction in `invest/site.py`.
- Portfolio benchmark and action sizing data in `invest/reports.py`.
- Tier 1/Tier 2 manager grouping in `invest/managers.py` and `config/invest.toml`.

Reuse these patterns:

- Left rail view navigation.
- Panel layout with 8px cards.
- Weight bars for portfolio context.
- Tags for compact evidence metadata.
- Manager group sections for Tier 1 and Tier 2.

## Unresolved Design Decisions

The design review resolved the obvious choices:

1. Do not add a marketing-style Top Move hero above the dashboard. The app should remain KPI-first with Action Queue as the primary workspace.
2. Add methodology as compact disclosure content, not persistent explanatory copy. The app should stay scannable.
3. Show immediate action and staged target path where they differ. This is already reflected by delta, post-action weight, target weight, and hedge budget.
4. Tier 1 gets stronger treatment through order, grouping, copy, and benchmark prominence. Avoid heavy visual decoration that makes Tier 2 feel irrelevant.
5. Keep current typography in the implementation for now, but use `DESIGN.md` as the source of truth before larger visual redesigns.

Still unresolved:

1. Exact methodology disclosure placement: inline per section, footer drawer, or dedicated methodology page.

## TODO Candidates

- Add `aria-current` to the active rail button.
- Add explicit empty/error states for report payload failures.
- Add a compact methodology disclosure for 13F proxy returns and action sizing.

## Plan Design Review

System audit:

- Branch: `unknown`.
- Base branch fallback: `main`.
- Plan file: `plans/alloiq-dashboard-design-plan.md`.
- `DESIGN.md`: created.
- `CLAUDE.md`: created.
- `TODOS.md`: missing.
- Visual mockups: not generated because the gstack designer binary is not available.
- Browser tooling: available, but this was a plan review, not a live-site visual QA.

Initial rating:

- Before this plan existed: 3/10. The repo had a working UI, but no design plan, no state coverage, no written hierarchy, and no formal design system.
- After plan creation and review fixes: 8/10. The plan now defines hierarchy, screen specs, interaction states, journey, responsive behavior, accessibility, AI-slop rejection rules, and explicit non-scope.

What would make it 10/10:

- Generate visual mockups once the gstack designer is available.
- Decide methodology disclosure placement.
- Add implementation acceptance checks for keyboard state, empty/error states, and mobile action-row behavior.

### Pass Ratings

| Pass | Before | After | Notes |
|---|---:|---:|---|
| Information Architecture | 4/10 | 9/10 | Added first-screen hierarchy, view map, and above-fold priority. |
| Interaction State Coverage | 2/10 | 8/10 | Added loading, empty, error, success, and partial-state table. |
| User Journey | 3/10 | 8/10 | Added storyboard and 5-second / 5-minute / 5-year design horizon. |
| AI Slop Risk | 5/10 | 9/10 | Classified as app UI and added explicit rejection rules. |
| Design System Alignment | 3/10 | 8/10 | Captured existing tokens and created `DESIGN.md`; implementation still uses legacy Inter. |
| Responsive & Accessibility | 4/10 | 8/10 | Added viewport and a11y specs, still needs implementation checks. |
| Unresolved Decisions | 3/10 | 8/10 | Resolved obvious choices, left methodology placement and formal design-system timing. |

### Design Review Decisions

1. The Dashboard remains an app workspace, not a marketing page.
2. The Action Queue is the dominant work surface.
3. Recommended moves must show explicit portfolio-weight deltas, post-action weights, target weights, and hedge budgets.
4. Tier 1 manager signal gets benchmark and grouping priority, not decorative emphasis.
5. Methodology should be compact and available on demand.
6. Public privacy boundaries remain visible through copy and data redaction, not by hiding useful weights.

### NOT In Scope

- A public marketing landing page.
- Broker order entry.
- Absolute public portfolio values.
- Authenticated private app.
- Replacing the current UI with a full design-system rewrite.
- Live manager trading inference from delayed 13F data.

### What Already Exists

- Static app shell in `web/index.html`.
- Rendering logic in `web/app.js`.
- Visual tokens and responsive layout in `web/styles.css`.
- Public redaction in `invest/site.py`.
- Portfolio benchmark and action sizing data in `invest/reports.py`.
- Tier grouping in `invest/managers.py` and `config/invest.toml`.

### Approved Mockups

No mockups were generated. The gstack designer binary was unavailable in this workspace.

## DESIGN PLAN REVIEW - COMPLETION SUMMARY

```text
+====================================================================+
|         DESIGN PLAN REVIEW - COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | DESIGN.md created, UI scope exists          |
| Step 0               | Initial 3/10, focus areas defined           |
| Pass 1  (Info Arch)  | 4/10 -> 9/10 after fixes                   |
| Pass 2  (States)     | 2/10 -> 8/10 after fixes                   |
| Pass 3  (Journey)    | 3/10 -> 8/10 after fixes                   |
| Pass 4  (AI Slop)    | 5/10 -> 9/10 after fixes                   |
| Pass 5  (Design Sys) | 3/10 -> 7/10 after fixes                   |
| Pass 6  (Responsive) | 4/10 -> 8/10 after fixes                   |
| Pass 7  (Decisions)  | 6 resolved, 2 deferred                     |
+--------------------------------------------------------------------+
| NOT in scope         | written (6 items)                          |
| What already exists  | written                                    |
| TODOS.md updates     | 4 candidates listed, no TODOS.md created   |
| Approved Mockups     | 0 generated, 0 approved                    |
| Decisions made       | 6 added to plan                            |
| Decisions deferred   | 2 listed                                   |
| Overall design score | 3/10 -> 8/10                               |
+====================================================================+
```

Status: DONE_WITH_CONCERNS.

Concerns:

- No visual mockups, because the designer binary is unavailable.
- This repo is not a git repo, so branch and commit tracking are degraded.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | - |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | - |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | - | - |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | issues_open | score: 3/10 -> 8/10, 6 decisions |

**UNRESOLVED:** 1 design decision remains: methodology disclosure placement.

**VERDICT:** DESIGN REVIEW COMPLETED WITH CONCERNS, eng review still required before implementation.
