"""Append-only simulated transaction journal. Never places broker orders.
Execution prices and conditions must be independently validated by caller.
"""
import json,pathlib,datetime,math,decimal
from zoneinfo import ZoneInfo
D=decimal.Decimal
STOCKS={'1','2','3','4'}
def load(path):
 p=pathlib.Path(path);return json.loads(p.read_text(encoding='utf8'))
def save_atomic(path,data):
 p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(p)
def money(x):return D(str(x)).quantize(D('.01'),rounding=decimal.ROUND_HALF_UP)
def apply_trade(state,event):
 """Events represent verified historical fills, NEVER merely orders/signals.
    Caller supplies unique event_id and a disclosure of fill price source.
 """
 required=('event_id','date','strategy','side','code','shares','price','source')
 if any(not event.get(k) for k in required):raise ValueError('missing event fields')
 if any(e['event_id']==event['event_id'] for e in state.get('trades',[])):raise ValueError('duplicate event_id')
 date=datetime.date.fromisoformat(event['date']);start=datetime.date.fromisoformat(state['first_execution_date'])
 if date<start:raise ValueError('fill before first execution')
 previous=state.get('last_applied_event_date')
 if previous and date<datetime.date.fromisoformat(previous):raise ValueError('out-of-order trade requires audited replay')
 sid=str(event['strategy']);code=event['code'];side=event['side'];shares=event['shares'];price=D(str(event['price']))
 if sid not in STOCKS or not isinstance(code,str) or len(code)!=6 or not code.isdigit():raise ValueError('invalid strategy/code')
 if not isinstance(shares,int) or isinstance(shares,bool) or shares<=0 or not price.is_finite() or price<=0:raise ValueError('invalid shares or price')
 account=state['accounts'][sid];positions=account['positions'];p=next((x for x in positions if x['code']==code),None)
 fee=money(price*shares*D('0.0001'));gross=money(price*shares);cash=money(account['cash_cny'])
 if side=='BUY':
  if shares%100:raise ValueError('this simplified mainboard buy requires round lots')
  if p is not None:raise ValueError('no silent duplicate stock holding')
  if cash<gross+fee:raise ValueError('insufficient cash')
  cash-=gross+fee;positions.append({'code':code,'name':event.get('name',''),'shares':shares,'buy_date':event['date'],'buy_price':float(price),'buy_commission_cny':float(fee),'source_strategy':event.get('source_strategy'),'limit_extension_count':int(event.get('limit_extension_count',0))})
 elif side=='SELL':
  if p is None or shares>p['shares']:raise ValueError('no matching position or insufficient shares')
  if date<=datetime.date.fromisoformat(p['buy_date']):raise ValueError('T+1: cannot sell shares on buy day')
  cash+=gross-fee;p['shares']-=shares
  if p['shares']==0:positions.remove(p)
 else:raise ValueError('side must be BUY or SELL')
 account['cash_cny']=float(cash);record={**event,'commission_cny':float(fee),'gross_cny':float(gross),'cash_after_cny':float(cash)};state.setdefault('trades',[]).append(record)
 state['last_applied_event_date']=max(state.get('last_applied_event_date') or '',event['date'])
 return state
