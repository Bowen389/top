import unittest
from longhu_independent import screen
D='2026-11-03'
def snap(code='002343',buy=21000000,sell=0,pct=6,inst_sell=False,missing=False):
 common=dict(SECURITY_CODE=code,TRADE_DATE=D+' 00:00:00',TRADE_ID='1',CHANGE_TYPE='x',EXPLANATION='日换手率')
 over={**common,'SECURITY_NAME_ABBR':'某股','CLOSE_PRICE':8.3,'CHANGE_RATE':pct,'BUY_SEAT_NEW':'1','SELL_SEAT_NEW':'1'}
 b={**common,'OPERATEDEPT_CODE':'1','OPERATEDEPT_NAME':'机构专用','BUY':buy,'SELL':sell,'NET':buy-sell}
 s={**common,'OPERATEDEPT_CODE':'2','OPERATEDEPT_NAME':'机构专用' if inst_sell else '某营业部','BUY':1,'SELL':2,'NET':-1}
 return dict(overview=[over],buy=[b],sell=[s] if not missing else [{**s,'SECURITY_CODE':'999999'}])
class Tests(unittest.TestCase):
 def test_shenzhen_buy_threshold_strict(self):
  self.assertEqual(len(screen(D,snap())[0]),1);self.assertEqual(len(screen(D,snap(buy=20000000))[0]),0);self.assertEqual(len(screen(D,snap(sell=1))[0]),0)
 def test_sell_institution_disqualifies(self):self.assertEqual(len(screen(D,snap(inst_sell=True))[0]),0)
 def test_gain_and_six_prefix(self):
  self.assertEqual(len(screen(D,snap(pct=4.999))[0]),0);self.assertEqual(len(screen(D,snap(code='600000',sell=1))[0]),1)
 def test_multiple_institution_rows_not_combined_for_zero_prefix(self):
  x=snap(buy=11000000);x['overview'][0]['BUY_SEAT_NEW']='11'
  x['buy'].append({**x['buy'][0],'OPERATEDEPT_CODE':'3'})
  self.assertEqual(len(screen(D,x)[0]),0)
 def test_multiple_institution_rows_combined_for_six_prefix(self):
  x=snap(code='600000',buy=11000000);x['overview'][0]['BUY_SEAT_NEW']='11'
  x['buy'].append({**x['buy'][0],'OPERATEDEPT_CODE':'3'})
  self.assertEqual(screen(D,x)[0][0]['qualifying_amount_yuan'],22000000)
 def test_empty_source_is_not_no_signal(self):
  with self.assertRaises(RuntimeError):screen(D,{'overview':[],'buy':[],'sell':[]})
if __name__=='__main__':unittest.main()
