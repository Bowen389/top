"""Generate a dated plan, settle yesterday's plan with today's public bars."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "stock_pool/reconstructed"
REPORTS = ROOT / "reports"
sys.path.insert(0, str(CORE))
import market_data
import longhu_independent as lh
from extra import fb_dual, n3_base, n3_candidates
from paper import settle, write_csv

STRATEGIES = {
    "v3": ("V3 动量突破", "开盘涨幅 −5% 至 +6%，涨跌停及停牌跳过；最多5只"),
    "s2": ("S2 标准5日", "开盘低于信号收盘价×1.0985，停牌或锁板跳过；最多4仓"),
    "bf": ("BF＋G3＋Tail1000（3050）", "开盘先判BF，再判G3；A池存在时不降B池"),
    "lh": ("龙虎榜机构席位", "开盘涨跌停或停牌跳过；单票不超过权益1/3"),
    "n3": ("龙虎榜 N3 强化", "开盘不低于−5%，排除涨停开盘；限价不超过开盘×1.005"),
    "fb": ("FB_DUAL_K4", "C2缺口[−2.5%, +5%]，C1缺口[−5%, −2%]；C2优先"),
}
SINA = "https://quotes.sina.cn/cn/api/jsonp.php/var%20x=/CN_MarketData.getKLineData"


def index(symbol):
    response = requests.get(SINA, params={"symbol": symbol, "scale": 240,
                            "ma": "no", "datalen": 150}, timeout=25)
    response.raise_for_status()
    body = response.text
    return {r["day"]: float(r["close"])
            for r in json.loads(body[body.index("([")+1:body.rindex("])")+1])}


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else default


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                         encoding="utf8")
    temporary.replace(path)


def bars(day):
    path = market_data.ROOT / (day + ".csv")
    frame = pd.read_csv(path, dtype={"code": str})
    if set(frame.date) != {day} or frame.code.duplicated().any():
        raise ValueError(f"{day}: invalid daily package")
    return frame.set_index("code").to_dict("index")


def package(days):
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(market_data.package, d): d for d in days}
        failures = {}
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                failures[futures[future]] = str(exc)
    return failures


def run(script):
    subprocess.run([sys.executable, script], cwd=CORE, check=True, timeout=450)


def market_scans(day, days, indices, add):
    failures = package(days[-120:])
    window = days[-85:]
    cache = CORE.parent / "reports"
    write(cache / "sessions_85.json", dict(asof=day, dates=window,
                                           missing=[d for d in window if d in failures]))
    stamp = day.replace("-", "")
    if any(d in failures for d in window):
        for key in ("v3", "s2", "bf"):
            add(key, error=f"通达信日包缺失：{[d for d in window if d in failures]}")
    else:
        def v3():
            run("scan_v3.py")
            data = read(cache / f"v3_prefilter_{stamp}.json")
            gate = data["market_gate"]
            if gate["status"] != "ok":
                raise ValueError("沪深300 MA20未核验")
            add("v3", data["raw_candidates"] if gate["gate_allow"] else [], meta=gate)

        def s2_bf():
            run("scan_live_tdx.py")
            data = read(cache / f"tdx_prefilter_{stamp}.json")
            unknown = [r for r in data["s2_shape_candidates"] if r["market_cap_status"] == "unverified"]
            add("s2", sorted(data["s2_cap_verified_signals"],
                             key=lambda r: (-r["trigger_pct"], r["code"])),
                meta={"market_cap_unverified": unknown, "scan_counts": data["status_counts"]["s2"]}, provisional=bool(unknown))
            selected = data["bf_selected_pool"]
            if selected:
                run("audit_bf_limits.py")
                audit = read(cache / f"bf_limit_audit_{stamp}.json")
                verified = {r["code"] for r in audit["records"] if r["quote_status"] == "verified"}
                if any(r["code"] not in verified for r in selected):
                    add("bf", error="反包池存在尚未核验的涨停价，等待复核")
                    return
            add("bf", selected, meta=data["market_gate_3050"])

        for keys, function in [(("v3",), v3), (("s2", "bf"), s2_bf)]:
            try:
                function()
            except Exception as exc:
                for key in keys:
                    add(key, error=str(exc))
    try:
        if failures:
            raise ValueError(f"120日上市资格窗口有缺失：{list(failures)}")
        csi1000 = index("sh000852")
        rows, meta = fb_dual(days, [csi1000[d] for d in days[-20:]])
        add("fb", rows, meta=meta)
    except Exception as exc:
        add("fb", error=str(exc))


def dragon_scans(day, previous, now, add):
    if now.time() < dt.time(17, 10):
        for key in ("lh", "n3"):
            add(key, error="17:10后复核龙虎榜披露")
        return
    try:
        current = lh.fetch_snapshot(day)
        time.sleep(15)
        confirmed = lh.fetch_snapshot(day)
        if lh.signature(current) != lh.signature(confirmed):
            raise ValueError("两次披露快照不一致")
        rows, skipped, _ = lh.screen(day, confirmed)
        add("lh", rows, meta={"isolated_rows": skipped}, provisional=True)
    except Exception as exc:
        for key in ("lh", "n3"):
            add(key, error=str(exc))
        return
    try:
        last = lh.fetch_snapshot(previous)
        market_data.package(previous)
        market_data.package(day)
        base = n3_base(day, confirmed, bars(day))
        before = n3_base(previous, last, bars(previous))
        add("n3", n3_candidates(base, before), meta={"base_count": len(base)}, provisional=True)
    except Exception as exc:
        add("n3", error=str(exc))


def publish(plan, book, note):
    day = plan["date"]
    lines = [f"# {day} 六策略盘后预案", "", f"次一交易日：{plan['next_session']}。{note}", "",
             "买点均为条件计划。逐笔成交为模拟。", ""]
    for key, (name, rule) in STRATEGIES.items():
        section = plan["strategies"][key]
        lines += [f"## {name}", "", f"次日买点：{rule}。", ""]
        if section.get("error"):
            lines += [f"数据待核验：{section['error']}", ""]
        else:
            rows = section["candidates"]
            status = "暂定" if section.get("provisional") else "已扫描"
            if section["meta"].get("market_cap_unverified"):
                lines.append(f"另有 {len(section['meta']['market_cap_unverified'])} 个市值待核验。")
            lines += [f"{status} · {len(rows)} 个候选", ""]
            for row in rows:
                detail = (f" · {row['label']}" if "label" in row else
                          f" · {row['category']}" if "category" in row else
                          f" · {'C2' if row['pos60_med'] < .7 else 'C1'}"
                          if "pos60_med" in row else "")
                lines.append(f"- {row['code']} {row['name']}{detail}")
            lines.append("")
        positions = book["accounts"][key]["positions"]
        if positions:
            lines += ["持仓：" + "、".join(f"{p['code']}（{p['due']} 到期）"
                                            for p in positions.values()), ""]
    md = "\n".join(lines)
    (REPORTS / f"{day}.md").write_text(md, encoding="utf8")
    (REPORTS / "latest.md").write_text(md, encoding="utf8")
    write(REPORTS / f"{day}.json", plan)
    write(REPORTS / "book.json", book)
    write_csv(REPORTS / "signals.csv", book, plan)


def main():
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date().isoformat()
    if now.weekday() >= 5 or now.time() < dt.time(15, 5):
        return
    from exchange_calendars import get_calendar
    cal = get_calendar("XSHG")
    if not cal.is_session(day):
        return
    note = ""
    book = read(REPORTS / "book.json", {"accounts": {
        key: {"cash": 200000, "positions": {}} for key in STRATEGIES},
        "events": [], "last_session": None})
    next_session = cal.next_session(day).date().isoformat()
    plan = read(REPORTS / f"{day}.json", dict(date=day, next_session=next_session,
                                             strategies={}))
    plan["updated_at"] = now.isoformat()

    def add(key, rows=None, *, meta=None, error=None, provisional=False):
        old = plan["strategies"].get(key, {})
        if error and old.get("error", True) is None:
            old["retry_error"] = error
            return
        plan["strategies"][key] = dict(candidates=rows or [], meta=meta or {},
                                       error=error, provisional=provisional)

    previous = cal.previous_session(day).date().isoformat()
    try:
        indices = index("sh000300")
        days = sorted(d for d in indices if d <= day)
        if not days or days[-1] != day:
            raise ValueError("沪深300尚未提供今日收盘日线")
        previous = days[-2]
        market_scans(day, days, indices, add)
    except Exception as exc:
        for key in ("v3", "s2", "bf", "fb"):
            add(key, error=str(exc))
    dragon_scans(day, previous, now, add)

    # Reprocess a pending market session before advancing past it.
    if book["last_session"] != day:
        try:
            first = book["last_session"] or previous
            for session in cal.sessions_in_range(first, day):
                d = session.date().isoformat()
                if d <= first:
                    continue
                prior = cal.previous_session(d).date().isoformat()
                old = read(REPORTS / f"{prior}.json", {"strategies": {}})
                market_data.package(d)
                settle(book, old, d, cal, bars(d))
                book["last_session"] = d
        except Exception as exc:
            note = f"模拟账本待更新：{exc}"
    publish(plan, book, note)


if __name__ == "__main__":
    main()
