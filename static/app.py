"""Local web server for high-dividend A-share watchlist (not investment advice)."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import engine
import reports as report_source

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / ".market_cache_v10.json"
REPORT_INDEX_PATH = ROOT / ".report_index_v10.json"
REPORT_INDEX = {}  # verified latest official PDF metadata by stock code
REPORT_JOB = {"running": False, "done": 0, "total": len(engine.STOCKS), "error": None,
              "last_refresh": None, "started": None}
REPORT_INDEX_TTL = 12 * 3600  # show cached timestamp, refresh index twice daily
DEFAULT_BOLL_SOURCE = "eastmoney"
LOCK = threading.RLock()
STATE = {
    "stocks": {code: {**record, "price": None, "change_pct": None, "quote_time": None,
                       "yield": None, "dividend_yield": None, "rly_details": None, "ttm_yield": None, "yield_percentile": None,
                       "yield_samples": 0, "boll": None, "resonance": None,
                       "errors": {}, "updated_at": None, "boll_source": None,
                       "boll_updated_at": None, "resonance_asof": None}
               for code, record in engine.STOCK_MAP.items()},
    "jobs": {"boll_source": DEFAULT_BOLL_SOURCE, "running": False, "done": 0, "total": len(engine.STOCKS), "phase": "未同步", "started": None,
             "finished": None, "error": None},
}


def _read_cache():
    if not CACHE_PATH.exists():
        return
    try:
        stored = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        with LOCK:
            for code, item in stored.get("stocks", {}).items():
                if code in STATE["stocks"]:
                    if stored.get('cache_version') != 10:
                        item = {k: v for k,v in item.items() if k not in ('boll','resonance')}
                    STATE["stocks"][code].update(item)
            STATE['jobs']['boll_source'] = stored.get('boll_source', DEFAULT_BOLL_SOURCE)
            for row in STATE['stocks'].values():
                if row.get('boll_source') != STATE['jobs']['boll_source']:
                    row.update({'boll': None, 'resonance': None, 'boll_source': None,
                                'boll_updated_at': None, 'resonance_asof': None})
    except (ValueError, OSError) as e:
        print("缓存读取失败", e)


def _save_cache():
    with LOCK:
        data = {"stocks": STATE["stocks"], "cache_version": 10, "boll_source": STATE["jobs"].get("boll_source", DEFAULT_BOLL_SOURCE)}
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, CACHE_PATH)


def _merge_stock(code, fields):
    with LOCK:
        STATE["stocks"][code].update(fields)


def _clear_boll_locked(source: str):
    """Must hold LOCK. Explicitly invalidates signals after a data-source switch."""
    STATE["jobs"]["boll_source"] = source
    CHART_CACHE.clear()
    for row in STATE["stocks"].values():
        row.update({"boll": None, "resonance": None, "boll_source": None,
                    "boll_updated_at": None, "resonance_asof": None})


def _apply_boll_result(code: str, calc: dict, source: str):
    """One atomic update for day/week/month, the derived signal, date and provider."""
    vals = calc["boll"]
    signal = engine.resonance(vals.get("day"), vals.get("week"), vals.get("month"))
    w, m = vals.get("week"), vals.get("month")
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOCK:
        if source != STATE["jobs"]["boll_source"]:
            return False  # A stale background result may not overwrite the new source.
        STATE["stocks"][code].update({"boll": vals, "resonance": signal,
            "boll_source": source, "boll_updated_at": stamp,
            "resonance_asof": w["date"] if w and m and w["date"] == m["date"] else
                              ({"week": w["date"], "month": m["date"]} if w and m else None)})
        CHART_CACHE[code] = calc["candles"]
    return True


def _process_boll(code: str, source: str):
    try:
        calc = engine.compute_boll_indicators(code, source=source)
        _apply_boll_result(code, calc, source)
        if calc.get("errors"):
            with LOCK:
                STATE["stocks"][code].setdefault("errors", {})["boll"] = calc["errors"]
        return True
    except Exception as exc:
        with LOCK:
            if source == STATE["jobs"]["boll_source"]:
                STATE["stocks"][code].update({"boll": None, "resonance": None,
                    "boll_source": None, "boll_updated_at": None, "resonance_asof": None})
                STATE["stocks"][code].setdefault("errors", {})["boll"] = str(exc)
        return False


def _run_boll_refresh(source: str):
    """Faster than full refresh: re-evaluate the 35 BOLL signals, no dividend queries."""
    with LOCK:
        STATE["jobs"].update({"running": True, "done": 0, "total": len(engine.STOCKS),
            "phase": "同步后复权BOLL和周/月低位共振", "started": datetime.now().isoformat(timespec="seconds"),
            "finished": None, "error": None, "boll_source": source})
        _clear_boll_locked(source)
    _save_cache()
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [executor.submit(_process_boll, code, source) for code, _, _ in engine.STOCKS]
            for future in as_completed(tasks):
                try:
                    future.result()
                except Exception as exc:
                    print("共振刷新异常", exc)
                with LOCK:
                    STATE["jobs"]["done"] += 1
                _save_cache()
    finally:
        with LOCK:
            STATE["jobs"].update({"running": False, "phase": "BOLL及共振同步结束",
                                  "finished": datetime.now().isoformat(timespec="seconds")})
        _save_cache()


def _refresh_quote():
    updates = engine.quotes_all()
    for code, q in updates.items():
        _merge_stock(code, q)
    return len(updates)


def _process_stock(code, boll_source=DEFAULT_BOLL_SOURCE):
    patch = {"errors": {}, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        calc = engine.compute_boll_indicators(code, source=boll_source)
        # Apply together, avoiding stale/provider-mixed resonance.
        _apply_boll_result(code, calc, boll_source)
        if calc["errors"]:
            patch["errors"]["boll"] = calc["errors"]
    except Exception as e:
        patch["errors"]["boll"] = str(e)
        # No calculation result is a pending signal, never an explicit "no".
        with LOCK:
            if boll_source == STATE["jobs"]["boll_source"]:
                STATE["stocks"][code].update({"boll": None, "resonance": None,
                    "boll_source": None, "boll_updated_at": None, "resonance_asof": None})
    try:
        divs = engine.dividends(code)
        with LOCK:
            DIVIDEND_CACHE[code] = divs
            raw_price = STATE["stocks"][code].get("price")
            qtime = str(STATE["stocks"][code].get("quote_time") or "")
        if not raw_price:
            raise RuntimeError("未取得最新原始股价，暂不计算股息率")
        # Tencent snapshot timestamp is normally YYYYMMDDhhmmss.
        asof = qtime[:8]
        if len(asof) == 8 and asof.isdigit():
            asof_date = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"
            date.fromisoformat(asof_date)
        else:
            asof_date = date.today().isoformat()
        detail = engine.rly_yield(divs, asof_date, raw_price)
        yield_pct = detail["yield"]
        patch["yield"] = yield_pct
        patch["dividend_yield"] = yield_pct
        patch["rly_details"] = detail
        patch["yield_asof"] = asof_date
        patch["yield_basis"] = "最近已公告年度末期+中期分红（每10股金额÷10），按原始现价计算；中期待定时显式标记"
        if yield_pct is None:
            patch["errors"]["rly"] = detail["status"]
            patch["yield_percentile"] = None
            patch["yield_samples"] = 0
        else:
            try:
                patch["ttm_yield"] = engine.cash_yield_percent(divs, asof_date, raw_price)
            except Exception:
                patch["ttm_yield"] = None
            try:
                history = engine.raw_week_kline(code)
                dist = engine.yield_percentile(history, yield_pct, divs, asof_date)
                patch["yield_percentile"] = dist["percentile"]
                patch["yield_samples"] = dist["samples"]
                patch["yield_rank_details"] = dist
                if dist.get("warning"):
                    patch["errors"]["percentile"] = dist["warning"]
            except Exception as e:
                patch["yield_percentile"] = None
                patch["yield_samples"] = 0
                patch["errors"]["percentile"] = str(e)
    except Exception as e:
        patch["errors"]["dividend"] = str(e)
        patch["yield"] = None
        patch["rly_details"] = None
        patch["yield_percentile"] = None
    _merge_stock(code, patch)
    return code

CHART_CACHE = {}
DIVIDEND_CACHE = {}
REPORT_CACHE = {}


def _read_report_index():
    if not REPORT_INDEX_PATH.exists():
        return
    try:
        data = json.loads(REPORT_INDEX_PATH.read_text(encoding="utf-8"))
        if data.get("version") != 10:
            return
        with LOCK:
            for code, item in data.get("reports", {}).items():
                if code in engine.STOCK_MAP and isinstance(item, dict):
                    REPORT_INDEX[code] = item
            REPORT_JOB["last_refresh"] = data.get("last_refresh")
    except (OSError, ValueError, TypeError):
        pass


def _save_report_index():
    with LOCK:
        payload = {"version": 10, "reports": REPORT_INDEX,
                   "last_refresh": REPORT_JOB.get("last_refresh")}
    try:
        temp = REPORT_INDEX_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, REPORT_INDEX_PATH)
    except OSError as e:
        print("财报索引缓存写入失败", e)


def _report_summary(code):
    """Reads and verifies metadata from current CNINFO response; no 2025 placeholders."""
    rows = report_source.fetch_latest_reports(code)
    if not rows:
        raise RuntimeError("没有取得带官方PDF链接的财报")
    with LOCK:
        REPORT_INDEX[code] = {"latest": rows[0], "reports": rows,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "error": None}
    return REPORT_INDEX[code]


def _run_report_refresh():
    with LOCK:
        REPORT_JOB.update({"running": True, "done": 0, "total": len(engine.STOCKS),
                           "error": None, "started": datetime.now().isoformat(timespec="seconds")})
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_report_summary, code): code for code, _, _ in engine.STOCKS}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    fut.result()
                except Exception as exc:
                    with LOCK:
                        existing = REPORT_INDEX.get(code)
                        if existing is None:
                            REPORT_INDEX[code] = {"latest": None, "reports": [], "fetched_at": None,
                                                  "error": str(exc)}
                        else:
                            # Keep last *verified* report; show data-source failure visibly.
                            existing["error"] = str(exc)
                with LOCK:
                    REPORT_JOB["done"] += 1
                _save_report_index()
    except Exception as exc:
        with LOCK:
            REPORT_JOB["error"] = str(exc)
    finally:
        with LOCK:
            REPORT_JOB["running"] = False
            REPORT_JOB["last_refresh"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save_report_index()


_read_report_index()


def _run_refresh(boll_source=DEFAULT_BOLL_SOURCE):
    with LOCK:
        if STATE["jobs"].get("boll_source") != boll_source:
            _clear_boll_locked(boll_source)
        STATE["jobs"].update({"running": True, "done": 0, "total": len(engine.STOCKS),
                               "phase": "拉取腾讯报价", "started": datetime.now().isoformat(timespec="seconds"),
                               "finished": None, "error": None, "boll_source": boll_source})
    try:
        try:
            _refresh_quote()
            _save_cache()
        except Exception as e:
            with LOCK:
                STATE["jobs"]["error"] = "报价获取失败：" + str(e)
        with LOCK:
            STATE["jobs"]["phase"] = "计算HFQ布林通道、宣告分红RLY及RLY历史分位（东财接口需要节流）"
        with ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [executor.submit(_process_stock, code, boll_source) for code, _, _ in engine.STOCKS]
            for future in as_completed(tasks):
                try:
                    future.result()
                except Exception as e:
                    print("单标的刷新异常", e)
                with LOCK:
                    STATE["jobs"]["done"] += 1
                _save_cache()
    finally:
        with LOCK:
            STATE["jobs"].update({"running": False, "phase": "刷新结束",
                                  "finished": datetime.now().isoformat(timespec="seconds")})
        _save_cache()


_read_cache()
app = FastAPI(title="高股息观察与财报分析", version="10.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/stocks")
def list_stocks():
    with LOCK:
        rows = []
        for code, _, _ in engine.STOCKS:
            r = dict(STATE["stocks"][code])
            item = REPORT_INDEX.get(code, {})
            r["recent_report"] = item.get("latest")
            r["report_error"] = item.get("error")
            r["report_fetched_at"] = item.get("fetched_at")
            rows.append(r)
        return {"stocks": rows, "report_job": dict(REPORT_JOB),
                "jobs": dict(STATE["jobs"]), "sources": engine.SOURCES,
                "metadata": {"watchlist_count": len(engine.STOCKS), "watchlist_note": "央国企新增候选需要动态复核千亿市值和连续4年分红，不作自动达标承诺", "boll_adjustment": "hfq", "boll_rule": "上>=上轨；中上>中轨+1σ；中=中轨±1σ；中下<中轨-1σ；下<=下轨；共振=周与月都为中/中下", "version": "10.0.0", "boll_source": STATE["jobs"].get("boll_source", DEFAULT_BOLL_SOURCE), "resonance_rule": "周与月=中或中下才是；缺失任一数据=待计算；日线不参与",
                             "dividend_type": "RLY 最近已公告年度末期+中期 (按报告期，元/10股÷10；中期未宣告待定沿用上期)",
                             "data_status": "缓存/实时均逐条标注；无网络时不会填充模拟值"}}


@app.post("/api/refresh")
def refresh(boll_source: str = DEFAULT_BOLL_SOURCE):
    if boll_source not in ('tencent','eastmoney'):
        raise HTTPException(status_code=400, detail='无效的后复权数据源')
    with LOCK:
        if STATE["jobs"]["running"]:
            return {"ok": True, "message": "刷新任务已在进行", "jobs": dict(STATE["jobs"])}
        STATE["jobs"]["running"] = True
    threading.Thread(target=_run_refresh, args=(boll_source,), daemon=True).start()
    return {"ok": True, "message": "已开始后台刷新，请观察进度"}


@app.post("/api/boll/refresh")
def refresh_boll_signals(source: str = DEFAULT_BOLL_SOURCE):
    if source not in ("tencent", "eastmoney"):
        raise HTTPException(status_code=400, detail="无效的后复权行情来源")
    with LOCK:
        if STATE["jobs"]["running"]:
            raise HTTPException(status_code=409, detail="其他刷新尚未结束，请稍后重试")
        STATE["jobs"]["running"] = True
        # Invalidate synchronously, before returning the HTTP response.
        _clear_boll_locked(source)
        STATE["jobs"]["phase"] = "切换来源，等待重算42只BOLL及低位共振"
    _save_cache()
    threading.Thread(target=_run_boll_refresh, args=(source,), daemon=True).start()
    return {"ok": True, "message": f"已切换为{source}，正在同步全部42只的后复权BOLL与低位共振"}


@app.post("/api/quotes")
def refresh_quotes():
    try:
        count = _refresh_quote()
        _save_cache()
        return {"ok": True, "count": count}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/reports/refresh")
def refresh_reports(force: bool = False):
    """Explicit client button or first page-open auto sync, independent of BOLL."""
    with LOCK:
        if REPORT_JOB["running"]:
            return {"ok": True, "message": "42只财报索引正在更新", "job": dict(REPORT_JOB)}
        cached = REPORT_JOB.get("last_refresh")
        if cached and not force:
            try:
                if (datetime.now().astimezone() - datetime.fromisoformat(cached)).total_seconds() < REPORT_INDEX_TTL:
                    return {"ok": True, "message": "报告索引12小时内已更新，使用缓存", "job": dict(REPORT_JOB)}
            except ValueError:
                pass
        REPORT_JOB["running"] = True
    threading.Thread(target=_run_report_refresh, daemon=True).start()
    return {"ok": True, "message": "正在从巨潮资讯核对42只股票最新财报PDF", "job": dict(REPORT_JOB)}


@app.get("/api/reports")
def report_index():
    with LOCK:
        return {"job": dict(REPORT_JOB), "items": {code: dict(item) for code, item in REPORT_INDEX.items()}}


@app.get("/api/stock/{code}/reports/latest")
def latest_report(code: str, force: bool = False):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在42只观察池中")
    with LOCK:
        item = REPORT_INDEX.get(code)
    if item and item.get("latest") and not force:
        return item
    try:
        result = _report_summary(code)
        _save_report_index()
        return result
    except Exception as exc:
        if item and item.get("latest"):
            return {**item, "error": "更新失败，以下为缓存财报：" + str(exc)}
        raise HTTPException(status_code=502, detail="巨潮资讯最近财报读取失败：" + str(exc))


@app.get("/api/stock/{code}/reports/pdf")
def preview_report_pdf(code: str, index: int = 0):
    """Serve official PDF only from CNINFO-validated metadata (not arbitrary URLs).

    Requests a real PDF only after the user clicks preview. Never caches it in
    the project folder; keeps the download cap bounded to 30 MiB.
    """
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在42只观察池中")
    with LOCK:
        item = REPORT_INDEX.get(code)
    if not item or not item.get("reports"):
        try:
            item = _report_summary(code)
            _save_report_index()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="官方财报暂不可用：" + str(exc))
    documents = item["reports"]
    if not (0 <= index < len(documents)):
        raise HTTPException(status_code=404, detail="所选报告序号不存在")
    doc = documents[index]
    pdf_url = doc.get("pdf_url")
    if not pdf_url or not pdf_url.startswith(report_source.PDF_BASE) or not report_source.valid_pdf_url(pdf_url.removeprefix(report_source.PDF_BASE)):
        raise HTTPException(status_code=502, detail="PDF地址未通过官方来源校验")
    try:
        with engine.SESSION.get(pdf_url, stream=True, timeout=(7, 35), allow_redirects=True,
                                headers={"User-Agent": report_source.HEADERS["User-Agent"]}) as resp:
            resp.raise_for_status()
            from urllib.parse import urlsplit
            if urlsplit(resp.url).hostname != "static.cninfo.com.cn":
                raise RuntimeError("PDF下载跳转到了非巨潮官方域名，拒绝加载")
            blocks = []
            total = 0
            for block in resp.iter_content(chunk_size=65536):
                if not block:
                    continue
                total += len(block)
                if total > 30 * 1024 * 1024:
                    raise RuntimeError("PDF超过30MB预览上限；请使用官方原始下载链接")
                blocks.append(block)
            content = b"".join(blocks)
            if not content.startswith(b"%PDF-"):
                raise RuntimeError("官方地址未返回有效PDF（可能被限流）")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="PDF预览失败，请改用官方链接：" + str(exc))
    return Response(content=content, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{code}_{doc["period"]}.pdf"',
                 "Cache-Control": "private, max-age=1800"})


@app.get("/api/stock/{code}")
def stock_detail(code: str):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在观察池中")
    with LOCK:
        result = dict(STATE["stocks"][code])
        result["chart"] = CHART_CACHE.get(code)
        result["dividends"] = DIVIDEND_CACHE.get(code)
    return result


@app.get("/api/stock/{code}/boll")
def stock_boll(code: str, source: str = DEFAULT_BOLL_SOURCE):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在观察池中")
    with LOCK:
        if source != STATE["jobs"]["boll_source"]:
            raise HTTPException(status_code=409, detail="当前列表数据源与请求不同，请先切换数据源")
    try:
        result = engine.compute_boll_indicators(code, source=source)
        _apply_boll_result(code, result, source)
        _save_cache()
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/stock/{code}/boll/audit")
def stock_boll_audit(code: str, asof: Optional[date] = None, source: str = DEFAULT_BOLL_SOURCE):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在观察池中")
    try:
        asof_value = asof.isoformat() if asof else None
        result = engine.compute_boll_indicators(code, asof=asof_value, source=source)
        if all(x is None for x in result["boll"].values()):
            raise HTTPException(status_code=502, detail="；".join(result["errors"].values()))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/stock/{code}/boll/compare")
def stock_boll_compare(code: str, asof: Optional[date] = None):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail='不在观察池中')
    return engine.compare_boll_sources(code, asof.isoformat() if asof else None)


@app.get("/api/stock/{code}/financials")
def financials(code: str, force: bool = False):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在观察池中")
    with LOCK:
        cached = REPORT_CACHE.get(code)
        if cached and not force and time.monotonic() - cached[0] < 3600:
            return cached[1]
        index = REPORT_INDEX.get(code, {})
    result = {"source": "新浪财务报表（指标）/ 巨潮资讯（PDF原文）", "statement": None,
              "latest_report": index.get("latest"), "reports": index.get("reports") or [],
              "errors": [], "fetched_at": datetime.now().isoformat(timespec="seconds")}
    if not result["reports"] or force:
        try:
            item = _report_summary(code)
            result["latest_report"] = item["latest"]
            result["reports"] = item["reports"]
            _save_report_index()
        except Exception as exc:
            result["errors"].append("巨潮资讯原始财报：" + str(exc))
    try:
        pnl = engine.sina_statement(code, "lrb")
        if not pnl:
            raise RuntimeError("利润表无数据")
        result["statement"] = engine.statement_metrics(pnl)
        result["statement"]["periods"] = [r["period"] for r in pnl]
        official = result.get("latest_report")
        if official and result["statement"]["period"] != official["period"]:
            result["period_warning"] = (
                f"财务指标来源报告期 {result['statement']['period']}，与巨潮最新PDF"
                f"报告期 {official['period']} 不一致，不能把这些指标称作最新财报指标")
    except Exception as exc:
        result["errors"].append("新浪财务报表：" + str(exc))
    if result["statement"] is None and not result["reports"]:
        raise HTTPException(status_code=502, detail="；".join(result["errors"]) or "数据源无数据")
    with LOCK:
        REPORT_CACHE[code] = (time.monotonic(), result)
    return result


@app.get("/api/stock/{code}/dividends")
def dividend_detail(code: str):
    if code not in engine.STOCK_MAP:
        raise HTTPException(status_code=404, detail="不在观察池中")
    with LOCK:
        cached = DIVIDEND_CACHE.get(code)
    try:
        rows = cached or engine.dividends(code)
        if not cached:
            with LOCK:
                DIVIDEND_CACHE[code] = rows
        return {"dividends": rows[:40], "type": "RLY宣告分红方案（同时展示除息日）", "source": "东方财富分红送转",
                "rly_details": STATE["stocks"][code].get("rly_details")}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/export.csv")
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    columns = ["代码", "名称", "行业", "原始现价", "涨跌幅%", "RLY宣告股息率%",
               "RLY历史分位%", "有效周样本数", "RLY末期每股分红", "RLY中期每股分红", "RLY状态", "实派TTM参考%"]
    for name in ("日", "周", "月"):
        columns += [f"{name}BOLL位置", f"{name}HFQ收盘", f"{name}上轨", f"{name}中轨", f"{name}下轨", f"{name}K线日期"]
    columns += ["低位共振", "共振数据日期", "BOLL数据源", "BOLL计算时间", "行情时间", "BOLL复权方式"]
    writer.writerow(columns)
    with LOCK:
        for code, _, _ in engine.STOCKS:
            r = STATE["stocks"][code]
            bs = r.get("boll") or {}
            row = [code, r["name"], r["industry"], r.get("price"), r.get("change_pct"),
                   r.get("yield"), r.get("yield_percentile"), r.get("yield_samples"),
                   (r.get("rly_details") or {}).get("year_end_cash"),
                   (r.get("rly_details") or {}).get("interim_cash"),
                   (r.get("rly_details") or {}).get("status"), r.get("ttm_yield")]
            for period in ("day", "week", "month"):
                v = bs.get(period) or {}
                row += [v.get("position"), v.get("close_hfq"), v.get("upper"),
                        v.get("mid"), v.get("lower"), v.get("date")]
            writer.writerow(row + [r.get("resonance"), r.get("resonance_asof"),
                                   r.get("boll_source"), r.get("boll_updated_at"),
                                   r.get("quote_time"), "hfq"])

    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="dividend-watch.csv"'})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
