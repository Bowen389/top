"""Scanners for the two frozen specifications supplied separately."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

import longhu_independent as lh
import market_data


def limit(ref, ratio):
    return float((Decimal(str(ref)) * Decimal(str(ratio))).quantize(
        Decimal(".01"), rounding=ROUND_HALF_UP))


def fb_dual(days, closes):
    deviation = closes[-1] / (sum(closes[-20:]) / 20) - 1
    if deviation >= 0:
        return [], {"B_MA20": deviation}
    if len(days) < 120:
        raise ValueError("120 sessions required for listing age")
    panel = pd.concat([pd.read_csv(market_data.ROOT / (d + ".csv"),
                                  dtype={"code": str}) for d in days[-120:]])
    today = panel[panel.date == days[-1]]
    today = today[today.volume.gt(0) & today.amount.ge(1e8)
                  & ~today.name.str.contains("ST|退|^[NC]", case=False, na=True)]
    output = []
    for t in today.itertuples():
        upper = limit(t.prev_close, 1.1)
        if abs(t.close - upper) > .005 or t.open >= upper - .005:
            continue
        history = panel[panel.code == t.code].set_index("date").reindex(days[-120:])
        if pd.isna(history.iloc[0].close):
            continue
        prior = history.iloc[-2]
        if pd.notna(prior.volume) and prior.volume > 0:
            if abs(prior.close - limit(prior.prev_close, 1.1)) <= .005:
                continue
        window = history.iloc[-61:-1].reset_index()
        if window[["close", "prev_close"]].isna().any().any():
            continue
        adj = market_data.prepare(window.reset_index(drop=True).assign(code=t.code))
        pos = adj.adj_close.iloc[-1] / adj.adj_close.median()
        if pos < 1:
            output.append(dict(code=t.code, name=t.name, close=t.close,
                               pos60_med=float(pos), money_t=t.amount, B_MA20=deviation))
    return output, {"B_MA20": deviation}


def n3_base(date, snapshot, bars):
    # Reuse validation of disclosure identity, pagination and seat completeness.
    _, skipped, audit = lh.screen(date, snapshot)
    invalid = {r["code"] for r in skipped}
    over = {lh.key(r, date): r for r in snapshot["overview"]
            if r.get("SECURITY_CODE") not in invalid}
    seats = defaultdict(list)
    sold = set()
    for side in ("buy", "sell"):
        for row in snapshot[side]:
            if row.get("SECURITY_CODE") in invalid:
                continue
            if "机构专用" in str(row.get("OPERATEDEPT_NAME", "")):
                key = lh.key(row, date)
                if side == "sell":
                    sold.add(key[0])
                else:
                    seats[key].append(row)
    result = {}
    for key, row in over.items():
        code = key[0]
        gain = row["CHANGE_RATE"]
        if code in sold or gain < 5 or code not in bars or bars[code]["volume"] <= 0:
            continue
        group = seats[key]
        amount = sum(s["BUY"] for s in group)
        if amount <= 20_000_000 or (code.startswith("0") and
                                    (len(group) != 1 or group[0]["SELL"] != 0)):
            continue
        turnover = row.get("TURNOVERRATE")
        if not lh.number(turnover, True):
            raise ValueError(f"{code}: missing N3 turnover")
        item = dict(code=code, name=row["SECURITY_NAME_ABBR"], close=row["CLOSE_PRICE"],
                    gain_pct=gain, turnover_pct=turnover, qualifying_amount_yuan=amount)
        if code not in result or amount > result[code]["qualifying_amount_yuan"]:
            result[code] = item
    return result


def n3_candidates(current, previous):
    result = []
    for code, row in sorted(current.items()):
        if code in previous or 7 <= row["gain_pct"] < 9:
            continue
        high_turnover = row["turnover_pct"] > 10
        if code.startswith("0"):
            label = "★N1+N3" if high_turnover else "★N1"
        elif high_turnover:
            label = "N3"
        else:
            continue
        result.append({**row, "label": label})
    return result
