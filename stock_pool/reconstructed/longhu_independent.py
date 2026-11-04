"""Independent Eastmoney institutional-seat scanner based on longhu strategy.
Does not read from or import the external repository or its output files.
"""
import argparse,collections,datetime,json,math,pathlib,time
from zoneinfo import ZoneInfo
import requests
import pandas as pd

ENDPOINT='https://datacenter-web.eastmoney.com/api/data/v1/get'
REPORTS={'overview':'RPT_DAILYBILLBOARD_DETAILS','buy':'RPT_BILLBOARD_DAILYDETAILSBUY','sell':'RPT_BILLBOARD_DAILYDETAILSSELL'}
IDENTITY=('SECURITY_CODE','TRADE_ID','CHANGE_TYPE','EXPLANATION')
STABLE_FIELDS={'overview':('SECURITY_CODE','TRADE_DATE','SECURITY_NAME_ABBR','TRADE_ID','CHANGE_TYPE','EXPLANATION','CLOSE_PRICE','CHANGE_RATE','TURNOVERRATE','BUY_SEAT_NEW','SELL_SEAT_NEW','BUY_SEAT','SELL_SEAT'),
'buy':('SECURITY_CODE','TRADE_DATE','TRADE_ID','CHANGE_TYPE','EXPLANATION','OPERATEDEPT_NAME','OPERATEDEPT_CODE','BUY','SELL','NET'),
'sell':('SECURITY_CODE','TRADE_DATE','TRADE_ID','CHANGE_TYPE','EXPLANATION','OPERATEDEPT_NAME','OPERATEDEPT_CODE','BUY','SELL','NET')}

def key(row,date):
 if str(row.get('TRADE_DATE',''))[:10]!=date:raise ValueError('incorrect date')
 vals=[row.get(k) for k in IDENTITY]
 if any(x is None or str(x).strip()=='' for x in vals):raise ValueError('missing disclosure identity')
 return tuple(str(x).strip() for x in vals)
def number(x,nonnegative=False):
 return not isinstance(x,bool) and isinstance(x,(int,float)) and math.isfinite(x) and (not nonnegative or x>=0)
def signature(s):
 return {k:sorted(json.dumps([str(x.get(z)) for z in fields],ensure_ascii=False) for x in s[k]) for k,fields in STABLE_FIELDS.items()}
def fetch_report(report,date):
 rows=[]; page=1;expected=None; pages=None
 while True:
  params={'reportName':report,'columns':'ALL','pageSize':500,'pageNumber':page,'sortColumns':'SECURITY_CODE','sortTypes':'1','filter':"(TRADE_DATE='"+date+"')"}
  r=requests.get(ENDPOINT,params=params,timeout=35,headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'})
  r.raise_for_status();body=r.json();result=body.get('result')
  if not body.get('success') or not isinstance(result,dict):raise RuntimeError('disclosure not available: '+report)
  if expected is None:expected=result.get('count');pages=result.get('pages')
  if result.get('count')!=expected or result.get('pages')!=pages:raise RuntimeError('disclosure changed during pagination')
  rows.extend(result.get('data') or [])
  if page>=pages:break
  page+=1
 if not isinstance(expected,int) or len(rows)!=expected or not rows:raise RuntimeError('empty or incomplete '+report)
 if not any(str(x.get('TRADE_DATE',''))[:10]==date for x in rows):raise RuntimeError('source has no rows for requested date')
 return rows
def fetch_snapshot(date):return {k:fetch_report(report,date) for k,report in REPORTS.items()}

def screen(date,snapshot):
 if not all(isinstance(snapshot.get(x),list) and snapshot[x] for x in ('overview','buy','sell')):raise RuntimeError('empty disclosure source')
 by_code=collections.defaultdict(list)
 for x in snapshot['overview']:by_code[str(x.get('SECURITY_CODE',''))].append(x)
 seat={side:collections.defaultdict(list) for side in ('buy','sell')}
 for side in ('buy','sell'):
  if not any(str(r.get('SECURITY_CODE','')) in by_code for r in snapshot[side]):raise RuntimeError('seat source contains no matching disclosures')
  for r in snapshot[side]:seat[side][str(r.get('SECURITY_CODE',''))].append(r)
 results=[];skipped=[];audit=[]
 for code in sorted(by_code):
  rows=by_code[code];names={str(r.get('SECURITY_NAME_ABBR') or '').strip() for r in rows};name=next(iter(names)) if len(names)==1 else ''
  if len(code)!=6 or not code.isdigit() or not code.startswith(('0','6')):
   skipped.append({'code':code,'name':name,'reason':'outside 0/6 code scope'});continue
  if not name or len(names)!=1:
   skipped.append({'code':code,'name':name,'reason':'missing or conflicting identity'});continue
  if 'ST' in name.upper().replace(' ',''):
   skipped.append({'code':code,'name':name,'reason':'ST excluded'});continue
  try:
   overview={};prices=set()
   for row in rows:
    k=key(row,date)
    if k in overview:raise ValueError('duplicate disclosure identity')
    close=row.get('CLOSE_PRICE');pct=row.get('CHANGE_RATE')
    if not number(close) or close<=0 or not number(pct) or pct<=-100:raise ValueError('invalid close or daily gain')
    prices.add((close,pct));overview[k]=row
   if len(prices)!=1:raise ValueError('conflicting close or daily gain')
   grouped={}
   for side in ('buy','sell'):
    grouping=collections.defaultdict(list)
    for row in seat[side].get(code,[]):grouping[key(row,date)].append(row)
    if set(grouping)!=set(overview):raise ValueError('missing or unexpected disclosure groups: '+side)
    for k,detail in grouping.items():
     over=overview[k];mask=over.get(side.upper()+'_SEAT_NEW')
     if mask is None:mask=over.get(side.upper()+'_SEAT')
     mask=str(mask) if mask is not None else ''
     if not mask or any(c not in '123' for c in mask) or len(mask)!=len(detail):raise ValueError('incomplete seat rows: '+side)
     fingerprints=set()
     for rec in detail:
      dep=rec.get('OPERATEDEPT_NAME');
      if not isinstance(dep,str) or not dep.strip():raise ValueError('missing seat identity')
      required=('BUY','SELL') if code.startswith('0') and side=='buy' else ('BUY',) if side=='buy' else ('SELL',)
      for field in ('BUY','SELL','NET'):
       val=rec.get(field)
       if field in required or val is not None:
        if not number(val,field!='NET'):raise ValueError('invalid seat '+field)
      fp=(rec.get('OPERATEDEPT_CODE'),dep,rec.get('BUY'),rec.get('SELL'),rec.get('NET'))
      if fp in fingerprints:raise ValueError('indistinguishable duplicate seat rows')
      fingerprints.add(fp)
    grouped[side]=grouping
   sold=any(str(r.get('OPERATEDEPT_NAME','')).strip()=='机构专用' for group in grouped['sell'].values() for r in group)
   close,pct=next(iter(prices));qualifying=[]
   for k,over in overview.items():
    buyers=[x for x in grouped['buy'][k] if str(x.get('OPERATEDEPT_NAME','')).strip()=='机构专用']
    reason='institution_on_sell_side' if sold else 'gain_below_5pct' if pct<5 else 'no_qualifying_institution_buy'
    amount=None
    if not sold and pct>=5 and buyers:
     if code.startswith('0'):
      qualified=[x['BUY'] for x in buyers if x['BUY']>20_000_000 and x['SELL']==0]
      if qualified:amount=max(qualified);reason='single_institution_buy_gt20m_sell_zero'
     elif code.startswith('6'):
      total=sum(x['BUY'] for x in buyers)
      if total>20_000_000:amount=total;reason='institution_buy_sum_gt20m'
    rec={'date':date,'code':code,'name':name,'close':close,'gain_pct':pct,'trade_id':k[1],'disclosure_reason':over['EXPLANATION'],'qualifying_amount_yuan':amount,'reason':reason}
    audit.append(rec)
    if amount is not None:qualifying.append(rec)
   if qualifying:results.append(sorted(qualifying,key=lambda r:(-r['qualifying_amount_yuan'],r['trade_id']))[0])
  except (ValueError,KeyError,TypeError) as exc:skipped.append({'code':code,'name':name,'reason':str(exc)})
 for code in sorted((set(seat['buy'])|set(seat['sell']))-set(by_code)):
  if len(code)==6 and code.startswith(('0','6')):skipped.append({'code':code,'name':'','reason':'seat detail without overview'})
 results.sort(key=lambda r:(-r['qualifying_amount_yuan'],r['code']))
 return results,skipped,audit

def main():
 p=argparse.ArgumentParser();p.add_argument('--asof',required=True);p.add_argument('--pause',type=int,default=15);a=p.parse_args();date=a.asof
 output=pathlib.Path('../reports');output.mkdir(parents=True,exist_ok=True);stamp=date.replace('-','');now=datetime.datetime.now(ZoneInfo('Asia/Shanghai'))
 status={'asof':date,'checked_at':now.isoformat(),'source':'东方财富龙虎榜披露及买卖席位，独立抓取','status':'checking','note':'从longhu规则独立实现；不读取其仓库结果和持仓账本'}
 try:
  first=fetch_snapshot(date);time.sleep(a.pause);second=fetch_snapshot(date)
  if signature(first)!=signature(second):raise RuntimeError('disclosure changed between snapshots')
  signals,skips,audit=screen(date,second)
  (output/('longhu_independent_raw_'+stamp+'.json')).write_text(json.dumps(second,ensure_ascii=False,indent=2),encoding='utf8')
  pd.DataFrame(signals,columns=['date','code','name','close','gain_pct','trade_id','disclosure_reason','qualifying_amount_yuan','reason']).to_csv(output/('longhu_independent_signals_'+stamp+'.csv'),index=False,encoding='utf-8-sig')
  pd.DataFrame(skips,columns=['code','name','reason']).to_csv(output/('longhu_independent_skips_'+stamp+'.csv'),index=False,encoding='utf-8-sig')
  pd.DataFrame(audit).to_csv(output/('longhu_independent_audit_'+stamp+'.csv'),index=False,encoding='utf-8-sig')
  status.update(status='provisional',source_counts={k:len(v) for k,v in second.items()},signals=signals,skipped=len(skips),warning='两次抓取一致不代表交易所当天全部披露已完成')
 except Exception as e:
  status.update(status='waiting_for_disclosure',error=str(e),warning='读取失败或数据变化，不等于无信号')
 (output/('longhu_independent_status_'+stamp+'.json')).write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(status,ensure_ascii=False,indent=2))
 if status['status']!='provisional':raise RuntimeError(status['error'])
if __name__=='__main__':main()
