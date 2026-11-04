"""Current-session experimental TDX panel screening. Strict limitations in output."""
import json, pathlib, sys, requests, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
import numpy as np,pandas as pd
from scanners import s2,bf_raw,bf_pool,mainboard
import market_data
base=market_data.ROOT; out=pathlib.Path('../reports'); sessions=json.loads((out/'sessions_85.json').read_text())['dates']; date=sessions[-1]
df=pd.concat([pd.read_csv(base/(d+'.csv'),dtype={'code':str}) for d in sessions],ignore_index=True)
df=df[df.code.map(mainboard)].copy();df=df[(df.open>0)&(df.close>0)&(df.prev_close>0)&(df.volume>0)].copy()
df=market_data.prepare(df); df['pct']=(df.close/df.prev_close-1)*100
# PC is TDX official previous/reference price, but 10% historical upper is only an approximation.
df['upper']=[float((Decimal(str(x))*Decimal('1.10')).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) for x in df.prev_close]
df['is_st']=df.name.fillna('').str.upper().str.contains('ST',regex=False)
df=df.rename(columns={'open':'open_raw','high':'high_raw','close':'close_raw','adj_open':'open_adj','adj_close':'close_adj','prev_close':'prev_ref'})
today=df[df.date==date]; universe=today.code.unique().tolist(); names=dict(zip(today.code,today.name)); groups={code:g for code,g in df.groupby('code')}
counts={'s2':{},'bf':{}};potential=[];bf=[]
for code in universe:
 g=groups[code].set_index('date').reindex(sessions).reset_index(names='date')
 # An ST, first-day/unlimited or suspicious 10% status inside the relevant BF window invalidates approximation.
 for strategy,fn in [('s2',s2),('bf',bf_raw)]:
  if strategy=='bf' and (g.iloc[-7:].is_st.isna().any() or g.iloc[-7:].is_st.eq(True).any() or g.iloc[-7:].name.fillna('').str.match(r'^[NC]').any()):
   r={'status':'limit_rule_or_ST_unverified'}
  else:r=fn(g,code)
  counts[strategy][r['status']]=counts[strategy].get(r['status'],0)+1
  if strategy=='s2' and r['status']=='triggered':potential.append({'code':code,'name':names[code],'signal_close_raw':float(g.iloc[-1].close_raw),'signal_factor':float(g.iloc[-1].factor),**r})
  if strategy=='bf' and r['status']=='raw':bf.append({'code':code,'name':names[code],'close':float(g.iloc[-1].close_raw),**r})
# User-revised fixed 3050 market index. Individual stock universe remains the
# original dynamic mainboard/non-ST eligibility rules.
from bf_index_3050 import compute
frozen=pd.read_csv('bf_index_universe_3050_20261103.csv',dtype={'code':str}).code.to_list()
marketgate=compute(base,sessions,date,frozen)
for item in potential:
 code=item['code']; sec=('1.' if code.startswith('6') else '0.')+code
 item['market_cap_status']='unverified'
 try:
  params={'secid':sec,'fields':'f43,f57,f58,f86,f116','fltt':2,'invt':2}
  r=requests.get('https://push2.eastmoney.com/api/qt/stock/get',params=params,timeout=20).json()['data']
  ts=datetime.datetime.fromtimestamp(r['f86'],ZoneInfo('Asia/Shanghai'))
  cap=r.get('f116'); close=r.get('f43')
  item['quote_time']=ts.isoformat()
  if (ts.date().isoformat()==date and (ts.hour,ts.minute)>=(15,0)
      and isinstance(cap,(int,float)) and cap>0 and isinstance(close,(int,float))
      and abs(float(close)-item['signal_close_raw'])<.001):
   item['signal_day_total_market_cap_yuan']=float(cap)
   item['market_cap_status']='pass' if cap<30_000_000_000 else 'fail'
 except Exception as e:item['market_cap_error']=str(e)
raw={'asof':date,'universe_today_valid':len(universe),'session_count':len(sessions),'s2_shape_candidates':potential,'s2_cap_verified_signals':[x for x in potential if x['market_cap_status']=='pass'],'bf_raw':bf,'bf_selected_pool':bf_pool(bf),'status_counts':counts,'market_gate_3050':marketgate,
'notes':['TDX OHLCV parsed from daily packages; non-frozen live mainboard universe; missing/suspended candidates not silently filled','S2 does not use historical shares/first-board cap per user; signal-day total market cap below 30 billion must be verified from same-day close','BF upper-price inferred 10% from reference price, not authoritative exchange limit; IPO special cases and candidate status require verification','S2/BF missing_data and rule exceptions may include unscanned candidates; output is preliminary, not exhaustive','BF market gate uses user-revised fixed 3050 universe, not the original frozen 3297; do not compare as identical strategies']}
(out/('tdx_prefilter_'+date.replace('-','')+'.json')).write_text(json.dumps(raw,ensure_ascii=False,indent=2,default=str),encoding='utf-8');print(json.dumps(raw,ensure_ascii=False,indent=2,default=str))
