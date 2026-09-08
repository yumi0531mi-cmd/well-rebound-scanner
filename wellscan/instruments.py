"""Public KIS security master classification; no quote or broker-order logic.

Schema: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
Only explicit ST / US security-type 2 is classified STOCK. ETPs stay UNKNOWN
until leverage/tax metadata or an explicit deployment policy is supplied.
"""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests
from filelock import FileLock

from .models import Candidate, Market

BASE = "https://new.real.download.dws.co.kr/common/master/"
SOURCES = {Market.KR: ("kospi_code.mst", "kosdaq_code.mst"),
           Market.US: ("nasmst.cod", "nysmst.cod", "amsmst.cod")}


def parse_master(name: str, raw: bytes) -> dict[str, str]:
    result = {}
    for line in raw.decode("cp949").splitlines():
        if not line.strip():
            continue
        if name.endswith(".cod"):
            fields = line.split("\t")
            if len(fields) != 24:
                raise ValueError(f"{name}: expected 24 fields, got {len(fields)}")
            security_type = fields[8].strip()
            if security_type not in {"1", "2", "3", "4"}:
                raise ValueError(f"{name}: unknown security type")
            symbol = fields[4].strip().upper()
            product = "STOCK" if security_type == "2" else "UNKNOWN"
            key = f"US:{name[:3].upper()}:{symbol}"
        else:
            # Official text-mode slices include the newline: 228/222.
            # splitlines removed that one character: 227/221 remain.
            tail = {"kospi_code.mst": 227, "kosdaq_code.mst": 221}[name]
            if len(line) < tail + 22:
                raise ValueError(f"{name}: truncated fixed-width record")
            symbol = line[:9].strip()
            group = line[-tail:][:2]
            if not 1 <= len(symbol) <= 9 or not symbol.isalnum() or not group.isalpha():
                raise ValueError(f"{name}: invalid code/group boundary")
            product = "STOCK" if group == "ST" else "UNKNOWN"
            key = f"KR:KRX:{symbol}"
        if not symbol or key in result:
            raise ValueError(f"{name}: missing/duplicate symbol")
        result[key] = product
    if not result:
        raise ValueError(f"{name}: empty master")
    return result


class MasterCatalog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._cache: dict[Market, dict] = {}
        self._failure: dict[Market, tuple[float, str]] = {}

    @staticmethod
    def _fresh(payload: dict) -> bool:
        stamp = datetime.fromisoformat(payload["retrieved_at"])
        return payload.get("schema") == 1 and stamp.tzinfo is not None and 0 <= (datetime.now(UTC) - stamp).total_seconds() < 86400

    def refresh(self, market: Market) -> dict:
        """Heavy-worker only. One daily batch, failures explicit and rate-limited."""
        with self._lock:
            cached = self._cache.get(market)
            if cached is not None and self._fresh(cached):
                return cached
            failure = self._failure.get(market)
            if failure and time.monotonic() - failure[0] < 300:
                raise RuntimeError(failure[1])
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{market.value}.json"
            try:
                with FileLock(str(path) + ".lock", timeout=20):
                    if path.exists():
                        cached = json.loads(path.read_text(encoding="utf-8"))
                        if self._fresh(cached):
                            self._cache[market] = cached
                            return cached
                    products = {}
                    for name in SOURCES[market]:
                        response = requests.get(BASE + name + ".zip", timeout=(5, 15))
                        response.raise_for_status()  # TLS verification remains enabled.
                        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                            matches = [entry for entry in archive.namelist() if entry.casefold() == name.casefold()]
                            if len(matches) != 1:
                                raise ValueError("master archive member missing or ambiguous")
                            member = matches[0]
                            info = archive.getinfo(member)
                            if info.file_size > 20_000_000:
                                raise ValueError("master archive too large")
                            parsed = parse_master(name, archive.read(member))
                        if products.keys() & parsed.keys():
                            raise ValueError("conflicting security master keys")
                        products.update(parsed)
                    payload = {"schema": 1, "retrieved_at": datetime.now(UTC).isoformat(),
                               "source": BASE, "products": products}
                    temporary = path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(payload), encoding="utf-8")
                    temporary.replace(path)
                    self._cache[market] = payload
                    return payload
            except Exception as exc:
                message = f"상품 마스터 확인 실패: {type(exc).__name__}: {exc}"
                self._failure[market] = (time.monotonic(), message)
                raise RuntimeError(message) from exc

    def product(self, candidate: Candidate) -> str:
        payload = self.refresh(candidate.market)
        key = f"{candidate.market.value}:{candidate.exchange}:{candidate.symbol.upper()}"
        return payload["products"].get(key, "UNKNOWN")
