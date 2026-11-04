import copy,unittest
from ledger_engine import apply_trade
class Tests(unittest.TestCase):
 def setUp(self):
  self.s={'first_execution_date':'2026-11-04','accounts':{'4':{'cash_cny':200000,'positions':[]}},'trades':[], 'last_applied_event_date':None}
  self.e={'event_id':'A','date':'2026-11-04','strategy':'4','side':'BUY','code':'002343','shares':8000,'price':8.3083,'source':'模拟开盘价+0.1%'}
 def test_buy_fees_and_duplicate(self):
  apply_trade(self.s,self.e);self.assertEqual(self.s['accounts']['4']['cash_cny'],133526.95);self.assertEqual(self.s['trades'][0]['commission_cny'],6.65)
  with self.assertRaises(ValueError):apply_trade(self.s,self.e)
 def test_no_same_day_sell(self):
  apply_trade(self.s,self.e);x={**self.e,'event_id':'B','side':'SELL'}
  with self.assertRaises(ValueError):apply_trade(self.s,x)
 def test_no_fake_prior_fill(self):
  with self.assertRaises(ValueError):apply_trade(self.s,{**self.e,'date':'2026-11-03'})
if __name__=='__main__':unittest.main()
