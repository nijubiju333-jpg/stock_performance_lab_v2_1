# Stock Screener Performance Tracker

A local Flask + SQLite web app for bulk importing NSE stock screening dates, fetching daily historical prices, calculating Day-1-based TP/SL levels using intraday daily High/Low ranges, tracking 15 subsequent trading sessions, and exporting results to CSV.

## Run on Windows
1. Install Python 3.10 or newer from https://www.python.org/downloads/ (enable **Add Python to PATH**).
2. Extract this folder.
3. Double-click `run_windows.bat`.
4. Open http://127.0.0.1:5000 in your browser.

The first run installs dependencies. Your records are stored in `stocks.db` in this folder. Keep that file to retain your data.

## Bulk import format
Paste one record per line, with a comma or tab between symbol and screening date:

```text
RELIANCE,2026-01-23
SBIN,23-01-2026
TCS,2026-01-26
```

Symbols are treated as NSE tickers by default. For a BSE listing, add `.BO` to the symbol (example: `RELIANCE.BO`). Duplicate symbol/date pairs are skipped.

## Calculation rules
- Entry close: screening-date close when that date was a trading session (reference only).
- Day 1: first trading-session close strictly after the screening date, including when the screening date is a weekend or market holiday.
- Target: screening-date close × 1.05.
- Stop: screening-date close × 0.98.
- The screening-date close sets the reference price. Intraday High/Low checks begin on the next trading session (Day 1) and continue through Day 15.
- Result: first subsequent session where High reaches TP or Low reaches SL, checked in chronological order through the 15-session window.
- If both TP and SL are inside the same daily candle, result is BOTH because daily OHLC cannot reveal which level occurred first.
- If no level is reached in available data, result is RUNNING. If the screening date has no closing-price data (for example, it was a market holiday), the entry-based target/stop cannot be calculated and the record remains PENDING until a valid entry price is available.
- TP/SL percentages in the dashboard use all imported records as the denominator. Win rate uses only resolved TP + SL records.

## Market data note
After importing new rows, the app automatically attempts to fetch their historical prices. You can also use **Refresh prices** at the top of the dashboard to retry. Any lookup failures are displayed under the bulk-import panel.

The app uses the `yfinance` Python package to retrieve Yahoo Finance historical data. This is an unofficial data source, may be delayed, rate-limited, incomplete for some symbols, and is not guaranteed for every NSE listing. Validate important results against an exchange/broker source. The app does not place trades or provide investment advice.

## Reset / backup
Back up `stocks.db` regularly. To reset all records, stop the app and delete `stocks.db`.
