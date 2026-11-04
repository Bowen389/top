"""Check market-source limit prices against reconstructed normal-board limits.
Only usable while provider quotes still correspond to as-of date.
"""
import datetime,requests,concurrent.futures,json,sys,pathlib
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
import pandas as pd
from scanners import bf_raw,mainboard
import market_data
out=pathlib.Path('../reports');data=json.loads((out/'sessions_85.json').read_text());dates=data['dates'];T=dates[-1];base=market_data.ROOT
if data['missing'] or data['asof']!=T:raise ValueError('missing market sessions')
df=pd.concat([pd.read_csv(base/(d+'.csv'),dtype={'code':str}) for d in dates],ignore_index=True);df=df[df.code.map(mainboard)&(df.volume>0)&(df.open>0)&(df.close>0)&(df.prev_close>0)].copy();df=market_data.prepare(df)
df['upper']=[float((Decimal(str(x))*Decimal('1.10')).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) for x in df.prev_close];df['is_st']=df.name.str.contains('ST',case=False,na=False,regex=False)
df=df.rename(columns={'open':'open_raw','high':'high_raw','close':'close_raw','prev_close':'prev_ref'});today=df[(df.date==T)&(~df.is_st)&(~df.name.str.contains('退',na=False))&(df.close_raw>=3)].copy()
# Ordinary mainboard securities with >=60 valid bars have standard limits unless exceptional status: validate current limit via another provider.
cap=today[(today.close_raw-today.upper).abs()<.001];groups={code:g for code,g in df.groupby('code')}
def quote(row):
 code=row['code'];sec=('1.' if code.startswith('6') else '0.')+code
 try:
  p={'secid':sec,'fields':'f43,f51,f52,f57,f58,f60,f86','fltt':'2','invt':'2'};j=requests.get('https://push2.eastmoney.com/api/qt/stock/get',params=p,timeout=20).json()['data']
  ts=datetime.datetime.fromtimestamp(j['f86'],ZoneInfo('Asia/Shanghai'));valid=ts.date().isoformat()==T and (ts.hour,ts.minute)>=(15,0)
  if not valid:return {'quote_status':'wrong_date_or_intraday','quote_timestamp':ts.isoformat()}
  fields=[j.get(k) for k in ['f43','f51','f52','f60']]
  if any(not isinstance(v,(int,float)) for v in fields):return {'quote_status':'missing_price','quote_timestamp':ts.isoformat()}
  match=abs(float(j['f43'])-row['close_raw'])<.001 and abs(float(j['f51'])-row['upper'])<.001 and abs(float(j['f60'])-row['prev_ref'])<.001
  return {'quote_status':'verified' if match else 'mismatch','quote_timestamp':ts.isoformat(),'actual_upper_T':j['f51'],'actual_lower_T':j['f52'],'actual_prev_ref_T':j['f60'],'actual_close_T':j['f43']}
 except Exception as e:return {'quote_status':'fetch_failure','error':str(e)}
rows=cap.to_dict('records');records=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
 results=list(pool.map(quote,rows))
for a,q in zip(rows,results):
 code=a['code'];g=groups[code].set_index('date').reindex(dates).reset_index(names='date');recent=g.iloc[-7:];unverified=recent.is_st.isna().any() or recent.is_st.eq(True).any() or recent.name.fillna('').str.match(r'^[NC]').any()
 if unverified:r={'status':'recent_st_missing_or_ipo_exception'}
 else:r=bf_raw(g,code)
 records.append({'code':code,'name':a['name'],'close':float(a['close_raw']),'reference':float(a['prev_ref']),'estimated_up':float(a['upper']),'recent_seven_dates':recent.date.tolist(),'recent_seven_names':recent.name.fillna('MISSING').tolist(),'valid_bars_85':int(g.volume.gt(0).sum()),'outcome':r,**q})
unknown=[r for r in records if r['quote_status']!='verified' or r['outcome']['status'] in ('missing_data','recent_st_missing_or_ipo_exception')]
result={'asof':T,'candidate_scope':'主板、非ST、有效交易、收盘>=3、按PC 10%初筛收盘涨停；IPO/ST/停牌等特殊情况另审','prefilter_n':len(records),'verified_T_upper':sum(r['quote_status']=='verified' for r in records),'unverified_T_upper':sum(r['quote_status']!='verified' for r in records),'outcome_counts':pd.Series([r['outcome']['status'] for r in records]).value_counts().to_dict(),'unknown':unknown,'records':records,'notes':['真实今日涨跌停价取东方财富行情接口f51/f52；复核时间必须为信号日15点后。','历史涨停按当日参考前收10%推算，仅用于已核验普通非ST历史交易日；历史ST、异常状态、停牌或无报价不强行套用。','结果不自动判定全市场完备；当日非ST的异常限价证券需另查。']}
(out/('bf_limit_audit_'+T.replace('-','')+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False,indent=2,default=str))
