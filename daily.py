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


def market_scans(day, days, add, needed):
    if not needed:
        return
    failures = package(days[-120:] if "fb" in needed else days[-85:])
    window = days[-85:]
    cache = CORE.parent / "reports"
    write(cache / "sessions_85.json", dict(asof=day, dates=window,
                                           missing=[d for d in window if d in failures]))
    stamp = day.replace("-", "")
    if any(d in failures for d in window):
        for key in ("v3", "s2", "bf"):
            if key not in needed:
                continue
            add(key, error=f"通达信日包缺失：{[d for d in window if d in failures]}")
    else:
        def v3():
            run("scan_v3.py")
            data = read(cache / f"v3_prefilter_{stamp}.json")
            gate = data["market_gate"]
            add("v3", data["raw_candidates"] if gate.get("gate_allow") else [],
                meta={**gate, "raw_candidates": data["raw_candidates"],
                      "scan_counts": data["status_counts"]},
                error="沪深300 MA20未核验" if gate["status"] != "ok" else None)

        def s2_bf():
            run("scan_live_tdx.py")
            data = read(cache / f"tdx_prefilter_{stamp}.json")
            unknown = [r for r in data["s2_shape_candidates"] if r["market_cap_status"] == "unverified"]
            add("s2", sorted(data["s2_cap_verified_signals"],
                             key=lambda r: (-r["trigger_pct"], r["code"])),
                meta={"market_cap_unverified": unknown, "scan_counts": data["status_counts"]["s2"]}, provisional=bool(unknown))
            selected = data["bf_selected_pool"]
            if "bf" not in needed:
                return
            meta = {**data["market_gate_3050"], "raw_candidates": data["bf_raw"],
                    "selected_pool": selected, "scan_counts": data["status_counts"]["bf"]}
            if selected:
                run("audit_bf_limits.py")
                audit = read(cache / f"bf_limit_audit_{stamp}.json")
                verified = {r["code"] for r in audit["records"] if r["quote_status"] == "verified"}
                if any(r["code"] not in verified for r in selected):
                    meta["unverified"] = [r["code"] for r in selected if r["code"] not in verified]
                    add("bf", meta=meta, error="反包池存在尚未核验的涨停价，等待复核")
                    return
            add("bf", selected, meta=meta)

        for keys, function in [(("v3",), v3), (("s2", "bf"), s2_bf)]:
            if not needed.intersection(keys):
                continue
            try:
                function()
            except Exception as exc:
                for key in keys:
                    add(key, error=str(exc))
    if "fb" not in needed:
        return
    try:
        if failures:
            raise ValueError(f"120日上市资格窗口有缺失：{list(failures)}")
        csi1000 = index("sh000852")
        rows, meta = fb_dual(days, [csi1000[d] for d in days[-20:]])
        add("fb", rows, meta=meta)
    except Exception as exc:
        add("fb", error=str(exc))


def dragon_scans(day, previous, add):
    try:
        current = lh.fetch_snapshot(day)
        time.sleep(15)
        confirmed = lh.fetch_snapshot(day)
        if lh.signature(current) != lh.signature(confirmed):
            raise ValueError("两次披露快照不一致")
        rows, skipped, audit = lh.screen(day, confirmed)
    except Exception as exc:
        for key in ("lh", "n3"):
            add(key, error=str(exc))
        return
    if not rows:
        add("lh", [], meta={"isolated_rows": skipped, "seat_audit": audit})
        add("n3", [])
        return
    n3_error = None
    try:
        last = lh.fetch_snapshot(previous)
        market_data.package(previous)
        market_data.package(day)
        base = n3_base(day, confirmed, bars(day))
        before = n3_base(previous, last, bars(previous))
        n3 = {r["code"]: r["label"] for r in n3_candidates(base, before)}
    except Exception as exc:
        n3, n3_error = {}, str(exc)
    for row in rows:
        code = row["code"]
        row["n3_tag"] = ("待核验" if n3_error else n3[code] if code in n3 else
                         "未通过N3基础席位或有效成交条件" if code not in base else
                         "前一交易日已入基础池" if code in before else
                         "涨幅位于[7%, 9%)" if 7 <= base[code]["gain_pct"] < 9 else
                         "换手率未超过10%")
        row["volume_tag"] = "待核验"
    add("lh", rows, meta={"isolated_rows": skipped, "seat_audit": audit, "n3_error": n3_error},
        provisional=True)
    # Existing N3 positions continue to settle. New candidates enter only the base flow.
    add("n3", [])


def add_volume_tags(section, days, package):
    rows = section.get("candidates", [])
    if not rows:
        return
    window = days[-150:]
    failures = package(window)
    codes = {row["code"] for row in rows}
    panels = []
    for day in window:
        if day in failures:
            continue
        frame = pd.read_csv(market_data.ROOT / (day + ".csv"), dtype={"code": str})
        if set(frame.date) != {day} or frame.code.duplicated().any():
            failures[day] = "日包日期或代码异常"
            continue
        panels.append(frame[frame.code.isin(codes) & frame.open.gt(0) & frame.volume.gt(0)])
    panel = pd.concat(panels) if panels else pd.DataFrame(columns=["code", "date"])
    for row in rows:
        history = panel[panel.code == row["code"]].sort_values("date").tail(121)
        if len(history) < 121 or history.date.iloc[-1] != days[-1] or any(
                day >= history.date.iloc[0] for day in failures):
            row["volume_tag"] = "待核验：有效日线不足或窗口缺档"
            continue
        adjusted = market_data.prepare(history)
        if len(adjusted) != 121:
            row["volume_tag"] = "待核验：复权数据不足"
            continue
        prior, current = adjusted.iloc[:-1], adjusted.iloc[-1]
        mean_volume, low = prior.volume.tail(20).mean(), prior.adj_low.min()
        if mean_volume <= 0 or low <= 0:
            row["volume_tag"] = "待核验：分母无效"
            continue
        ratio, multiple = current.volume / mean_volume, current.adj_close / low
        row["volume_ratio_20"], row["low_multiple_120"] = float(ratio), float(multiple)
        rejected = [label for failed, label in [(ratio >= 3, "量比≥3"), (multiple >= 4, "低点倍数≥4")] if failed]
        row["volume_tag"] = "未通过：" + "、".join(rejected) if rejected else "符合量价"
    section["provisional"] = bool(section["meta"].get("n3_error")) or any(
        row["volume_tag"].startswith("待核验") for row in rows)


def audit_lines(key, section):
    meta = section["meta"]
    if key == "v3" and "raw_candidates" in meta:
        yield "排序：触发项数降序，其次信号日涨幅降序，再按股票代码升序。展示三项全中及两项前3。"
        allowed = meta.get("gate_allow")
        gate = "通过" if allowed else "门控待核验" if allowed is None else "门控拦截"
        if allowed is not None:
            yield f"沪深300收盘 {meta['close']:.2f} / MA20 {meta['ma20']:.2f}（偏离 {meta['deviation_pct']:+.2f}%）：{gate}。"
        if not allowed:
            for rank, row in v3_display_rows(meta["raw_candidates"]):
                yield f"- 形态第 {rank}：{row['code']} {row['name']} · {priority(key, row)} · {gate}"
    if key == "fb" and "B_MA20" in meta:
        deviation = meta["B_MA20"]
        decision = "通过" if deviation < 0 else "门控拦截，当日不开新仓"
        yield f"中证1000相对MA20偏离 {deviation:+.4%}。规则要求严格低于MA20：{decision}。"
        if deviation >= 0:
            yield "当前0候选由指数门控直接返回，个股首板形态未继续筛选。"
    if key == "bf":
        yield "BF个股盘后条件：20日均成交额至少2亿元，且前一日没有出现触板未封。前一日触板未封不计入A/B评分，但取消BF资格，保留G3资格。BF还需通过市场门控及次日开盘条件。"
    if key == "bf" and "selected_pool" in meta:
        gate = "通过，次日先判BF" if meta["market_gate_pass"] else "未通过，按原规则走G3"
        yield f"自建指数 {meta['I']:.4f} / MA20 {meta['ma20']:.4f}（偏离 {meta['deviation_pct']:+.2f}%）：{gate}。"
        if section.get("error"):
            for rank, row in enumerate(meta["selected_pool"], 1):
                yield f"- 待核验第 {rank}：{row['code']} {row['name']} · {priority(key, row)}"
        selected = {r["code"] for r in meta["selected_pool"]}
        for row in meta["raw_candidates"]:
            if row["code"] not in selected:
                why = "已有A档，B档不参与" if row["category"] == "B" else "评分不足2分"
                yield f"- 排除：{row['code']} {row['name']} · {why} · {priority(key, row)}"
    if key == "lh":
        for field, label in [("n3_error", "N3"), ("volume_error", "量价")]:
            if meta.get(field):
                yield f"{label}待核验：{meta[field]}"
    if key == "s2" and meta.get("scan_counts"):
        counts = meta["scan_counts"]
        yield f"未触发 {counts.get('no_trigger', 0)} 只，排除 ST {counts.get('excluded_st', 0)} 只，历史日线不全 {counts.get('missing_data', 0)} 只。"
    elif meta.get("scan_counts"):
        yield "扫描状态汇总：" + "、".join(f"{k} {v}" for k, v in meta["scan_counts"].items())


def v3_display_rows(rows):
    two_hits = 0
    for rank, row in enumerate(rows, 1):
        if row["strength"] == 3:
            yield rank, row
        elif row["strength"] == 2 and two_hits < 3:
            yield rank, row
            two_hits += 1


def priority(key, row):
    if key == "lh":
        fields = [f"合格机构买入 {row['qualifying_amount_yuan']/1e8:.2f}亿",
                  f"N3判定：{row.get('n3_tag', '待核验')}", row.get("volume_tag", "待核验")]
        if "volume_ratio_20" in row:
            fields.append(f"量比 {row['volume_ratio_20']:.2f} / 低点倍数 {row['low_multiple_120']:.2f}")
        return " · ".join(fields)
    if key == "v3":
        names = ("涨幅与5日放量", "60日突破且放量1.5倍", "60日突破且放量1.2倍")
        triggers = "、".join(name for name, passed in zip(names, row["conditions"]) if passed)
        return f"触发 {row['strength']} 项（{triggers}），涨幅 {row['gain']:+.2%}"
    if key == "bf":
        blocks = []
        if row["S"] < 2e8:
            blocks.append("20日均额不足2亿")
        if row["previous_broken_limit"]:
            blocks.append("前一日触板未封")
        route = "满足BF个股盘后条件" if not blocks else "BF排除、保留G3资格：" + "、".join(blocks)
        names = ("前6至前2日仅1次涨停", "20日均额≥2.5亿", "信号日未低开")
        points = "、".join(name for name, earned in zip(names, row["score_bits"]) if earned)
        return f"{row['category']}档 {row['score']}分（{points or '无得分项'}），20日均额 {row['S']/1e8:.2f}亿 · {route}"
    if key == "s2":
        return f"首板 {row['first_date']}，信号涨幅 {row['trigger_pct']:+.2f}%，市值已核验"
    kind = "C2" if row["pos60_med"] < .7 else "C1"
    return f"{kind}，位置 {row['pos60_med']:.3f}，同档待开盘缺口排序"


def publish(plan, book, note):
    day = plan["date"]
    lines = [f"# {day} 盘后策略预案", "", f"次一交易日：{plan['next_session']}。{note}", "",
             "买点均为条件计划。逐笔成交为模拟。", ""]
    for key in ("lh", "v3", "s2", "bf", "fb"):
        name, rule = STRATEGIES[key]
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
            ranked = v3_display_rows(rows) if key == "v3" else enumerate(rows, 1)
            for rank, row in ranked:
                prefix = "待开盘排序" if key == "fb" else f"第 {rank}"
                lines.append(f"- {prefix}：{row['code']} {row['name']} · {priority(key, row)}")
            lines.append("")
        lines.extend(audit_lines(key, section))
        lines.append("")
        positions = book["accounts"][key]["positions"]
        if positions:
            lines += ["持仓：" + "、".join(f"{code}（{p['due']} 到期）"
                                            for code, p in positions.items()), ""]
    md = "\n".join(lines)
    (REPORTS / f"{day}.md").write_text(md, encoding="utf8")
    (REPORTS / "latest.md").write_text(md, encoding="utf8")
    write(REPORTS / f"{day}.json", plan)
    write(REPORTS / "book.json", book)
    write_csv(REPORTS / "signals.csv", book, plan)


def main():
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date().isoformat()
    retry_only = "--retry-missing" in sys.argv
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

    def needs(key):
        section = plan["strategies"].get(key)
        return not retry_only or not section or bool(section.get("error") or section.get("provisional")) or (
            key == "lh" and "seat_audit" not in section["meta"])

    def add(key, rows=None, *, meta=None, error=None, provisional=False):
        if not needs(key):
            return
        old = plan["strategies"].get(key, {})
        if error and old.get("error", True) is None:
            old["retry_error"] = error
            return
        plan["strategies"][key] = dict(candidates=rows or [], meta=meta or {},
                                       error=error, provisional=provisional)

    previous = cal.previous_session(day).date().isoformat()
    refresh_dragon = needs("lh")
    if refresh_dragon:
        dragon_scans(day, previous, add)
    # Keep the legacy strategy key for paper settlement, with no new N3 entries.
    plan["strategies"]["n3"] = dict(candidates=[], meta={}, error=None, provisional=False)
    needed = {key for key in ("v3", "s2", "bf", "fb") if needs(key)}

    try:
        indices = index("sh000300")
        days = sorted(d for d in indices if d <= day)
        if not days or days[-1] != day:
            raise ValueError("沪深300尚未提供今日收盘日线")
        previous = days[-2]
        market_scans(day, days, add, needed)
        dragon = plan["strategies"].get("lh", {})
        if refresh_dragon and dragon.get("candidates"):
            try:
                add_volume_tags(dragon, days, package)
            except Exception as exc:
                dragon["provisional"] = True
                dragon["meta"]["volume_error"] = str(exc)
    except Exception as exc:
        for key in needed:
            add(key, error=str(exc))

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
