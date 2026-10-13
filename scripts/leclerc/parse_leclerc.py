#!/usr/bin/env python3
"""Index preserved E.Leclerc DOM receipts without OCR or network access."""

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

MONTHS = dict(zip(
    'janvier février mars avril mai juin juillet août septembre octobre novembre décembre'.split(),
    range(1, 13),
))
MONEY = r'-?\d+[.,]\d{2}'


def cents(value):
    return int(Decimal(value.replace(',', '.')) * 100)


def decimal(value):
    return Decimal(value.replace(',', '.'))


def parseReceipt(source):
    if source.get('sha256'):
        content = json.dumps([source['raw_text'], source['html']], ensure_ascii=False, separators=(',', ':'))
        if hashlib.sha256(content.encode()).hexdigest() != source['sha256']:
            raise ValueError('Source content checksum differs from preserved capture')
    text = source['raw_text'].replace('\xa0', ' ').replace('\u202f', ' ')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header = re.search(
        r'Caisse\s+(\S+)\s+(\d+)\s+(\w+)\s+(\d{4})\s+(\d{2}:\d{2})', text,
    )
    barcode = re.search(r'^\s*(99[0-9A-Z]{18,30})\s*$', text, re.M)
    total_match = re.search(rf'^\s*Total\s+(\d+)\s+articles?\s+({MONEY})\s*$', text, re.M)
    if not header or not barcode or not total_match:
        raise ValueError('Missing register, ticket barcode or total')
    ticket_id = barcode[1]
    if source.get('ticket_id', ticket_id) != ticket_id:
        raise ValueError('Source ticket ID differs from printed barcode')
    date = datetime(int(header[4]), MONTHS[header[3].lower()], int(header[2])).date()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(r'TTC\s+TVA', line))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith('Total '))
    items, discounts, pending = [], [], []
    section = None
    for line in lines[start + 1:end]:
        if re.fullmatch(r'[-*]+', line):
            continue
        if line.startswith('>>'):
            if pending:
                raise ValueError('Unparsed article before department heading')
            section = line[2:].strip()
            continue
        match = re.fullmatch(rf'(.+?)\s+({MONEY})\s+(\d+)', line)
        if not match:
            pending.append(line)
            continue
        label, total, tax = match.groups()
        quantity, weight, unit, per_kg = Decimal(1), None, None, None
        measure = re.fullmatch(rf'(\d+(?:[.,]\d+)?)(kg)?\s+[Xx×]\s+({MONEY})€(/kg)?', label)
        if measure:
            if not pending or bool(measure[2]) != bool(measure[4]):
                raise ValueError('Quantity/weight line without article or unit mismatch')
            if measure[2]:
                weight, per_kg = decimal(measure[1]), cents(measure[3])
                expected = weight * per_kg
            else:
                quantity, unit = decimal(measure[1]), cents(measure[3])
                expected = quantity * unit
            if int(expected.quantize(Decimal(1), rounding=ROUND_HALF_UP)) != cents(total):
                raise ValueError('Quantity or weight does not reconcile with line total')
            name = ' '.join(pending)
        else:
            name = ' '.join(pending + [label])
            unit = cents(total)
        raw_line = '\n'.join(pending + [line])
        pending = []
        marked = name.startswith('*')
        name = name.lstrip('* ').strip()
        if re.match(r'^(REMISE|REDUCTION|RÉDUCTION)\b', name, re.I):
            discounts.append({'label': name, 'amount_cents': cents(total), 'raw_line': raw_line})
            continue
        product_id = hashlib.sha256(' '.join(name.upper().split()).encode()).hexdigest()[:24]
        items.append({
            'product_id': product_id, 'name': name, 'section': section,
            'quantity': float(quantity), 'unit_price_cents': unit,
            'weight_kg': float(weight) if weight is not None else None,
            'price_per_kg_cents': per_kg, 'total_cents': cents(total),
            'tax_code': tax, 'marked': int(marked), 'raw_line': raw_line,
        })
    if pending or not items:
        raise ValueError('Incomplete or empty article section')
    total = cents(total_match[2])
    if sum(i['total_cents'] for i in items) + sum(d['amount_cents'] for d in discounts) != total:
        raise ValueError('Article amounts do not reconcile with ticket total')
    # Weighed produce counts as one article; a unit lot counts its printed quantity.
    if sum(Decimal(str(i['quantity'])) for i in items) != int(total_match[1]):
        raise ValueError('Article quantities differ from printed article count')
    payments = []
    for line in lines[end + 1:]:
        if line.startswith('Code '):
            break
        match = re.fullmatch(rf'(.+?)\s+({MONEY})', line)
        if match:
            payments.append({'method': match[1], 'amount_cents': cents(match[2])})
    if payments and sum(p['amount_cents'] for p in payments) != total:
        raise ValueError('Payment amounts do not reconcile with ticket total')
    return {
        'ticket_id': ticket_id, 'date': date.isoformat(), 'time': header[5],
        'store_name': lines[0], 'store_address': lines[1], 'store_city': lines[2],
        'register': header[1], 'total_cents': total, 'item_count': int(total_match[1]),
        'source_url': source.get('source_url'), 'raw_text': source['raw_text'],
        'items': items, 'discounts': discounts, 'payments': payments,
    }


SCHEMA = '''
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS receipts (
        ticket_id TEXT PRIMARY KEY, date TEXT NOT NULL, time TEXT NOT NULL,
        store_name TEXT, store_address TEXT, store_city TEXT, register TEXT,
        total_cents INTEGER NOT NULL, item_count INTEGER NOT NULL,
        total REAL GENERATED ALWAYS AS (total_cents / 100.0) VIRTUAL,
        source_url TEXT, source_path TEXT NOT NULL, raw_text TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS products (
        product_id TEXT PRIMARY KEY, name TEXT NOT NULL, ean TEXT
    );
    CREATE TABLE IF NOT EXISTS receipt_items (
        ticket_id TEXT REFERENCES receipts(ticket_id), line_number INTEGER,
        product_id TEXT REFERENCES products(product_id), name TEXT NOT NULL,
        section TEXT, quantity REAL, unit_price_cents INTEGER, weight_kg REAL,
        price_per_kg_cents INTEGER, total_cents INTEGER NOT NULL, tax_code TEXT,
        marked INTEGER, raw_line TEXT NOT NULL,
        total REAL GENERATED ALWAYS AS (total_cents / 100.0) VIRTUAL,
        unit_price REAL GENERATED ALWAYS AS (unit_price_cents / 100.0) VIRTUAL,
        price_per_kg REAL GENERATED ALWAYS AS (price_per_kg_cents / 100.0) VIRTUAL,
        PRIMARY KEY (ticket_id, line_number)
    );
    CREATE TABLE IF NOT EXISTS receipt_discounts (
        ticket_id TEXT REFERENCES receipts(ticket_id), line_number INTEGER,
        label TEXT, amount_cents INTEGER, raw_line TEXT,
        PRIMARY KEY (ticket_id, line_number)
    );
    CREATE TABLE IF NOT EXISTS payments (
        ticket_id TEXT REFERENCES receipts(ticket_id), line_number INTEGER,
        method TEXT, amount_cents INTEGER, PRIMARY KEY (ticket_id, line_number)
    );
    CREATE INDEX IF NOT EXISTS items_product ON receipt_items(product_id);
    CREATE VIEW IF NOT EXISTS product_observations AS
        SELECT i.*, r.date, r.time, r.store_name FROM receipt_items i
        JOIN receipts r USING(ticket_id);
'''


def indexReceipts(root):
    paths = sorted((root / 'sources').glob('*.json'))
    if not paths:
        raise ValueError('No sources/*.json captures found')
    # Validate the whole batch before modifying the existing index.
    receipts = {}
    for path in paths:
        source = json.loads(path.read_text())
        try:
            receipt = parseReceipt(source)
        except (ValueError, KeyError, StopIteration) as error:
            raise ValueError(f'{path.name}: {error}') from error
        stamp = source.get('captured_at', '')
        previous = receipts.get(receipt['ticket_id'])
        if previous is None or stamp > previous[0]:
            receipts[receipt['ticket_id']] = (stamp, path, receipt)
    with sqlite3.connect(root / 'leclerc.sqlite') as db:
        db.executescript(SCHEMA)
        for _, path, receipt in receipts.values():
            metadata = {k: v for k, v in receipt.items() if k not in ('items', 'discounts', 'payments')}
            metadata['source_path'] = str(path.resolve())
            keys = ', '.join(metadata)
            params = ', '.join('?' for _ in metadata)
            updates = ', '.join(f'{k}=excluded.{k}' for k in metadata if k != 'ticket_id')
            db.execute(f'INSERT INTO receipts ({keys}) VALUES ({params}) '
                       f'ON CONFLICT(ticket_id) DO UPDATE SET {updates}', list(metadata.values()))
            for table in ('receipt_items', 'receipt_discounts', 'payments'):
                db.execute(f'DELETE FROM {table} WHERE ticket_id=?', (receipt['ticket_id'],))
            for n, item in enumerate(receipt['items'], 1):
                db.execute('INSERT OR IGNORE INTO products(product_id, name) VALUES (?, ?)',
                           (item['product_id'], item['name']))
                row = {'ticket_id': receipt['ticket_id'], 'line_number': n, **item}
                db.execute(f"INSERT INTO receipt_items ({', '.join(row)}) "
                           f"VALUES ({', '.join('?' for _ in row)})", list(row.values()))
            for table, kind in [('receipt_discounts', 'discounts'), ('payments', 'payments')]:
                for n, item in enumerate(receipt[kind], 1):
                    row = {'ticket_id': receipt['ticket_id'], 'line_number': n, **item}
                    db.execute(f"INSERT INTO {table} ({', '.join(row)}) "
                               f"VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        count, total = db.execute('SELECT COUNT(*), SUM(total_cents) FROM receipts').fetchone()
        return {'receipts': count, 'total_cents': total}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', type=Path, default=Path.home() / 'Archive/leclerc')
    args = parser.parse_args()
    try:
        print(json.dumps(indexReceipts(args.archive_dir.expanduser()), ensure_ascii=False))
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Import failed: {error}\n')


if __name__ == '__main__':
    main()
