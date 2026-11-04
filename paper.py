"""Independent paper accounts using published daily OHLC, with per-signal audit."""
import copy
import csv
import json
import math

from extra import limit
from scanners import bf_budget, g3_budget


def active(row):
    return bool(row and row["volume"] > 0 and row["open"] > 0
                and row["close"] > 0 and row["prev_close"] > 0)


def fees(strategy, amount, sell=False):
    if strategy == "fb":
        return round(max(amount * .00025, 5) + (amount * .0005 if sell else 0), 2)
    return round(amount * .0001, 2)


def next_day(cal, day, count=1):
    return cal.session_offset(day, count).date().isoformat()


def at_limit(code, row, field, up):
    width = .20 if code.startswith(("688", "689")) else (
        .05 if "ST" in row["name"].upper() else .10)
    boundary = limit(row["prev_close"], 1 + width if up else 1 - width)
    return row[field] >= boundary - .005 if up else row[field] <= boundary + .005


def event(events, strategy, signal, day, status, **details):
    record = {**signal, **details}
    if "reason" in signal:
        record["signal_reason"] = signal["reason"]
    record.update(signal_id=f"{strategy}:{signal['signal_date']}:{signal['code']}",
                  strategy=strategy, date=day, status=status)
    events.append(record)


def evaluate_open(strategy, signal, row):
    code = signal["code"]
    if not active(row):
        return "停牌或缺少开盘数据", None, None
    if strategy != "n3" and at_limit(code, row, "open", True):
        return "涨停开盘", None, None
    if strategy in ("v3", "s2", "bf", "lh") and at_limit(code, row, "open", False):
        return "跌停开盘", None, None
    gap = row["open"] / row["prev_close"] - 1
    label = signal.get("label")
    if strategy == "v3" and not -.05 <= round(gap, 10) <= .06:
        return "开盘缺口越界", gap, label
    if strategy == "s2" and row["open"] >= signal["signal_close_raw"] * 1.0985:
        return "开盘超S2阈值", gap, label
    if strategy == "n3":
        gap = row["open"] / signal["close"] - 1
        upper = .198 if code.startswith(("688", "689")) else .098
        if round(gap, 10) < -.05 or round(gap, 10) >= upper:
            return "开盘缺口越界", gap, label
    if strategy == "fb":
        label = "C2" if signal["pos60_med"] < .7 else "C1"
        lower, upper = (-.025, .05) if label == "C2" else (-.05, -.02)
        if not lower <= round(gap, 10) <= upper:
            return "开盘缺口越界", gap, label
    return None, gap, label


def exits(key, account, day, bars, events, opening):
    if opening != (key == "v3"):
        return
    for code, position in list(account["positions"].items()):
        if day < position["due"] and (key != "s2" or day <= position["buy_date"]):
            continue
        row = bars.get(code)
        signal = position["signal"]
        field = "open" if opening else "close"
        reason = "到期"
        if not active(row) or at_limit(code, row, field, False):
            if day >= position["due"]:
                position["deferred"] += 1
                event(events, key, signal, day, "DEFER", reason="停牌、缺数据或跌停")
            continue
        if key == "bf" and position["extensions"] < 3 and at_limit(code, row, "close", True):
            position["extensions"] += 1
            position["deferred"] += 1
            event(events, key, signal, day, "DEFER", reason="涨停顺延")
            continue
        price = row[field]
        if key == "s2":
            # Scale the stop and adjusted support using official ex-right references.
            if row["low"] < position["stop"]:
                price = min(row["open"], position["stop"])
                reason = "盘中止损（日线模拟）"
            elif row["close"] < position["support"]:
                reason = "收盘破支撑"
            elif day < position["due"]:
                continue
        slip = {"bf": .0005, "lh": .001}.get(key, 0)
        price = round(price * (1 - slip), 4)
        gross = round(price * position["shares"], 2)
        fee = fees(key, gross, sell=True)
        account["cash"] = round(account["cash"] + gross - fee, 2)
        event(events, key, signal, day, "SELL", side="SELL", price=price,
              shares=position["shares"], fee=fee, gross=gross,
              net_profit=round(gross - fee - position["cost"], 2),
              cash_after=account["cash"], deferred_days=position["deferred"],
              reason=reason, source="TDX日线模拟")
        del account["positions"][code]


def entries(key, account, section, signal_day, day, cal, bars, events):
    positions = account["positions"]
    queue = []
    for candidate in section.get("candidates", []):
        signal = {**candidate, "signal_date": signal_day}
        # Scanner status belongs to the source audit, not the trade event.
        signal.pop("status", None)
        signal.pop("date", None)
        label = signal.pop("label", None)
        reason, gap, computed = evaluate_open(key, {**signal, "label": label}, bars.get(signal["code"]))
        signal.update(gap=gap, trade_class=computed)
        if reason or signal["code"] in positions:
            event(events, key, signal, day, "SKIP", reason=reason or "已有同股持仓")
        else:
            queue.append(signal)
    if not queue:
        return
    equity = account["cash"] + sum(p["mark"] for p in positions.values())
    max_positions = {"v3": 5, "s2": 4, "bf": 4, "fb": 4}.get(key, math.inf)
    route = key
    daily_limit = math.inf
    if key == "v3":
        daily_limit = min(len(queue), 5 - len(positions))
    elif key == "bf":
        bf = [r for r in queue if r["bf_static_ok"] and round(r["gap"], 10) <= .08]
        if section["meta"]["market_gate_pass"] and bf:
            for row in queue:
                if row not in bf:
                    event(events, key, row, day, "SKIP", reason="BF路线开盘过滤")
            queue = bf
            daily_limit = 2 if queue[0]["category"] == "A" else 1
        else:
            route = "g3"
            daily_limit = 0 if any(p["route"] == "g3" for p in positions.values()) else 1
    elif key == "fb":
        queue.sort(key=lambda r: (r["trade_class"] != "C2", r["gap"], r["code"]))
        if account.get("drawdown_halt"):
            daily_limit = 0
    bought = 0
    for signal in queue:
        code = signal["code"]
        row = bars[code]
        if len(positions) >= max_positions or bought >= daily_limit:
            event(events, key, signal, day, "SKIP", reason="仓位、当日名额或风控限制")
            continue
        cash = account["cash"]
        if key == "v3":
            budget = cash / (daily_limit - bought)
        elif key == "s2":
            budget = cash / (4 - len(positions))
        elif key == "bf":
            budget = g3_budget(cash) if route == "g3" else bf_budget(cash, len(positions))
        elif key == "lh":
            budget = equity / 3
        elif key == "n3":
            budget = min(equity * .5 / len(queue),
                         equity / (3 if signal["trade_class"] == "N3" else 2))
        else:
            budget = min((cash + sum(p["mark"] for p in positions.values())) / 4 * .99,
                         cash * .99, signal["money_t"] * .001)
        budget = min(budget, cash)
        slip = {"bf": .0005, "lh": .001}.get(key, 0)
        price = round(row["open"] * (1 + slip), 4)
        shares = int(budget / price / 100) * 100
        gross = round(shares * price, 2)
        fee = fees(key, gross)
        while shares and gross + fee > (cash if key == "fb" else budget):
            shares -= 100
            gross = round(shares * price, 2)
            fee = fees(key, gross)
        if not shares:
            event(events, key, signal, day, "SKIP", reason="预算不足一手")
            continue
        cost = round(gross + fee, 2)
        account["cash"] = round(cash - cost, 2)
        due = next_day(cal, day, 2 if key == "v3" else 1)
        position = dict(signal=signal, route=route, buy_date=day, due=due,
                        shares=shares, cost=cost, mark=gross, last_close=row["open"],
                        deferred=0, extensions=0)
        if key == "s2":
            position.update(stop=price * .95,
                            support=signal["support_adjusted"] / signal["signal_factor"]
                            * row["prev_close"] / signal["signal_close_raw"])
        positions[code] = position
        event(events, key, signal, day, "BUY", side="BUY", price=price,
              shares=shares, gross=gross, fee=fee, cash_after=account["cash"],
              due=due, route=route, source="TDX日线模拟")
        bought += 1


def settle(book, plan, day, cal, bars):
    # Commit all six accounts only after a complete session succeeds.
    working = copy.deepcopy(book)
    events = working["events"]
    for key, account in working["accounts"].items():
        for code, position in account["positions"].items():
            row = bars.get(code)
            if key == "s2" and active(row):
                ratio = row["prev_close"] / position["last_close"]
                position["stop"] *= ratio
                position["support"] *= ratio
        exits(key, account, day, bars, events, opening=True)
        section = plan["strategies"].get(key)
        if section and not section.get("error"):
            entries(key, account, section, plan["date"], day, cal, bars, events)
        else:
            events.append(dict(strategy=key, date=day, status="UNVERIFIED",
                               reason=(section or {}).get("error") or "未存档前一交易日预案"))
        exits(key, account, day, bars, events, opening=False)
        for code, position in account["positions"].items():
            row = bars.get(code)
            if active(row):
                position["mark"] *= row["close"] / (row["open"] if position["buy_date"] == day
                                                    else row["prev_close"])
                position["last_close"] = row["close"]
        equity = round(account["cash"] + sum(p["mark"] for p in account["positions"].values()), 2)
        account["equity"] = equity
        account["peak"] = max(account.get("peak", 200000), equity)
        if key == "fb" and equity <= account["peak"] * .65:
            account["drawdown_halt"] = True
    book.update(working)


def write_csv(path, book, plan):
    fields = ("signal_id", "strategy", "signal_date", "date", "code", "name", "status",
              "trade_class", "route", "gap", "side", "price", "shares", "gross", "fee",
              "net_profit", "cash_after", "due", "deferred_days", "reason", "source",
              "signal_factors")
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        pending = []
        for strategy, section in plan["strategies"].items():
            for candidate in section["candidates"]:
                signal = {**candidate, "signal_date": plan["date"]}
                signal.pop("date", None)
                signal.pop("status", None)
                event(pending, strategy, signal, plan["date"], "PENDING",
                      due=plan["next_session"], reason="待下一交易日开盘确认")
        for row in book["events"] + pending:
            factors = {k: v for k, v in row.items() if k not in fields}
            writer.writerow({**row, "signal_factors": json.dumps(factors, ensure_ascii=False)})
