import unittest
import pandas as pd
from scanners import v3,bf_raw,bf_pool,bf_budget,g3_budget,s2
class ScannerTests(unittest.TestCase):
 def test_v3_excludes_current_from_previous_60(self):
  d=pd.DataFrame({'close_adj':[10.]*60+[10.3],'volume':[100.]*60+[210.]})
  r=v3(d,True); self.assertEqual(r['strength'],3); self.assertEqual(v3(d,None)['status'],'unverified_universe')
 def test_missing_is_not_no_signal(self):
  self.assertEqual(s2(pd.DataFrame(),'600000')['status'],'missing_data')
  self.assertEqual(bf_raw(pd.DataFrame(),'600000')['status'],'missing_data')
 def test_bf_pool_never_downgrades(self):
  rows=[dict(code='600001',category='A',S=300),dict(code='600002',category='B',S=500)]
  self.assertEqual([r['code'] for r in bf_pool(rows)],['600001'])
 def test_bf_signal_and_window(self):
  d=pd.DataFrame([dict(open_raw=10.,high_raw=10.,close_raw=10.,upper=11.,prev_ref=10.,factor=1.,amount=300_000_000.,is_st=False,volume=100.) for _ in range(61)])
  d.loc[54,'close_raw']=11.;d.loc[59,'open_raw']=10.3;d.loc[59,'close_raw']=10.;d.loc[60,'open_raw']=10.;d.loc[60,'close_raw']=11.
  r=bf_raw(d,'600000');self.assertEqual(r['status'],'raw');self.assertEqual(r['category'],'A')
 def test_s2_trigger(self):
  d=pd.DataFrame([dict(date=str(i),open_adj=10.,close_adj=10.,volume=100.,pct=0.,total_shares=1e9,close_raw=10.,is_st=False) for i in range(20)])
  d.loc[12,['pct','volume','close_adj']]=[10.,200.,10.]
  d.loc[13,['pct','volume']]=[1.,200.]
  d.loc[14:18,'volume']=[100.,90.,80.,70.,60.]
  d.loc[19,['open_adj','close_adj','volume','pct']]=[10.,10.3,121.,3.]
  self.assertEqual(s2(d.drop(columns=['total_shares','close_raw']),'600000')['status'],'triggered')
 def test_budgets(self):
  self.assertEqual(bf_budget(120000,0),100000);self.assertEqual(bf_budget(20000,0),17000);self.assertEqual(g3_budget(120000),20000);self.assertEqual(g3_budget(95000),0)
if __name__=='__main__':unittest.main()
