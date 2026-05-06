# ASX Portfolio Analyser

An educational portfolio-analysis tool for ASX-listed equities and ETFs. Users describe their starting capital, risk tolerance, investment horizon, and preferences; the tool returns a candidate portfolio with transparent reasoning, historical risk/return characteristics, and AI-generated explanations.

> **Not financial advice.** This project is a data-analysis and educational tool. It does not constitute personal financial advice under the Australian *Corporations Act 2001*. Anyone making investment decisions should seek advice from an AFSL-licensed adviser and consult [ASIC's MoneySmart](https://moneysmart.gov.au/).

## What this project demonstrates

- **Data engineering** — automated pipeline pulling daily OHLCV data, dividends, and corporate actions for the ASX 200 and major Australian ETFs from Yahoo Finance, with caching and a SQLite backing store.
- **Quantitative analysis** — for each instrument, computes annualised return, volatility, Sharpe ratio, max drawdown, beta vs the ASX 200, dividend yield, sector exposure, and correlation.
- **Portfolio construction** — rules-based screening combined with mean-variance optimisation, parameterised by user inputs.
- **AI integration** — natural-language input parsing (free-text "I'm 28 with $20k and want to retire at 60") and AI-generated plain-English explanations of recommended portfolios.
- **Frontend** — clean, minimal web UI with input form, results page, and interactive charts.

## Project structure

```
.
├── data/           # Raw and processed market data (gitignored)
├── pipeline/       # Data ingestion scripts
├── analysis/       # Analytics and portfolio construction
├── notebooks/      # Exploratory Jupyter notebooks
├── web/            # Frontend HTML/CSS/JS
├── requirements.txt
└── README.md
```

## Status

Project scaffolded. Data pipeline in development.

---

Built by Arlen Chijian as a portfolio project. Source code available on GitHub.
