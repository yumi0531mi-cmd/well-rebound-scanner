import pytest

from wellscan.instruments import parse_master


def test_us_security_type_not_name_determines_stock():
    row = [""] * 24
    row[4], row[8] = "TEST", "2"
    assert parse_master("nasmst.cod", "\t".join(row).encode())["US:NAS:TEST"] == "STOCK"
    row[8] = "3"
    assert parse_master("nasmst.cod", "\t".join(row).encode())["US:NAS:TEST"] == "UNKNOWN"


@pytest.mark.parametrize("name,tail", [("kospi_code.mst", 227), ("kosdaq_code.mst", 221)])
def test_kr_fixed_width_type(name, tail):
    prefix = "005930   " + "KR7005930003" + "삼성전자"
    raw = (prefix + "ST" + " " * (tail - 2) + "\r\n").encode("cp949")
    assert parse_master(name, raw)["KR:KRX:005930"] == "STOCK"
    raw = (prefix + "EF" + " " * (tail - 2) + "\n").encode("cp949")
    assert parse_master(name, raw)["KR:KRX:005930"] == "UNKNOWN"


@pytest.mark.parametrize("name", ["nasmst.cod", "kospi_code.mst"])
def test_truncated_master_is_error(name):
    with pytest.raises(ValueError):
        parse_master(name, b"bad")


def test_verified_master_used_only_when_no_explicit_policy(tmp_path, monkeypatch):
    from wellscan.kis import KISClient
    from wellscan.models import Candidate
    client = KISClient(tmp_path / "auth")
    monkeypatch.delenv("WELLSCAN_INSTRUMENT_POLICIES", raising=False)
    monkeypatch.setattr(client._instruments, "product", lambda _: "STOCK")
    candidate = Candidate("005930", "mock", 100, 0, 100, 10000)
    assert client.trading_policy(candidate).product == "STOCK"
    monkeypatch.setenv("WELLSCAN_INSTRUMENT_POLICIES", '{"KR:005930":{"product":"UNKNOWN"}}')
    assert client.trading_policy(candidate).product == "UNKNOWN"


def test_master_failure_not_replaced_by_guessed_stock(tmp_path, monkeypatch):
    from wellscan.kis import KISClient
    from wellscan.models import Candidate
    client = KISClient(tmp_path / "auth")
    monkeypatch.delenv("WELLSCAN_INSTRUMENT_POLICIES", raising=False)
    def fail(_):
        raise RuntimeError("mock master outage")
    monkeypatch.setattr(client._instruments, "product", fail)
    with pytest.raises(RuntimeError, match="outage"):
        client.trading_policy(Candidate("005930", "mock", 100, 0, 100, 10000))


def test_daily_master_cache_and_uppercase_archive(tmp_path, monkeypatch):
    import io
    import zipfile

    from wellscan.instruments import MasterCatalog
    from wellscan.models import Market
    calls = []
    def download(url, **kwargs):
        assert kwargs.get("verify", True) is True
        calls.append(url)
        row = [""] * 24
        row[4], row[8] = "TEST", "2"
        buffer = io.BytesIO()
        name = url.rsplit("/", 1)[-1][:-4].upper()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, "\t".join(row))
        class Response:
            content = buffer.getvalue()
            def raise_for_status(self):
                pass
        return Response()
    monkeypatch.setattr("wellscan.instruments.requests.get", download)
    catalog = MasterCatalog(tmp_path)
    assert len(catalog.refresh(Market.US)["products"]) == 3
    assert len(MasterCatalog(tmp_path).refresh(Market.US)["products"]) == 3
    assert len(calls) == 3


def test_failed_master_refresh_has_retry_backoff(tmp_path, monkeypatch):
    from wellscan.instruments import MasterCatalog
    from wellscan.models import Market
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("mock outage")
    monkeypatch.setattr("wellscan.instruments.requests.get", fail)
    catalog = MasterCatalog(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="mock outage"):
            catalog.refresh(Market.US)
    assert len(calls) == 1
