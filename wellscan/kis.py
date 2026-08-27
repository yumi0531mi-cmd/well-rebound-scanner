from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from filelock import FileLock

from .bar_store import CockroachBarStore
from .models import Candidate, Market, TradingSession


class KISError(RuntimeError):
    pass


class KISClient:
    """Read-only KIS client for rankings, current price and minute history."""

    def __init__(self, cache_root: str | Path = ".scanner_data/auth", auth_store: CockroachBarStore | None = None):
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._approval_key = ""
        self._approval_expires = datetime.min.replace(tzinfo=UTC)
        self._auth_store = auth_store if auth_store is not None else CockroachBarStore.from_environment()

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _token_path(self) -> Path:
        return self.cache_root / "token.json"

    def access_token(self) -> str:
        path = self._token_path()
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                expiry = datetime.fromisoformat(payload["expires_at"])
                if expiry > datetime.now(UTC) + timedelta(minutes=10):
                    return str(payload["access_token"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        with FileLock(str(path) + ".lock", timeout=15):
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    expiry = datetime.fromisoformat(payload["expires_at"])
                    if expiry > datetime.now(UTC) + timedelta(minutes=10):
                        return str(payload["access_token"])
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if self._auth_store is not None:
                durable = self._auth_store.load_auth("kis_access_token")
                if durable is not None:
                    token, expiry_value = durable
                    expiry = expiry_value.to_pydatetime()
                    if expiry > datetime.now(UTC) + timedelta(minutes=10):
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(
                            json.dumps({"access_token": token, "expires_at": expiry.isoformat()}), encoding="utf-8"
                        )
                        temporary.replace(path)
                        return token
            if not self.configured:
                raise KISError("KIS_APP_KEY/KIS_APP_SECRET 환경변수가 필요합니다.")
            response = self.session.post(
                f"{self.base_url}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=15,
            )
        if not response.ok:
            try:
                error_body = response.json()
                detail = str(error_body.get("error_description") or error_body.get("msg1") or error_body.get("error") or "")
            except (ValueError, AttributeError):
                detail = ""
            safe_detail = detail[:240].replace(self.app_key, "***").replace(self.app_secret, "***")
            suffix = f" · {safe_detail}" if safe_detail else ""
            raise KISError(f"KIS 토큰 발급 실패: HTTP {response.status_code}{suffix}")
        body = response.json()
        token = str(body.get("access_token") or "")
        if not token:
            raise KISError("KIS 토큰 응답에 access_token이 없습니다.")
        expires_in = max(int(body.get("expires_in") or 86400) - 600, 600)
        temporary = path.with_suffix(".tmp")
        expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        temporary.write_text(json.dumps({"access_token": token, "expires_at": expiry.isoformat()}), encoding="utf-8")
        temporary.replace(path)
        if self._auth_store is not None:
            self._auth_store.save_auth("kis_access_token", token, pd.Timestamp(expiry))
        return token

    def websocket_approval_key(self) -> str:
        if self._approval_key and self._approval_expires > datetime.now(UTC):
            return self._approval_key
        if self._auth_store is not None:
            durable = self._auth_store.load_auth("kis_websocket_approval")
            if durable is not None:
                key, expiry_value = durable
                expiry = expiry_value.to_pydatetime()
                if expiry > datetime.now(UTC) + timedelta(minutes=10):
                    self._approval_key, self._approval_expires = key, expiry
                    return key
        response = self.session.post(
            f"{self.base_url}/oauth2/Approval",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
            timeout=15,
        )
        if not response.ok:
            raise KISError(f"WebSocket 접속키 발급 실패: HTTP {response.status_code}")
        key = str(response.json().get("approval_key") or "")
        if not key:
            raise KISError("WebSocket 접속키가 비어 있습니다.")
        self._approval_key = key
        self._approval_expires = datetime.now(UTC) + timedelta(hours=23)
        if self._auth_store is not None:
            self._auth_store.save_auth("kis_websocket_approval", key, pd.Timestamp(self._approval_expires))
        return key

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + 0.25 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get(self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "") -> tuple[dict[str, Any], str]:
        response = None
        for attempt in range(3):
            self._throttle()
            response = self.session.request(
                "GET",
                f"{self.base_url}{path}",
                headers={
                    "authorization": f"Bearer {self.access_token()}",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                    "tr_id": tr_id,
                    "tr_cont": tr_cont,
                    "custtype": "P",
                },
                params=params,
                timeout=15,
            )
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                break
            time.sleep((0.35 * (2**attempt)) + random.uniform(0.0, 0.15))
        assert response is not None
        if not response.ok:
            raise KISError(f"{tr_id} HTTP {response.status_code}")
        payload = response.json()
        if str(payload.get("rt_cd", "0")) != "0":
            raise KISError(str(payload.get("msg1") or f"{tr_id} 응답 오류"))
        return payload, str(response.headers.get("tr_cont") or response.headers.get("TR_CONT") or "")

    @staticmethod
    def _number(row: dict[str, Any], *keys: str) -> float:
        for key in keys:
            try:
                value = row.get(key)
                if value not in (None, ""):
                    return float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return 0.0

    def _ranking(self, sort_code: str, source: str, limit: int = 100) -> list[Candidate]:
        rows: list[dict[str, Any]] = []
        continuation = ""
        for _ in range(10):
            payload, next_continuation = self.get(
                "/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "1",
                    "FID_BLNG_CLS_CODE": sort_code,
                    "FID_TRGT_CLS_CODE": "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                    "FID_INPUT_PRICE_1": "0",
                    "FID_INPUT_PRICE_2": "300000",
                    "FID_VOL_CNT": "0",
                    "FID_INPUT_DATE_1": "",
                },
                continuation,
            )
            rows.extend(row for row in payload.get("output", []) if isinstance(row, dict))
            if len(rows) >= limit or next_continuation not in {"M", "F"}:
                break
            continuation = "N"
        candidates: list[Candidate] = []
        for row in rows[:limit]:
            symbol = str(row.get("mksc_shrn_iscd") or "").strip()
            if not symbol:
                continue
            candidates.append(
                Candidate(
                    symbol=symbol,
                    name=str(row.get("hts_kor_isnm") or symbol),
                    price=self._number(row, "stck_prpr"),
                    change_pct=self._number(row, "prdy_ctrt"),
                    volume=self._number(row, "acml_vol"),
                    turnover=self._number(row, "acml_tr_pbmn"),
                    sources=frozenset({source}),
                )
            )
        return candidates

    def candidate_union(self, limit_each: int = 100) -> list[Candidate]:
        merged: dict[str, Candidate] = {}
        for candidate in self._ranking("0", "거래량TOP100", limit_each) + self._ranking("3", "거래대금TOP100", limit_each):
            previous = merged.get(candidate.symbol)
            if previous is None:
                merged[candidate.symbol] = candidate
            else:
                merged[candidate.symbol] = Candidate(
                    symbol=previous.symbol,
                    name=previous.name or candidate.name,
                    price=previous.price or candidate.price,
                    change_pct=previous.change_pct or candidate.change_pct,
                    volume=max(previous.volume, candidate.volume),
                    turnover=max(previous.turnover, candidate.turnover),
                    sources=previous.sources | candidate.sources,
                )
        return sorted(merged.values(), key=lambda item: (len(item.sources), item.turnover, item.volume), reverse=True)

    def current_price(self, symbol: str) -> tuple[float, float, datetime]:
        payload, _ = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        output = payload.get("output") or {}
        price = self._number(output, "stck_prpr")
        change = self._number(output, "prdy_ctrt")
        if price <= 0:
            raise KISError(f"{symbol} 현재가 미수신")
        return price, change, datetime.now(UTC)

    def _overseas_ranking(self, exchange: str, source: str, limit: int, session: TradingSession) -> list[Candidate]:
        path = "/uapi/overseas-stock/v1/ranking/trade-vol" if source == "거래량 TOP100" else "/uapi/overseas-stock/v1/ranking/trade-pbmn"
        tr_id = "HHDFS76310010" if source == "거래량 TOP100" else "HHDFS76320010"
        rows: list[dict[str, Any]] = []
        keyb = ""
        continuation = ""
        for _ in range(5):
            payload, next_continuation = self.get(
                path,
                tr_id,
                {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0", "KEYB": keyb, "AUTH": "", "PRC1": "", "PRC2": ""},
                continuation,
            )
            rows.extend(row for row in payload.get("output2", []) if isinstance(row, dict))
            keyb = str(payload.get("keyb") or "")
            if len(rows) >= limit or next_continuation not in {"M", "F"}:
                break
            continuation = "N"
        results = []
        for row in rows[:limit]:
            symbol = str(row.get("symb") or "").strip().upper()
            if not symbol:
                continue
            results.append(Candidate(
                symbol=symbol,
                name=str(row.get("name") or row.get("ename") or symbol),
                price=self._number(row, "last"),
                change_pct=self._number(row, "rate"),
                volume=self._number(row, "tvol"),
                turnover=self._number(row, "tamt"),
                sources=frozenset({source}),
                market=Market.US,
                exchange=exchange,
                session=session,
            ))
        return results

    def overseas_candidate_union(self, session: TradingSession, limit_each: int = 100) -> list[Candidate]:
        merged: dict[tuple[str, str], Candidate] = {}
        per_exchange = max(20, (limit_each + 2) // 3)
        for exchange in ("NAS", "NYS", "AMS"):
            for source in ("거래량 TOP100", "거래대금 TOP100"):
                for item in self._overseas_ranking(exchange, source, per_exchange, session):
                    key = (exchange, item.symbol)
                    previous = merged.get(key)
                    if previous is None:
                        merged[key] = item
                    else:
                        merged[key] = Candidate(
                            symbol=item.symbol, name=previous.name or item.name, price=previous.price or item.price,
                            change_pct=previous.change_pct or item.change_pct, volume=max(previous.volume, item.volume),
                            turnover=max(previous.turnover, item.turnover), sources=previous.sources | item.sources,
                            market=Market.US, exchange=exchange, session=session,
                        )
        return sorted(merged.values(), key=lambda item: (len(item.sources), item.turnover, item.volume), reverse=True)[: limit_each * 2]

    def overseas_current_price(self, symbol: str, exchange: str) -> tuple[float, float, datetime]:
        payload, _ = self.get(
            "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
            {"AUTH": "", "EXCD": exchange, "SYMB": symbol},
        )
        output = payload.get("output") or {}
        price = self._number(output, "last")
        if price <= 0:
            raise KISError(f"{exchange}:{symbol} 해외 현재가 미수신")
        return price, self._number(output, "rate"), datetime.now(UTC)

    def overseas_minutes(self, symbol: str, exchange: str, max_records: int = 1200, before: str = "") -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        keyb = before
        for page in range(max(1, min(10, (max_records + 119) // 120))):
            continuation_page = bool(before) or page > 0
            payload, continuation = self.get(
                "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", "HHDFS76950200",
                {"AUTH": "", "EXCD": exchange, "SYMB": symbol, "NMIN": "1", "PINC": "1" if continuation_page else "0",
                 "NEXT": "1" if continuation_page else "", "NREC": "120", "FILL": "", "KEYB": keyb},
                "N" if continuation_page else "",
            )
            batch = [row for row in payload.get("output2", []) if isinstance(row, dict)]
            rows.extend(batch)
            if not batch or len(rows) >= max_records or continuation not in {"M", "F"}:
                break
            last = batch[-1]
            raw = f"{last.get('xymd', '')}{str(last.get('xhms', '')).zfill(6)}"
            try:
                keyb = (datetime.strptime(raw, "%Y%m%d%H%M%S") - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
            except ValueError:
                break
        records = []
        for row in rows:
            try:
                timestamp = pd.to_datetime(f"{row['xymd']}{str(row['xhms']).zfill(6)}", format="%Y%m%d%H%M%S")
                records.append({"timestamp": timestamp, "open": float(row["open"]), "high": float(row["high"]),
                                "low": float(row["low"]), "close": float(row["last"]), "volume": float(row["evol"])})
            except (KeyError, TypeError, ValueError):
                continue
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame(records).set_index("timestamp").sort_index().drop_duplicates()

    def minute_day(self, symbol: str, business_date: str) -> pd.DataFrame:
        payload, _ = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
            "FHKST03010230",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": "153000",
                "FID_INPUT_DATE_1": business_date,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "",
            },
        )
        records = []
        for row in payload.get("output2", []):
            try:
                timestamp = pd.to_datetime(
                    str(row.get("stck_bsop_date") or business_date) + str(row.get("stck_cntg_hour") or "").zfill(6),
                    format="%Y%m%d%H%M%S",
                )
                records.append(
                    {
                        "timestamp": timestamp,
                        "open": float(row["stck_oprc"]),
                        "high": float(row["stck_hgpr"]),
                        "low": float(row["stck_lwpr"]),
                        "close": float(row["stck_prpr"]),
                        "volume": float(row["cntg_vol"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame(records).set_index("timestamp").sort_index()
