"""V3 daily stock signal from user's frozen 969 pool; no orders or fills."""
import json,pathlib,sys,requests
import pandas as pd
from scanners import v3
import market_data
base=market_data.ROOT;out=pathlib.Path('../reports');c=json.loads((out/'sessions_85.json').read_text());sessions=c['dates'];date=c['asof']
if c['missing'] or sessions[-1]!=date:raise RuntimeError('missing/incorrect sessions')
u=pd.read_csv('../strategy1/strategy1_universe_969.csv',dtype={'代码':str});codes=u['代码'].tolist()
if len(codes)!=969 or len(set(codes))!=969:raise RuntimeError('frozen universe validation failed')
df=pd.concat([pd.read_csv(base/(d+'.csv'),dtype={'code':str}) for d in sessions],ignore_index=True);df=df[df.code.isin(codes)];df=df[(df.open>0)&(df.close>0)&(df.prev_close>0)&(df.volume>0)];df=market_data.prepare(df);df.rename(columns={'adj_close':'close_adj'},inplace=True)
groups={c:g for c,g in df.groupby('code')};today=df[df.date==date].set_index('code');signals=[];status={}
for code in codes:
 if code not in today.index: r={'status':'missing_or_inactive_T'}
 elif 'ST' in str(today.loc[code,'name']).upper():r={'status':'excluded_ST'}
 elif '退' in str(today.loc[code,'name']):r={'status':'excluded_delisting'}
 else:
  g=groups[code].set_index('date').reindex(sessions).reset_index(names='date');r=v3(g,True)
 status[r['status']]=status.get(r['status'],0)+1
 if r.get('strength',0)>0:signals.append({'code':code,'name':str(today.loc[code,'name']),'signal_close_raw':float(today.loc[code,'close']),**r})
signals.sort(key=lambda x:(-x['strength'],-x['gain'],x['code']))
url='https://quotes.sina.cn/cn/api/jsonp.php/var%20x=/CN_MarketData.getKLineData'
r=requests.get(url,params={'symbol':'sh000300','scale':'240','ma':'no','datalen':'120'},timeout=30);r.raise_for_status();body=r.text[r.text.index('([')+1:r.text.rindex('])')+1];index=[x for x in json.loads(body) if x['day']<=date];mkt={'status':'unverified'}
if len(index)>=20 and index[-1]['day']==date:
 last=index[-20:];cl=[float(x['close']) for x in last];ma=sum(cl)/20;mkt={'status':'ok','source':'Sina CSI300 daily','date':date,'close':cl[-1],'ma20':ma,'gate_allow':cl[-1]>=ma,'deviation_pct':100*(cl[-1]/ma-1)}
result={'asof':date,'pool':969,'status_counts':status,'market_gate':mkt,'raw_candidates':signals,'notes':['20-day market gate includes T','60-day stock lookback excludes T: documented reconstruction assumption','status missing means not verified, not no signal','next open gap, limit and trade status unknown','No order, fill or account ledger is created']}
(out/('v3_prefilter_'+date.replace('-','')+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'asof':date,'pool':969,'counts':status,'raw_candidates':len(signals),'market_gate':mkt},ensure_ascii=False))
