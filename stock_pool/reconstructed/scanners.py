"""Text-derived signal core, not a certified reproduction. No brokerage access.
All functions operate on per-stock data ALIGNED TO MARKET SESSIONS. Missing rows
remain NaN; callers must never forward-fill suspended stocks.
"""
import math
import numpy as np
import pandas as pd

MAIN_PREFIX=('600','601','603','605','000','001','002','003')
def mainboard(code): return str(code).zfill(6).startswith(MAIN_PREFIX)

def required(df, cols, n):
    if len(df)<n or any(c not in df.columns for c in cols): return False
    x=df.iloc[-n:][list(cols)]
    return not x.isna().any().any()

def v3(df, eligible):
    """Signal-day scan. eligible must be supplied by the original 969-universe logic.
    60-day high and volume averages exclude signal day, a disclosed assumption.
    Uses already price-adjusted close and same-unit volume throughout.
    """
    if eligible is not True: return {'status':'unverified_universe' if eligible is None else 'excluded'}
    if not required(df,['close_adj','volume'],61): return {'status':'missing_data'}
    c=df.close_adj.astype(float);v=df.volume.astype(float)
    if (c.iloc[-2]<=0 or (v.iloc[-61:-1]<=0).any()): return {'status':'invalid_data'}
    gain=c.iloc[-1]/c.iloc[-2]-1
    cond1=gain>.02 and v.iloc[-1]>2*v.iloc[-6:-1].mean()
    high=c.iloc[-1]>=c.iloc[-61:-1].max()
    cond2=high and v.iloc[-1]>1.5*v.iloc[-61:-1].mean()
    cond3=high and v.iloc[-1]>1.2*v.iloc[-61:-1].mean()
    return {'status':'ok','gain':gain,'strength':int(cond1)+int(cond2)+int(cond3),'conditions':[bool(cond1),bool(cond2),bool(cond3)]}

def s2(df, code):
    """S2 trigger scan; relative prices use consistent adjusted prices.
    First-board date is T-7, T-8 or T-9. Missing core data never means no signal.
    Rate uses correct ex-right reference. Market cap must be checked on signal day
    by the caller; historical shares and first-board cap are not considered.
    Original event-table implementation is unavailable; no exact-equivalence claim.
    """
    if not mainboard(code): return {'status':'excluded'}
    # Historical shares/first-board capitalization are deliberately not tested;
    # the caller verifies the signal-day market cap independently.
    fields=['open_adj','close_adj','volume','pct','is_st']
    if not required(df,fields,20): return {'status':'missing_data'}
    x=df.iloc[-20:].reset_index(drop=True)
    if bool(x.iloc[-1].is_st): return {'status':'excluded_st'}
    for first in (len(x)-10,len(x)-9,len(x)-8):
        f=x.iloc[first]; div=x.iloc[first+1]; adj=x.iloc[first+2:first+7]
        win=x.iloc[first+7:first+10]
        if len(adj)!=5 or len(win)<1: continue
        # Restrict trigger to the current date and to the first valid restart.
        if len(win)!=len(x)-first-7: continue
        hist=x.iloc[first-10:first]
        if len(hist)!=10: return {'status':'missing_data'}
        vs=adj.volume.astype(float).to_numpy()
        if min(vs)<=0 or min(x.iloc[first-5:first].volume)<=0: return {'status':'invalid_data'}
        slope=float(np.polyfit(np.arange(5), np.log(vs), 1)[0])
        conditions=(float(f.pct)>=9.85 and float(x.iloc[first-1].pct)<9.85
                    and (hist.pct.astype(float)<9.85).all()
                    and float(f.volume)>=2*float(x.iloc[first-5:first].volume.mean())
                    and float(div.pct)<9.85 and float(div.pct)<=5
                    and float(div.volume)>=.8*float(f.volume)
                    and float(vs.mean())<=.6*float(div.volume) and slope<0
                    and (adj.close_adj.astype(float)>=.97*float(f.close_adj)).all())
        if not conditions: continue
        hits=win[(win.close_adj>win.open_adj)&(win.volume>=1.5*vs.mean())]
        if len(hits) and hits.index[0]==len(x)-1:
            if float(x.iloc[-1].pct)>=2:
                return {'status':'triggered','first_date':str(f.get('date','')),'trigger_pct':float(x.iloc[-1].pct),'support_adjusted':.97*float(f.close_adj)}
    return {'status':'no_trigger'}

def bf_raw(df, code):
    """BF/G3 original signal and static scores only; NOT the market gate or execution."""
    if not mainboard(code): return {'status':'excluded'}
    cols=['open_raw','high_raw','close_raw','upper','prev_ref','factor','amount','is_st','volume']
    # 60 valid bars can span more than 60 market sessions; only the S/limit
    # windows must be contiguous market sessions. Never fill suspended bars.
    if len(df)<21 or any(c not in df.columns for c in cols): return {'status':'missing_data'}
    valid=df[['open_raw','close_raw','volume']].dropna()
    valid=valid[(valid.open_raw>0)&(valid.close_raw>0)&(valid.volume>0)]
    if len(valid)<60 or not required(df,cols,21): return {'status':'missing_data'}
    x=df.reset_index(drop=True);t=x.iloc[-1];p=x.iloc[-2]
    if bool(t.is_st) or t.volume<=0 or t.close_raw<3: return {'status':'excluded_basic'}
    prev20=x.iloc[-21:-1]
    if (prev20.volume<=0).any(): return {'status':'missing_s_window'}
    S=float(prev20.amount.mean())
    if S<30_000_000: return {'status':'excluded_liquidity'}
    limit=lambda a:abs(float(a.close_raw)-float(a.upper))<.001
    z=x.iloc[-7:-2]; cnt=sum(limit(r) for _,r in z.iterrows())
    broken=abs(float(p.high_raw)-float(p.upper))<.001 and not limit(p)
    down=float(p.close_raw)<float(p.open_raw)
    engulf=float(t.close_raw*t.factor)>max(float(p.open_raw*p.factor),float(p.close_raw*p.factor))
    if not(limit(t) and (down or broken) and cnt>=1 and engulf): return {'status':'no_raw_signal'}
    bits=[int(cnt==1),int(S>=250_000_000),int(t.open_raw>=t.prev_ref)];score=sum(bits)
    return {'status':'raw','S':S,'prior_limits':cnt,'score_bits':bits,'score':score,
            'category':'A' if score==3 else 'B' if score==2 else 'discard',
            'previous_broken_limit':bool(broken),'bf_static_ok':bool(S>=200_000_000 and not broken)}

def bf_pool(rows):
    """Choose A before any execution filtering; never fall back to B after A selected."""
    a=[r for r in rows if r.get('category')=='A'];b=[r for r in rows if r.get('category')=='B']
    return sorted(a if a else b,key=lambda r:(-r['S'],str(r['code']).zfill(6)))

def bf_budget(cash, positions):
    return min(100000,max(0,cash-max(0,4-positions-1)*1000))

def g3_budget(cash): return min(50000,max(0,cash-100000))
