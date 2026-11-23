from flask import Flask, render_template, request, jsonify, Response
import sqlite3, csv, io, os
from datetime import datetime, date, timedelta

app = Flask(__name__)
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'stocks.db'))


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS screenings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            screen_date TEXT NOT NULL,
            entry_price REAL,
            closes TEXT DEFAULT '[]',
            highs TEXT DEFAULT '[]',
            lows TEXT DEFAULT '[]',
            target REAL,
            stop REAL,
            result TEXT DEFAULT 'PENDING',
            hit_day INTEGER,
            updated_at TEXT,
            UNIQUE(symbol, screen_date)
        )''')
        # Migrate databases created by earlier versions without deleting records.
        columns = {row['name'] for row in con.execute('PRAGMA table_info(screenings)').fetchall()}
        if 'highs' not in columns:
            con.execute("ALTER TABLE screenings ADD COLUMN highs TEXT DEFAULT '[]'")
        if 'lows' not in columns:
            con.execute("ALTER TABLE screenings ADD COLUMN lows TEXT DEFAULT '[]'")


def fetch_history(symbol, screen_date):
    """Fetch screening-date close plus the first 15 sessions after screening.

    Stores daily High/Low as well as Close. High/Low show whether a TP/SL
    level was touched intraday. If both are touched on the same daily candle,
    their order cannot be known from daily OHLC and the result is BOTH.
    """
    try:
        import yfinance as yf
        ticker = symbol.strip().upper()
        if not ticker.endswith(('.NS', '.BO')):
            ticker += '.NS'
        start = datetime.strptime(screen_date, '%Y-%m-%d').date()
        end = max(date.today() + timedelta(days=2), start + timedelta(days=45))

        hist = None
        errors = []
        try:
            hist = yf.Ticker(ticker).history(
                start=start.isoformat(), end=end.isoformat(),
                auto_adjust=False, actions=False, timeout=20
            )
        except Exception as exc:
            errors.append(str(exc)[:120])
        if hist is None or hist.empty:
            try:
                hist = yf.download(
                    ticker, start=start.isoformat(), end=end.isoformat(),
                    auto_adjust=False, progress=False, threads=False, timeout=20
                )
                if hist is not None and not hist.empty and getattr(hist.columns, 'nlevels', 1) > 1:
                    hist.columns = hist.columns.get_level_values(0)
            except Exception as exc:
                errors.append(str(exc)[:120])

        if hist is None or hist.empty:
            detail = '; '.join(x for x in errors if x)
            return None, [], [], [], f'No Yahoo Finance history for {ticker}. Check the NSE symbol or internet connection. {detail}'

        hist = hist[~hist.index.duplicated(keep='last')].sort_index()
        dates = [x.date() for x in hist.index]
        entry = None
        if start in dates:
            try:
                entry = float(hist.iloc[dates.index(start)]['Close'])
            except (TypeError, ValueError, KeyError):
                entry = None

        future_rows = hist[[d > start for d in dates]].head(15)
        closes, highs, lows = [], [], []
        for _, row in future_rows.iterrows():
            try:
                c, h, l = float(row['Close']), float(row['High']), float(row['Low'])
                if all(v == v for v in (c, h, l)):
                    closes.append(round(c, 4))
                    highs.append(round(h, 4))
                    lows.append(round(l, 4))
                else:
                    continue
            except (TypeError, ValueError, KeyError):
                continue

        if not closes:
            return (round(entry, 4) if entry is not None else None), [], [], [], f'No trading-session prices found after {start.isoformat()} for {ticker}.'
        return (round(entry, 4) if entry is not None else None), closes, highs, lows, None
    except ImportError:
        return None, [], [], [], 'The market-data package is not installed. Close the app and run: pip install -r requirements.txt'
    except Exception as exc:
        return None, [], [], [], f'Price lookup failed: {str(exc)[:180]}'


def calculate(entry_price, closes, highs, lows):
    """Use the screening-date close as the reference; test intraday High/Low on Days 1-15."""
    if entry_price is None or not closes:
        return None, None, 'PENDING', None
    target = round(float(entry_price) * 1.05, 4)
    stop = round(float(entry_price) * 0.98, 4)

    # Entry is the screening-date close, so all subsequent trading sessions
    # (Day 1 through Day 15) are eligible for intraday TP/SL detection.
    for i in range(min(len(closes), len(highs), len(lows))):
        hit_tp = highs[i] >= target
        hit_sl = lows[i] <= stop
        if hit_tp and hit_sl:
            return target, stop, 'BOTH', i + 1
        if hit_tp:
            return target, stop, 'TP', i + 1
        if hit_sl:
            return target, stop, 'SL', i + 1
    return target, stop, 'RUNNING', None

def row_dict(r):
    import json
    closes = json.loads(r['closes'] or '[]')
    highs = json.loads(r['highs'] or '[]') if 'highs' in r.keys() else []
    lows = json.loads(r['lows'] or '[]') if 'lows' in r.keys() else []
    return {
        'id': r['id'], 'symbol': r['symbol'], 'screen_date': r['screen_date'],
        'entry_price': r['entry_price'], 'closes': closes, 'highs': highs, 'lows': lows,
        'target': r['target'], 'stop': r['stop'], 'result': r['result'],
        'hit_day': r['hit_day'], 'updated_at': r['updated_at']
    }


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/screenings')
def list_screenings():
    with db() as con:
        rows = con.execute('SELECT * FROM screenings ORDER BY screen_date DESC, id DESC').fetchall()
    items = [row_dict(r) for r in rows]
    counts = {k: sum(1 for x in items if x['result'] == k) for k in ['TP', 'SL', 'RUNNING', 'PENDING', 'BOTH']}
    resolved = counts['TP'] + counts['SL']
    return jsonify({'items': items, 'summary': {
        'total': len(items), 'tp': counts['TP'], 'sl': counts['SL'],
        'running': counts['RUNNING'], 'pending': counts['PENDING'], 'both': counts['BOTH'],
        'win_rate': round(counts['TP'] / resolved * 100, 2) if resolved else 0,
        'tp_pct': round(counts['TP'] / len(items) * 100, 2) if items else 0,
        'sl_pct': round(counts['SL'] / len(items) * 100, 2) if items else 0,
        'running_pct': round(counts['RUNNING'] / len(items) * 100, 2) if items else 0
    }})


@app.post('/api/import')
def import_bulk():
    payload = request.get_json(silent=True) or {}
    raw = payload.get('text', '')
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    added, skipped, errors = 0, 0, []
    parsed = []
    for line_no, line in enumerate(lines, start=1):
        # Google Sheets / Excel clipboard data is normally tab-separated;
        # CSV files are comma-separated. Accept both, including a header row.
        parts = [p.strip().strip('\"') for p in (line.split('\t') if '\t' in line else line.split(','))]
        if len(parts) < 2:
            errors.append(f'Line {line_no}: add both a stock symbol and screening date, separated by a TAB or comma. Example: RELIANCE,23-01-2026')
            continue
        raw_symbol, date_text = parts[0].upper().strip(), parts[1].strip()
        if raw_symbol in ('SYMBOL', 'STOCK', 'STOCK SYMBOL', 'STOCK NAME', 'TICKER', 'NAME'):
            continue
        if raw_symbol.startswith('BSE:'):
            symbol = raw_symbol[4:].strip()
            if not symbol.endswith('.BO'):
                symbol += '.BO'
        elif raw_symbol.startswith('NSE:'):
            symbol = raw_symbol[4:].strip()
        else:
            symbol = raw_symbol
        try:
            if len(date_text) == 10 and date_text[4] == '-':
                d = datetime.strptime(date_text, '%Y-%m-%d').date()
            else:
                d = datetime.strptime(date_text, '%d-%m-%Y').date()
            if d > date.today():
                raise ValueError('date is in the future')
            if not symbol or len(symbol) > 30:
                raise ValueError('invalid symbol')
            parsed.append((symbol, d.isoformat()))
        except Exception as e:
            errors.append(f'Line {line_no}: invalid date or symbol ({e})')
    for symbol, d in parsed:
        try:
            with db() as con:
                cur = con.execute('INSERT OR IGNORE INTO screenings(symbol, screen_date) VALUES (?,?)', (symbol, d))
                if cur.rowcount:
                    added += 1
                else:
                    skipped += 1
        except Exception as e:
            errors.append(f'{symbol}: {e}')
    return jsonify({'added': added, 'skipped': skipped, 'errors': errors})


@app.post('/api/refresh')
def refresh_prices():
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids')
    with db() as con:
        if ids:
            placeholders = ','.join('?' for _ in ids)
            rows = con.execute(f'SELECT * FROM screenings WHERE id IN ({placeholders})', ids).fetchall()
        else:
            rows = con.execute('SELECT * FROM screenings').fetchall()
    updated, failed = 0, []
    import json
    for r in rows:
        entry, closes, highs, lows, error = fetch_history(r['symbol'], r['screen_date'])
        if error:
            failed.append({'symbol': r['symbol'], 'date': r['screen_date'], 'error': error})
            continue
        target, stop, result, hit_day = calculate(entry, closes, highs, lows)
        with db() as con:
            con.execute('''UPDATE screenings SET entry_price=?, closes=?, highs=?, lows=?, target=?, stop=?, result=?, hit_day=?, updated_at=? WHERE id=?''',
                (entry, json.dumps(closes), json.dumps(highs), json.dumps(lows), target, stop, result, hit_day, datetime.now().isoformat(timespec='seconds'), r['id']))
        updated += 1
    return jsonify({'updated': updated, 'failed': failed})


@app.delete('/api/screenings/<int:item_id>')
def delete_screening(item_id):
    with db() as con:
        con.execute('DELETE FROM screenings WHERE id=?', (item_id,))
    return jsonify({'ok': True})


@app.get('/api/export')
def export_csv():
    import json
    with db() as con:
        rows = con.execute('SELECT * FROM screenings ORDER BY screen_date DESC, id DESC').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Stock Symbol', 'Screening Date', 'Screening-date Close', *[f'Day {i} Close' for i in range(1,16)], 'TP (+5% from screening-date close)', 'SL (-2% from screening-date close)', 'Result', 'Hit Day'])
    for r in rows:
        closes = json.loads(r['closes'] or '[]')
        writer.writerow([r['symbol'], r['screen_date'], r['entry_price'] or '', *closes, *([''] * (15-len(closes))), r['target'] or '', r['stop'] or '', r['result'], r['hit_day'] or ''])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=stock_screening_results.csv'})


init_db()
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
