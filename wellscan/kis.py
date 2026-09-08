from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import threading
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from filelock import FileLock

from .bar_store import CockroachBarStore
from .indicators import normalize_bars
from .instruments import MasterCatalog
from .models import Candidate, Market, TradingSession

DOMESTIC_LIMIT_UP_FALLBACK_PCT = 29.5
_DOMESTIC_LEVERAGED_NAME = re.compile(
    r"레버리지|인버스|LEVERAG(?:E|ED)|INVERSE|(?<![0-9])[2-9](?:\.\d+)?X|(?<![0-9])[2-9](?:\.\d+)?배",
    re.IGNORECASE,
)


class KISError(RuntimeError):
    pass


class KISClient:
    """Read-only KIS client for rankings, current price and minute history."""

    _request_lock = threading.Lock()
    _request_at = 0.0

    def __init__(self, cache_root: str | Path = ".scanner_data/auth", auth_store: CockroachBarStore | None = None):
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._instruments = MasterCatalog(self.cache_root.parent / "instruments")
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._approval_key = ""
        self._approval_expires = datetime.min.replace(tzinfo=UTC)
        self._auth_store = auth_store if auth_store is not None else CockroachBarStore.from_environment()

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _token_path(self) -> Path:
        return self.cache_root / "token.json"

    def access_token(self) -> str:
        # Publication must be inside the issuance lock, including DB/cache write.
        with FileLock(str(self._token_path()) + ".issuance.lock", timeout=45):
            return self._access_token_locked()

    def _access_token_locked(self) -> str:
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
                try:
                    durable = self._auth_store.load_auth("kis_access_token")
                except Exception as exc:
                    raise KISError("KIS 토큰 영구 캐시 읽기 실패 · 중복 발급 방지를 위해 발급 중지") from exc
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
            with self._lock:
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
            try:
                saved = self._auth_store.save_auth("kis_access_token", token, pd.Timestamp(expiry))
            except Exception as exc:
                raise KISError("KIS 토큰 영구 저장 실패 · 재시작 중복 발급 위험으로 사용 중지") from exc
            if saved is not True:
                raise KISError("KIS 토큰 영구 저장 성공 미확인 · 재시작 중복 발급 위험으로 사용 중지")
        return token

    def websocket_approval_key(self) -> str:
        path = self.cache_root / "websocket_approval.json"
        with FileLock(str(path) + ".issuance.lock", timeout=45):
            now = datetime.now(UTC)
            if self._approval_key and self._approval_expires > now + timedelta(minutes=10):
                return self._approval_key

            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    key = str(payload["approval_key"])
                    expiry = datetime.fromisoformat(payload["expires_at"])
                    if key and expiry > now + timedelta(minutes=10):
                        if self._auth_store is not None and payload.get("durable_published", True) is not True:
                            try:
                                saved = self._auth_store.save_auth(
                                    "kis_websocket_approval", key, pd.Timestamp(expiry)
                                )
                            except Exception as exc:
                                raise KISError("WebSocket 접속키 영구 저장 실패 · 재발급 없이 사용 중지") from exc
                            if saved is not True:
                                raise KISError("WebSocket 접속키 영구 저장 성공 미확인 · 재발급 없이 사용 중지")
                            payload["durable_published"] = True
                            temporary = path.with_suffix(".tmp")
                            temporary.write_text(json.dumps(payload), encoding="utf-8")
                            temporary.replace(path)
                        self._approval_key, self._approval_expires = key, expiry
                        return key
                except (OSError, ValueError, KeyError, TypeError):
                    pass

            if self._auth_store is not None:
                try:
                    durable = self._auth_store.load_auth("kis_websocket_approval")
                except Exception as exc:
                    raise KISError("WebSocket 접속키 영구 캐시 읽기 실패 · 재발급 중지") from exc
                if durable is not None:
                    key, expiry_value = durable
                    expiry = expiry_value.to_pydatetime()
                    if key and expiry > now + timedelta(minutes=10):
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(
                            json.dumps(
                                {"approval_key": key, "expires_at": expiry.isoformat(), "durable_published": True}
                            ),
                            encoding="utf-8",
                        )
                        temporary.replace(path)
                        self._approval_key, self._approval_expires = key, expiry
                        return key

            if not self.configured:
                raise KISError("KIS_APP_KEY/KIS_APP_SECRET 환경변수가 필요합니다.")
            with self._lock:
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
            expiry = datetime.now(UTC) + timedelta(hours=23)
            temporary = path.with_suffix(".tmp")
            approval_payload = {
                "approval_key": key,
                "expires_at": expiry.isoformat(),
                "durable_published": self._auth_store is None,
            }
            temporary.write_text(json.dumps(approval_payload), encoding="utf-8")
            temporary.replace(path)
            if self._auth_store is not None:
                try:
                    saved = self._auth_store.save_auth("kis_websocket_approval", key, pd.Timestamp(expiry))
                except Exception as exc:
                    raise KISError("WebSocket 접속키 영구 저장 실패 · 재발급 위험으로 사용 중지") from exc
                if saved is not True:
                    raise KISError("WebSocket 접속키 영구 저장 성공 미확인 · 재발급 위험으로 사용 중지")
                approval_payload["durable_published"] = True
                temporary.write_text(json.dumps(approval_payload), encoding="utf-8")
                temporary.replace(path)
            self._approval_key, self._approval_expires = key, expiry
            return key

    def _throttle(self) -> None:
        with KISClient._request_lock:
            wait = KISClient._request_at + 0.25 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            KISClient._request_at = time.monotonic()

    def get(self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "") -> tuple[dict[str, Any], str]:
        response = None
        for attempt in range(3):
            token = self.access_token()
            self._throttle()
            started = time.perf_counter()
            try:
                with self._lock:
                    response = self.session.request(
                        "GET",
                        f"{self.base_url}{path}",
                        headers={
                            "authorization": f"Bearer {token}",
                            "appkey": self.app_key,
                            "appsecret": self.app_secret,
                            "tr_id": tr_id,
                            "tr_cont": tr_cont,
                            "custtype": "P",
                        },
                        params=params,
                        timeout=15,
                    )
            except (requests.ConnectionError, requests.Timeout) as exc:
                logging.getLogger(__name__).warning(
                    "kis_transport_retry tr_id=%s attempt=%s error=%s",
                    tr_id,
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt == 2:
                    raise KISError(f"{tr_id} KIS 전송 실패({type(exc).__name__})") from exc
                time.sleep((0.35 * (2**attempt)) + random.uniform(0.0, 0.15))
                continue
            logging.getLogger(__name__).info("kis_request tr_id=%s attempt=%s elapsed_s=%.3f status=%s",
                                            tr_id, attempt + 1, time.perf_counter() - started, response.status_code)
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
                    number = float(str(value).replace(",", ""))
                    if math.isfinite(number):
                        return number
            except (TypeError, ValueError):
                continue
        raise KISError(f"숫자 필드 누락 또는 오류: {','.join(keys)}")

    @classmethod
    def _domestic_rank_candidate(cls, row: dict[str, Any], source: str) -> Candidate | None:
        symbol = str(row.get("mksc_shrn_iscd") or "").strip()
        if not symbol:
            return None
        name = str(row.get("hts_kor_isnm") or symbol).strip()
        change_pct = cls._number(row, "prdy_ctrt")
        normalized_name = unicodedata.normalize("NFKC", name)
        change_sign = str(row.get("prdy_vrss_sign") or "").strip()
        # KIS documents sign 1 as upper-limit and sign 2 as an ordinary rise.
        # Only use the percentage fallback when the sign is unavailable; a
        # valid sign=2 row near the limit is not yet an upper-limit row.
        limit_up = change_sign == "1" or (
            change_sign not in {"1", "2", "3", "4", "5"}
            and change_pct >= DOMESTIC_LIMIT_UP_FALLBACK_PCT
        )
        if limit_up or _DOMESTIC_LEVERAGED_NAME.search(normalized_name):
            return None
        return Candidate(
            symbol=symbol,
            name=name,
            price=cls._number(row, "stck_prpr"),
            change_pct=change_pct,
            volume=cls._number(row, "acml_vol"),
            turnover=cls._number(row, "acml_tr_pbmn"),
            sources=frozenset({source}),
        )

    def _ranking(self, sort_code: str, source: str, limit: int = 100) -> list[Candidate]:
        candidates: list[Candidate] = []
        continuation = ""
        for _ in range(10):
            payload, next_continuation = self.get(
                "/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
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
            for row in payload.get("output", []):
                if not isinstance(row, dict):
                    continue
                candidate = self._domestic_rank_candidate(row, source)
                if candidate is not None:
                    candidates.append(candidate)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit or next_continuation not in {"M", "F"}:
                break
            continuation = "N"
        return candidates

    def trading_policy(self, candidate: Candidate):
        from .policy import instrument_policy
        policy = instrument_policy(candidate.market, candidate.symbol, candidate.session)
        if policy.product == "UNKNOWN":
            product = self._instruments.product(candidate)
            policy = instrument_policy(candidate.market, candidate.symbol, candidate.session, verified_product=product)
        return policy

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
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        rows: list[dict[str, Any]] = []
        keyb = before
        used_cursors = {before} if before else set()
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
            try:
                batch_times = [
                    datetime.strptime(
                        f"{row['xymd']}{str(row['xhms']).zfill(6)}", "%Y%m%d%H%M%S"
                    )
                    for row in batch
                ]
            except (KeyError, TypeError, ValueError) as exc:
                raise KISError("해외 분봉 페이지 시간 파싱 실패") from exc
            next_keyb = (min(batch_times) - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
            if next_keyb in used_cursors:
                raise KISError("해외 분봉 페이지 진행 중단: 동일 시간 반복")
            if keyb and next_keyb >= keyb:
                raise KISError("해외 분봉 페이지 진행 중단: 시간이 과거로 이동하지 않음")
            used_cursors.add(next_keyb)
            keyb = next_keyb
        records = []
        for row in rows[:max_records]:
            try:
                timestamp = pd.to_datetime(f"{row['xymd']}{str(row['xhms']).zfill(6)}", format="%Y%m%d%H%M%S")
                records.append({"timestamp": timestamp, "open": float(row["open"]), "high": float(row["high"]),
                                "low": float(row["low"]), "close": float(row["last"]), "volume": float(row["evol"])})
            except (KeyError, TypeError, ValueError) as exc:
                raise KISError("해외 분봉 파싱 실패") from exc
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return normalize_bars(pd.DataFrame(records).set_index("timestamp"))

    def minute_day(self, symbol: str, business_date: str, *, full_day: bool = False) -> pd.DataFrame:
        """Latest page for live refresh; bounded backward paging for history."""
        end = "153000"
        local_now = datetime.now(ZoneInfo("Asia/Seoul"))
        if business_date == local_now.strftime("%Y%m%d"):
            end = min(end, local_now.strftime("%H%M%S"))
        frames = []
        for _ in range(8 if full_day else 1):
            frame = self._minute_page(symbol, business_date, end)
            if frame.empty:
                break
            frame = frame.loc[frame.index.strftime("%Y%m%d") == business_date]
            if frame.empty:
                break
            frames.append(frame)
            earliest = frame.index.min()
            if earliest.strftime("%H%M%S") <= "090000":
                break
            next_end = (earliest - pd.Timedelta(minutes=1)).strftime("%H%M%S")
            if next_end >= end:
                raise KISError("국내 분봉 페이지 진행 중단: 동일 시간 반복")
            end = next_end
        return normalize_bars(pd.concat(frames)) if frames else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def _minute_page(self, symbol: str, business_date: str, end: str) -> pd.DataFrame:
        payload, _ = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
            "FHKST03010230",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": end,
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
            except (KeyError, TypeError, ValueError) as exc:
                raise KISError("국내 분봉 파싱 실패") from exc
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return normalize_bars(pd.DataFrame(records).set_index("timestamp"))
