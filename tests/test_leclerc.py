import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'leclerc', Path(__file__).resolve().parents[1] / 'scripts/leclerc/parse_leclerc.py',
)
leclerc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leclerc)

TEXT = '''E.LECLERC TEST
1 RUE DU TEST
00000 TEST
BIENVENUE
Caisse 003-0127 13 octobre 2026 16:07
Ticket 13/10/26 0 TEST 00000
990032192262860TEST00000
TTC TVA
>> EPICERIE
* SAUCE TOMATE
2 X 1.59€ 3.18 2
>> FRUITS ET LEGUMES
HARICOT VERT
0.436kg X 4.99€/kg 2.18 2
----------
Total 3 articles 5.36
CB 5.36
Code HT TVA TTC
2 5%50 5.08 0.28 5.36
PUBLICITE 99.99 2
'''


class LeclercTests(unittest.TestCase):
    def testLotsWeightsAndAppendices(self):
        receipt = leclerc.parseReceipt({'raw_text': TEXT.replace(' ', '\xa0')})
        self.assertEqual(receipt['date'], '2026-10-13')
        self.assertEqual(receipt['total_cents'], 536)
        lot, weighed = receipt['items']
        self.assertEqual((lot['quantity'], lot['unit_price_cents'], lot['marked']), (2, 159, 1))
        self.assertEqual((weighed['weight_kg'], weighed['price_per_kg_cents']), (0.436, 499))
        self.assertIsNone(weighed['unit_price_cents'])

    def testRejectIncompleteOrInconsistentCaptures(self):
        for before, after in [('5.36', '5.37'), ('Total 3', 'Total 2'),
                              ('2.18 2', '2.19 2'), ('HARICOT VERT\n', ''),
                              ('CB 5.36', 'CB 5.35')]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                leclerc.parseReceipt({'raw_text': TEXT.replace(before, after)})
        with self.assertRaises(ValueError):
            leclerc.parseReceipt({'raw_text': TEXT, 'ticket_id': 'WRONG'})
        with self.assertRaises(ValueError):
            leclerc.parseReceipt({'raw_text': TEXT, 'html': '<table/>', 'sha256': 'wrong'})

    def testIdempotencyRetentionAndBatchFailure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sources').mkdir()
            source = root / 'sources/one.json'
            source.write_text(json.dumps({'raw_text': TEXT}))
            self.assertEqual(leclerc.indexReceipts(root)['receipts'], 1)
            leclerc.indexReceipts(root)
            with sqlite3.connect(root / 'leclerc.sqlite') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM receipt_items').fetchone()[0], 2)
            second = TEXT.replace('TEST00000', 'TEST00001')
            other = root / 'sources/two.json'
            other.write_text(json.dumps({'raw_text': second}))
            leclerc.indexReceipts(root)
            other.unlink()
            self.assertEqual(leclerc.indexReceipts(root)['receipts'], 2)
            other.write_text(json.dumps({'raw_text': second.replace('5.36', '5.37')}))
            with self.assertRaises(ValueError):
                leclerc.indexReceipts(root)
            with sqlite3.connect(root / 'leclerc.sqlite') as db:
                self.assertEqual(db.execute('SELECT SUM(total_cents) FROM receipts').fetchone()[0], 1072)


if __name__ == '__main__':
    unittest.main()
