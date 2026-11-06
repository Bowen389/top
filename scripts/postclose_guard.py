"""Decide whether the current post-close report still needs work."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

KEYS = ('lh', 'n3', 'v3', 's2', 'bf', 'fb')


def needs_scan(now, plan, book):
    now = now.astimezone(ZoneInfo('Asia/Shanghai'))
    if now.weekday() >= 5 or now.time() < dt.time(16, 45):
        return False
    day = now.date().isoformat()
    if not plan or plan.get('date') != day or book.get('last_session') != day:
        return True
    try:
        stamp = dt.datetime.fromisoformat(plan['updated_at'])
        if stamp.tzinfo is None:
            return True
        stamp = stamp.astimezone(ZoneInfo('Asia/Shanghai'))
        if stamp.date() != now.date() or stamp.time() < dt.time(16, 45):
            return True
    except (KeyError, TypeError, ValueError):
        return True
    for key in KEYS:
        section = plan.get('strategies', {}).get(key)
        if not isinstance(section, dict) or section.get('error') or section.get('provisional'):
            return True
    return False


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    now = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
    day = now.date().isoformat()
    needed = needs_scan(now, read(root/'reports'/f'{day}.json'), read(root/'reports'/'book.json'))
    print('true' if needed else 'false')
