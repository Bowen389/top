"""Strategy-3 revised fixed 3050 constituent market gate; daily-close research only."""
import json, argparse, pathlib, hashlib
import pandas as pd
PREFIX=('600','601','603','605','000','001','002','003')
def compute(prices,sessions,asof,codes):
    sessions=[d for d in sessions if d<=asof]
    if not sessions or sessions[-1]!=asof or len(sessions)<21: raise ValueError('need at least 21 verified market sessions through asof')
    if len(codes)!=len(set(codes)):raise ValueError('duplicate universe codes')
    if any(len(c)!=6 or not c.startswith(PREFIX) for c in codes):raise ValueError('invalid mainboard code')
    frames=[];base=pathlib.Path(prices)
    for d in sessions[-21:]:
        path=base/(d+'.csv')
        if not path.exists():raise ValueError('missing full market session: '+d)
        p=pd.read_csv(path,dtype={'code':str,'date':str})
        if p.duplicated(['date','code']).any():raise ValueError('duplicate stock/day: '+d)
        if set(p.date)!={d}:raise ValueError('date mismatch '+d)
        p=p[p.code.isin(codes)][['date','code','close','volume']].copy()
        p=p[(p.close>0)&(p.volume>0)];frames.append(p)
    daily=pd.concat(frames,ignore_index=True)
    pvt=daily.pivot(index='date',columns='code',values='close').reindex(sessions[-21:])
    # Exactly the preceding MARKET session; a suspended stock cannot skip ahead.
    returns=(pvt / pvt.shift(1)-1).clip(-.11,.11).iloc[-20:]
    counts=returns.notna().sum(axis=1);r=returns.mean(axis=1)
    if r.isna().any():raise ValueError('market index missing an entire day')
    index=(1+r).cumprod();ma=index.mean();final=index.iloc[-1]
    return {'asof':asof,'constituent_pool_size':len(codes),'days':20,'I':float(final),'ma20':float(ma),'deviation_pct':float((final/ma-1)*100),'market_gate_pass':bool(final>=ma),'valid_returns_T':int(counts.iloc[-1]),'constituent_count_each_day':{str(k):int(v) for k,v in counts.items()},'daily_market_returns':{str(k):float(v) for k,v in r.items()},'index_basis':'fixed 2026-11-03 non-ST 3050; historical ST may be included; active and valid prior-market-day return required','source':'TDX raw daily packages; no fill for suspensions','warning':'user-modified research strategy; not the original 3297 frozen universe; no historical backtest validation'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--sessions',default='../reports/sessions_85.json');p.add_argument('--prices',default='../data/prices');p.add_argument('--universe',default='bf_index_universe_3050_20261103.csv');p.add_argument('--asof',default='2026-11-03');p.add_argument('--out',default='../reports/bf_index_3050_20261103.json');a=p.parse_args()
    u=pathlib.Path(a.universe);raw=u.read_bytes();df=pd.read_csv(u,dtype={'code':str});codes=df.code.to_list()
    if len(codes)!=3050:raise ValueError('expected fixed pool of 3050')
    sessions=json.loads(pathlib.Path(a.sessions).read_text())['dates'];r=compute(a.prices,sessions,a.asof,codes);r['universe_sha256']=hashlib.sha256(raw).hexdigest();pathlib.Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(r,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
