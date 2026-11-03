"""Recent company periodic disclosures from CNINFO (no fabricated filings).

The official announcement JSON contains both report titles and `adjunctUrl` (PDF).
A reported period is derived from the TITLE, not an announcement date. All
not-fetched/old results are explicit; no fixed "2026 Q3" is assumed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
from urllib.parse import urlsplit
import requests

BASE = 'https://www.cninfo.com.cn'
PDF_BASE = 'https://static.cninfo.com.cn/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
    'Referer': 'https://www.cninfo.com.cn/new/disclosure',
    'Origin': 'https://www.cninfo.com.cn',
    'X-Requested-With': 'XMLHttpRequest',
}
CATEGORIES = ('category_sjdbg_szsh', 'category_bndbg_szsh', 'category_ndbg_szsh')
_ORG_IDS: dict[str,str] = {}


def clean_title(title: Any) -> str:
    return re.sub(r'<[^>]*>', '', str(title or '')).strip()


def period_from_title(title: str):
    """Return (quarter-end ISO day, period caption), or None.

    Avoid headlines like ‘关于2026年三季报审议的董事会决议’; financial
    reports must explicitly end in a reporting-document noun.
    """
    title = clean_title(title)
    if any(x in title for x in ('摘要', '提示性公告', '审计报告', '英文版', '取消', '披露时间', '业绩预告', '业绩快报', '关于', '编制')):
        return None
    m = re.search(r'(20\d{2})年\s*(第?[一三1３3]季度|第一季度|第三季度|一季度|三季度|半年度|中期|年度|年报)\s*(?:财务)?报告', title)
    if not m:
        return None
    year, period = int(m[1]), m[2]
    if 2000 > year or year > date.today().year:
        return None
    if '三' in period or '3' in period or '３' in period:
        return (f'{year}-09-30', f'{year}三季报')
    if '一' in period or '1' in period:
        return (f'{year}-03-31', f'{year}一季报')
    if '半' in period or '中期' in period:
        return (f'{year}-06-30', f'{year}半年报')
    return (f'{year}-12-31', f'{year}年报')


def valid_pdf_url(path: Any) -> str | None:
    """Only allow the CNINFO official CDN finalpage PDF path."""
    if not isinstance(path, str):
        return None
    value = path.strip().lstrip('/')
    if not re.fullmatch(r'finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9A-Za-z_.-]+\.pdf', value, re.I):
        return None
    return PDF_BASE + value


def convert_announcement(row: dict, code: str, asof: date):
    if str(row.get('secCode') or '') != code:
        return None
    title = clean_title(row.get('announcementTitle'))
    period = period_from_title(title)
    if not period:
        return None
    try:
        ms = int(row.get('announcementTime'))
        stamp = datetime.fromtimestamp(ms/1000, timezone(timedelta(hours=8))).date()
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    if stamp > asof or stamp < asof - timedelta(days=740):
        return None
    pdf_url = valid_pdf_url(row.get('adjunctUrl'))
    if pdf_url is None:
        return None
    ann_id = str(row.get('announcementId') or '')
    detail_url = (f'https://www.cninfo.com.cn/new/disclosure/detail?annoId={ann_id}'
                  if ann_id.isdigit() else None)
    return {'title': title, 'date': stamp.isoformat(), 'period': period[0],
            'period_label': period[1], 'pdf_url': pdf_url, 'url': detail_url or pdf_url,
            'source': '巨潮资讯（PDF公告原文）', 'size_kb': row.get('adjunctSize'),
            'announcement_id': ann_id}


def extract_reports(payload: dict, code: str, asof: date | None = None):
    asof = asof or date.today()
    rows = payload.get('announcements') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError('巨潮资讯公告接口未返回有效公告列表')
    seen = set()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = convert_announcement(row, code, asof)
        if item and item['pdf_url'] not in seen:
            seen.add(item['pdf_url']); out.append(item)
    # The latest PERIOD matters more than the newest metadata announcement date.
    return sorted(out, key=lambda x: (x['period'], x['date'], x['announcement_id']), reverse=True)


def _post(session: requests.Session, endpoint: str, data: dict, timeout=16):
    r = session.post(BASE + endpoint, data=data, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    return j


def resolve_org_id(code: str, session: requests.Session) -> str:
    if code in _ORG_IDS:
        return _ORG_IDS[code]
    # This API supports Shanghai + Shenzhen A-share identifiers.
    lookup_error = None
    try:
        results = _post(session, '/new/information/topSearch/query', {'keyWord': code, 'maxNum': 10})
        options = results if isinstance(results, list) else results.get('stockList', []) if isinstance(results, dict) else []
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        lookup_error = exc
        options = []
    for obj in options:
        if str(obj.get('zwjc') or obj.get('code') or '') == code and obj.get('orgId'):
            _ORG_IDS[code] = str(obj['orgId']); return _ORG_IDS[code]
        if str(obj.get('secCode') or obj.get('stockCode') or '') == code and obj.get('orgId'):
            _ORG_IDS[code] = str(obj['orgId']); return _ORG_IDS[code]
    # Common CNINFO response objects can use `code` for orgId rather than secCode.
    for obj in options:
        if str(obj.get('secCode') or obj.get('stockCode') or obj.get('zwjc') or '') == code and obj.get('code'):
            value = str(obj['code'])
            if value != code:
                _ORG_IDS[code] = value; return value
    # Legacy static mapping can be an alternate official source when topSearch is blocked.
    try:
        r = session.get(BASE + '/new/data/szse_stock.json', headers=HEADERS, timeout=15)
        r.raise_for_status()
        items = r.json().get('stockList') or []
        for x in items:
            if str(x.get('code') or '') == code and x.get('orgId'):
                _ORG_IDS[code] = str(x['orgId']); return _ORG_IDS[code]
    except (RuntimeError, requests.RequestException, ValueError, TypeError, AttributeError):
        pass
    raise RuntimeError(f'未查到{code}的巨潮组织ID（topSearch={lookup_error}）；不猜测PDF归属')


def fetch_latest_reports(code: str, session: requests.Session | None = None, asof: date | None = None, limit: int = 8) -> list[dict]:
    """Fetch up-to-date periodic filings. Failure is observable, never made up.

    First fetch filtered categories and fall back to a broader ‘报告’ search.
    This avoids the old logic, which looked only at the most recent 60 ordinary
    announcements and often missed mandatory quarterly reports.
    """
    if not re.fullmatch(r'[036]\d{5}', code):
        raise ValueError('股票代码格式错误')
    session = session or requests.Session()
    asof = asof or date.today()
    org = resolve_org_id(code, session)
    start = (asof - timedelta(days=580)).isoformat()
    common = {'stock': f'{code},{org}', 'tabName': 'fulltext', 'pageSize': '30',
              'pageNum': '1', 'column': ('sse' if code.startswith('6') else 'szse'),
              'plate': '', 'seDate': f'{start}~{asof.isoformat()}',
              'sortName': '', 'sortType': '', 'searchkey': '', 'isHLtitle': 'false'}
    errors = []
    found = []
    # 3 categories = periodic disclosures only; distinguish quarterly, H1 and annual.
    for category in CATEGORIES:
        try:
            payload = _post(session, '/new/hisAnnouncement/query', {**common, 'category': category})
            found.extend(extract_reports(payload, code, asof))
        except (ValueError, RuntimeError, requests.RequestException) as exc:
            errors.append(f'{category}: {exc}')
    # Fallback if the endpoint's category syntax changes.
    if not found:
        for page in range(1, 4):
            try:
                payload = _post(session, '/new/hisAnnouncement/query', {**common, 'category': '', 'searchkey': '报告', 'pageNum': str(page)})
                found.extend(extract_reports(payload, code, asof))
                if found: break
                if not payload.get('announcements'): break
            except (ValueError, RuntimeError, requests.RequestException) as exc:
                errors.append(f'通用搜索第{page}页: {exc}'); break
    if not found:
        raise RuntimeError('未找到可验证PDF的定期报告；' + '; '.join(errors[:3]))
    return sorted({r['pdf_url']: r for r in found}.values(),
                  key=lambda x: (x['period'], x['date'], x['announcement_id']), reverse=True)[:limit]
