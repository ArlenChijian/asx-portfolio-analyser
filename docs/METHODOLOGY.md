# Methodology

This document explains every meaningful design decision in the ASX Portfolio Analyser. It's intended for two audiences: (1) people technically auditing the project and asking "why is this built this way?", and (2) future-me, returning to the codebase six months from now and needing to remember what I was thinking.

## Contents

1. [Project intent and scope](#1-project-intent-and-scope)
2. [Regulatory framing (AFSL safety)](#2-regulatory-framing-afsl-safety)
3. [Data pipeline](#3-data-pipeline)
4. [Analytics layer](#4-analytics-layer)
5. [Portfolio construction](#5-portfolio-construction)
6. [Projections](#6-projections)
7. [AI integration](#7-ai-integration)
8. [Web layer](#8-web-layer)
9. [Limitations and known issues](#9-limitations-and-known-issues)
10. [Future work](#10-future-work)

---

## 1. Project intent and scope

The goal is a **portfolio piece**, not a SaaS product. Three audiences in priority order:

1. **Recruiters and hiring managers** assessing data-analyst capability. They look for: clean code, well-structured data pipelines, defensible methodology, evidence of judgement, willingness to engage with ambiguity (especially regulatory).
2. **The author**, as a working tool for personal portfolio screening (within the legal framing of "education, not advice").
3. **Other developers** who might want to use the project as a reference implementation for the broader pattern of *deterministic core + AI augmentations*.

Explicit non-goals:

- This is **not** an alpha-generation strategy. The portfolio engine is a screening tool, not a trading system.
- This is **not** a personalised financial advice product. See §2.
- This is **not** trying to be cheaper or faster than commercial portfolio tools. It's trying to be more *transparent*.

---

## 2. Regulatory framing (AFSL safety)

In Australia, providing personal financial product advice requires an Australian Financial Services Licence (AFSL) under the *Corporations Act 2001*. The penalties are substantial; the regulator (ASIC) is active. This applies even to free, AI-generated, or "experimental" tools.

The project is **deliberately structured to avoid providing personal advice**:

- All outputs are framed as "candidate portfolios" or "screen results", never "recommendations".
- The AI-generated explanations are prompted to use language like *"the screen produced these holdings because…"* rather than *"you should buy…"*.
- A prominent disclaimer appears on the home page, in the page footer, in the API metadata description, and in every AI-generated explanation.
- The methodology is fully disclosed (this document); a user can audit every step.
- The tool does not collect or store any personal information about the user (no accounts, no CRM).
- Links to ASIC's MoneySmart appear in multiple places.

A more cautious framing would skip the personalised inputs entirely (purely educational with no user-specific calibration), but that would make the project pedagogically uninteresting. The current framing is the line between "useful demonstration" and "regulated product." The author is open to revising if regulatory guidance changes.

---

## 3. Data pipeline

### 3.1 Universe

The investment universe is the union of:

- **Current S&P/ASX 300 constituents** (~300 stocks). Scraped from Wikipedia on each pipeline run rather than hard-coded, so the project stays current as constituents change quarterly. Falls back to ASX 200 if the 300 page is unavailable.
- **A curated list of ~50 ASX-listed ETFs** spanning 12 asset classes: broad AU equity, AU sector ETFs, broad international equity, US equity (hedged and unhedged), emerging markets, thematic (cybersecurity, robotics+AI, ESG, healthcare, agriculture, crypto), AU bonds, global bonds, cash, AU and global property, commodities.

#### Why ASX 300 over ASX 200?

The ASX 300 covers ~95% of the Australian equity market by capitalisation, including small/mid-caps that aren't in the 200. It's a better universe for users who don't just want mega-caps.

#### Why not all ASX-listed equities?

There are ~2 000 ASX-listed companies. The bottom ~1 700 are mostly micro-cap miners, shell companies, and listed investment funds with very thin trading and patchy data on Yahoo Finance. Including them would mean spending most of the engineering effort on data cleaning for instruments no rational investor would touch. Better to start narrow and well-curated.

#### Why curate the ETF list?

There are ~250 ASX-listed ETFs. Most are niche or illiquid. The 50 in the curated list cover 95% of what retail investors actually consider, and the asset-class tagging gives the portfolio engine a natural way to organise the universe. Curation here is a *signal of judgement*, not laziness — recruiters notice when a developer can pick the *right* 50 things.

### 3.2 Data source

We use **Yahoo Finance** via the [`yfinance`](https://github.com/ranaroussi/yfinance) library:

- Free, no API key, well-maintained.
- Returns OHLCV, dividends, splits, basic metadata in one call.
- Has known rate limits but tolerates polite usage (we sleep 250ms between calls).
- Has known data quality issues for very illiquid stocks (which is partly why we curate the universe).

For a real product we'd consider IEX Cloud or Refinitiv (both paid). For a portfolio project, Yahoo is right.

### 3.3 Storage

A single **SQLite** file (`data/market.sqlite`) with three tables:

- `instruments` — one row per ticker, with metadata.
- `prices` — daily OHLCV rows, primary key `(ticker, date)`. Indexed on both columns separately too.
- `dividends` — dividend payments, primary key `(ticker, date)`.

Why SQLite:

- Zero configuration. No server to run.
- Single file, easy to back up or ship.
- Pandas can read/write directly via `to_sql` / `read_sql`.
- For ~575 000 rows it's massively over-spec'd, which means it's fast.

A `metrics` table is added by the analysis layer — see §4.

### 3.4 Scraping etiquette

The Wikipedia request sets a descriptive `User-Agent` identifying the project, version, and a contact email. Wikipedia rejects anonymous requests with HTTP 403; this is the simplest fix. We also fall back to ASX 200 if the ASX 300 page returns an unexpected layout.

---

## 4. Analytics layer

The analytics layer reads adjusted-close prices from SQLite and computes 12 metrics per instrument, writing them to a `metrics` table.

### 4.1 Metrics

For each instrument:

| Metric | Definition | Why it matters |
|---|---|---|
| `return_1y/3y/5y/10y` | Compound annual growth rate of adjusted close over the window | Different windows reveal different stories; 5y is the workhorse |
| `volatility_1y/3y` | Stdev of daily returns × √252 | Headline "risk" measure |
| `sharpe_1y/3y` | (return − RBA cash rate) / volatility | Reward per unit of risk; the dominant ranking signal |
| `max_drawdown_5y` | Largest peak-to-trough decline | What volatility hides; how *bad* the worst case felt |
| `beta_5y` | OLS coefficient of asset returns vs STW.AX returns | Sensitivity to the broader ASX market |
| `dividend_yield_ttm` | Sum of trailing-12-month dividends / latest price | Crucial for income-focused users |

### 4.2 Why these specific metrics

This is the foundation set used by every equity-screening tool. They span return, risk, risk-adjusted return, tail risk, market sensitivity, and income — i.e. they cover the questions a thoughtful adviser actually asks.

Things deliberately *not* computed (yet):

- **CAPM alpha** — adds little beyond beta + Sharpe at this stage.
- **Multi-factor (Fama-French) loadings** — would require multi-factor return series; out of scope.
- **Information ratio vs a custom benchmark** — only meaningful with a stated benchmark per instrument.

### 4.3 Risk-free rate

Hard-coded to **4.35%** (RBA cash rate as of project creation). In a production system we'd refresh from the RBA API on each run; for a portfolio project, a constant is fine and clearly documented.

### 4.4 Beta benchmark choice

Beta is computed against **STW.AX** (SPDR S&P/ASX 200 ETF), not against the ASX 200 index directly. Why:

- STW.AX is already in our universe (no extra fetches needed).
- Tracking error vs the index is tiny (a few basis points).
- Simpler than maintaining a separate index data feed.
- Defensible — many institutional analysts use ETFs as proxies.

### 4.5 Edge cases

- Insufficient history → returns `None` rather than fabricating a number. (E.g. a 2-year-old stock has no `return_5y`.)
- Zero variance → no division-by-zero crash; returns `None`.
- Negative or zero starting prices (corporate actions, errors) → returns `None`.
- All-NaN windows → returns `None`.

The `metrics.py` file is intentionally pure functions with no I/O. Each takes a price series and returns a number. This makes it easy to unit-test, easy to reuse from notebooks, and easy to read.

---

## 5. Portfolio construction

Given a `UserProfile`, produce a candidate portfolio (a list of `Holding`s with weights summing to 1).

### 5.1 Why not pure mean-variance optimisation?

Markowitz (1952) is the textbook answer: maximise expected return given target variance, using the asset covariance matrix. In practice it's notoriously brittle:

- Massively sensitive to estimation error in expected returns.
- Tends to dump everything into 2–3 instruments that happen to have high historical Sharpe by luck.
- Produces non-intuitive allocations that are hard to explain.
- Real fund managers usually layer constraints, shrinkage estimators, or Black-Litterman priors on top — at which point you've reinvented something simpler.

Equally, we don't do **equal-weight** because it ignores risk: a portfolio that's 5% in a 60%-vol crypto ETF and 5% in a 5%-vol cash ETF is far from balanced.

### 5.2 The hybrid we use

A **two-stage rules-based + risk-parity** approach:

1. **Risk profile + horizon + geographic tilt → target asset allocation across 12 asset classes**. This is rule-based, transparent, modelled loosely on Vanguard's lifecycle fund glide path (more equity for higher risk tolerance / longer horizon). Codified as constants in `portfolio/profile.py`.

2. **`max_holdings` budget distributed across sleeves proportionally to target weight**. Bigger sleeves (e.g. AU equity at 22%) get up to 3 holdings; smaller sleeves (e.g. commodities at 2%) get 1.

3. **For each asset class**: screen the candidate universe (apply ETFs-only / ESG / sector includes / sector excludes / ticker excludes / min-yield / max-vol / min-history filters), rank survivors by Sharpe ratio (3y if available, else 1y), take the top N for that sleeve.

4. **Within the sleeve**: weight by inverse volatility ("risk parity within sleeve") — assets with lower volatility get larger weights, so each contributes roughly equally to the sleeve's risk.

5. **Position cap**: any single holding capped at `max_position_size` (default 15%); excess redistributed iteratively to avoid renormalisation drift.

### 5.3 Why this design wins

- **Transparent.** Every allocation has a one-sentence reason. Recruiters can audit it.
- **Defensible.** Each step has academic backing (Brinson 1986 for asset allocation, risk-parity for within-sleeve weighting).
- **Robust.** No optimisation solver to misbehave; no opaque numerics.
- **Adjustable.** Each lever (target allocation, position cap, screening filters, sleeve count) is a clearly-named parameter in the code.

### 5.4 Horizon overlay

Short horizons override the risk profile's target allocation:

- < 2 years: forces ≥60% defensive (bonds + cash).
- < 5 years: forces ≥30% defensive.

This reflects the well-established result that equity returns at sub-5-year horizons are dominated by drawdown risk regardless of long-run expected returns.

### 5.5 Geographic tilt overlay

After the base allocation is set, the geo-tilt overlay scales AU vs Global asset classes:

- `au_only`: zeros all global classes.
- `au_heavy`: AU × 1.30, Global × 0.75.
- `neutral`: no change.
- `global_heavy`: AU × 0.75, Global × 1.30.
- `global_only`: zeros all AU equity (cash/bonds kept).

Then the whole allocation is renormalised to 1.0.

### 5.6 Theme and income preferences

`preferred_themes` and `prefer_income` work by **promoting** specific tickers within their sleeves rather than overriding the sleeve structure entirely. E.g. if the user picks "cybersecurity" as a theme, HACK.AX is moved to the front of the candidate list in the Thematic sleeve and is selected if it passes the other filters. This keeps the allocation logic clean: themes never blow up the asset-class structure.

### 5.7 Min-history filter (data quality)

Default `min_history_years = 3`. Instruments without 3 years of price history are excluded entirely from the candidate pool. This eliminates the noise that was inflating projections in earlier versions: newly listed micro-caps with 1-year histories can show annualised returns >1000% (4DX.AX showed 1235% in our data), which is meaningless extrapolation. Users can override down to 1 year if they want to include recent listings.

---

## 6. Projections

The "what could this be worth in N years?" question.

### 6.1 The model

A standard **lognormal returns model**:

```
ln(final / initial) ~ Normal((μ − ½σ²)·T, σ·√T)
```

where μ is the expected (annualised) return, σ is the annualised volatility, T is the horizon in years. The `−½σ²` correction is the difference between the geometric and arithmetic means, which matters for long horizons.

We report three percentiles of the resulting distribution:

- **Pessimistic (P10)**: the value below which the outcome falls 10% of the time.
- **Median (P50)**: the geometric expected value.
- **Optimistic (P90)**: the value above which the outcome falls 10% of the time.

Z = ±1.282 for the 80% confidence band.

### 6.2 Inputs to the model

- μ = portfolio-level expected return = weighted average of the 5-year (preferred) / 3-year / 1-year annualised returns of the holdings.
- σ = portfolio-level expected volatility = weighted average of the 3-year (preferred) / 1-year annualised volatilities of the holdings.

### 6.3 Limitations

- We use **weighted-average volatility as a proxy for portfolio volatility**. The true portfolio volatility depends on the asset covariance matrix and is generally lower (diversification benefit). We use the upper-bound proxy because it's transparent and slightly conservative — better to under-promise than over-promise.
- Historical returns are not predictive of future returns. The projection assumes the historical risk/return profile holds, which it generally doesn't, especially across regime changes (e.g. interest-rate cycles).
- The lognormal assumption underestimates tail risk. Real return distributions have fatter tails than lognormal — extreme drawdowns happen more often than the model suggests.

These limitations are acknowledged in the on-page text accompanying the projection.

---

## 7. AI integration

Two AI features, both using **Claude Haiku 4.5** for cost.

### 7.1 Natural-language profile parser

Free-text user descriptions → structured `UserProfile` fields.

Implementation: Anthropic's **tool use** API with a *forced* tool call. The model has exactly one tool available (`set_profile`), required to call it, with an `input_schema` defining the structured output shape. This is far more reliable than asking the model to "respond in JSON" and parsing free-form output.

Returned fields are deliberately a *partial* mapping: anything the user didn't mention is left absent so the form keeps its defaults. The user reviews and can adjust before submitting.

### 7.2 Plain-English portfolio explanation

`PortfolioResult` → 200-word plain-English summary.

Implementation: a normal Claude call with a structured system prompt. The prompt instructs the model to:

1. Connect the user's stated profile to the actual allocation, referencing specific inputs.
2. Pick out 2–3 of the most interesting holdings or design choices and explain why.
3. Name the key risks honestly: drawdown, volatility, projection band width.
4. Close with a non-advice disclaimer.

We don't use the model for stock-picking. The portfolio engine remains deterministic. This is a deliberate *competence boundary* choice: LLMs are great at parsing free text into structure and at translating numbers into prose — they should not be picking stocks.

### 7.3 Cost and graceful degradation

- Both calls together cost roughly **$0.005** at current Haiku 4.5 pricing.
- Anthropic's $5 free credit covers ~1 000 analyses.
- If `ANTHROPIC_API_KEY` is unset, both endpoints return `ai_available: false`. The frontend disables the natural-language card and hides the explanation card. The rest of the app works exactly as before.

---

## 8. Web layer

### 8.1 FastAPI backend

Five endpoints:

- `GET /` — serves the single-page frontend.
- `GET /api/health` — liveness + AI availability flag.
- `GET /api/sectors` — distinct GICS sectors in the universe (used to populate the include/exclude pickers).
- `GET /api/themes` — list of available theme keys.
- `POST /api/portfolio` — `UserProfile` JSON → `PortfolioResult`.
- `POST /api/parse` — free text → structured profile fields (AI).
- `POST /api/explain` — `{profile, result}` → plain-English summary (AI).

Pydantic models on every endpoint give us automatic OpenAPI docs at `/docs`, type validation, and clear error messages.

### 8.2 Frontend

Vanilla HTML/CSS/JavaScript. No framework, no build step. ~700 lines total.

The reason for no React (or similar): the page is single-page with one form, one results area, and a few charts. A framework would add 100KB+ of bundle for no functional benefit, and would obscure the simple "form-submit-then-render" logic that's the whole point of a portfolio piece.

Chart.js is loaded from a CDN; we use it for the asset-allocation doughnut and the projection line chart. Everything else is hand-rolled CSS Grid.

### 8.3 Hosting

The intended deployment target is a free tier on **Render** or **Fly.io**:

- Single Dockerfile / `render.yaml`.
- The SQLite database is small enough (~50MB) to ship inside the deployment artefact, avoiding the need for a separate database service.
- Cold-start on free tiers is ~15s; acceptable for a portfolio demo.
- `ANTHROPIC_API_KEY` configured as a secret in the hosting console.

---

## 9. Limitations and known issues

Honest list — things that would block this from being a real product:

- **No transaction-cost modelling.** The portfolio rebalances are presented as if costless. In practice, brokerage fees, bid-ask spreads, and (for individual stocks) market impact would erode small portfolios significantly.
- **No tax considerations.** Australian franking credits, CGT discounts, and superannuation wrappers all materially affect after-tax returns. Ignored.
- **No survivorship bias correction.** The ASX 300 list is the *current* constituents. We don't account for stocks that have been delisted or removed from the index — historical returns are flattering as a result.
- **Returns are assumed stationary.** The model treats the 5-year historical return as an unbiased estimate of the future return. This is an assumption, not a fact.
- **Lognormal projection underestimates tail risk.** Real markets have fatter tails than the model implies. Acknowledged on the page; not corrected.
- **Wikipedia scraping is fragile.** If the ASX 300 page layout changes, the scraper falls back to ASX 200 (which is more stable) but could break entirely. Documented and acceptable for a portfolio piece.
- **Yahoo Finance data quality.** Some splits and dividends are missed or misdated for less-traded instruments. Acceptable for screening; would require a paid data source for a real product.
- **Beta is computed against STW.AX, a proxy.** Tracking error is small but non-zero.
- **No covariance modelling for portfolio volatility.** We use weighted-average volatility, which is an upper bound. Diversification benefit is therefore understated.
- **The portfolio engine treats every sleeve symmetrically.** A more sophisticated approach would weight sleeves with more candidates more heavily.

---

## 10. Future work

Things I'd add if continuing:

- **Backtesting.** Run the construction algorithm on historical universes to see how the produced portfolios would have performed. Particularly important for the projection: are the model's confidence bands actually well-calibrated?
- **Monte Carlo projection.** Replace the closed-form lognormal model with a 10 000-path Monte Carlo using the asset covariance matrix. More accurate, less analytical.
- **Tax wrapper modelling.** Add a "super" / "non-super" toggle that adjusts after-tax returns for franking credits and CGT.
- **Live broker integration.** Allow the user to compare the candidate portfolio against their actual current holdings (read-only via a broker API).
- **Scheduled refresh.** Cron the data pipeline to run nightly via the hosting platform's job scheduler.
- **More granular ESG screen.** Source ESG ratings from a real provider (Sustainalytics, MSCI) instead of the current "ETF tagged ESG" proxy.
- **A proper covariance matrix in the projection.** Compute the daily-return correlation matrix across the universe and use it to estimate true portfolio volatility (lower than the current weighted-average upper bound).
- **A second AI feature: "what would change if…"** counterfactual analysis ("if you delayed retirement by 5 years, your portfolio would shift from 60/40 to 75/25 and the projected median would increase by X").

---

*Last updated: 2026-05-07. Authored by Arlen Chijian.*
