import tempfile, unittest
from pathlib import Path
import pandas as pd
from bf_index_3050 import compute
class IndexTests(unittest.TestCase):
 def test_missing_session_not_skipped(self):
  dates=[f'2026-01-{i:02d}' for i in range(1,22)]
  with tempfile.TemporaryDirectory() as tmp:
   for i,d in enumerate(dates):
    rows=[{'date':d,'code':'600000','close':10.,'volume':100.}]
    if i!=10: rows.append({'date':d,'code':'600001','close':10.,'volume':100.})
    pd.DataFrame(rows).to_csv(Path(tmp)/(d+'.csv'),index=False)
   r=compute(tmp,dates,dates[-1],['600000','600001'])
   self.assertEqual(r['constituent_count_each_day'][dates[10]],1)
   self.assertEqual(r['constituent_count_each_day'][dates[11]],1)
   self.assertEqual(r['constituent_count_each_day'][dates[12]],2)
   self.assertAlmostEqual(r['I'],1);self.assertTrue(r['market_gate_pass'])
 def test_missing_file_raises(self):
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(ValueError):compute(tmp,[f'2026-01-{i:02d}' for i in range(1,22)],'2026-01-21',['600000'])
if __name__=='__main__':unittest.main()
