    def minute_day(self, symbol: str, business_date: str) -> pd.DataFrame:
        """하루치 1분봉 전체 수집 (120개씩 시간 커서로 역방향 페이지네이션)."""
        all_rows: list[dict[str, Any]] = []
        seen_times: set[str] = set()
        cursor = "153000"
        for _ in range(6):   # 120개 × 6 = 최대 720개 (390개면 충분, 여유 포함)
            payload, _ = self.get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
                "FHKST03010230",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": cursor,
                    "FID_INPUT_DATE_1": business_date,
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_FAKE_TICK_INCU_YN": "",
                },
            )
            rows = [row for row in payload.get("output2", []) if isinstance(row, dict)]
            new_rows = []
            for row in rows:
                t = str(row.get("stck_cntg_hour") or "").zfill(6)
                if t not in seen_times:
                    seen_times.add(t)
                    new_rows.append(row)
            all_rows.extend(new_rows)
            if len(new_rows) < 120 or not new_rows:
                break
            # 이번 배치 중 가장 이른 시간을 다음 커서로
            times = sorted(str(r.get("stck_cntg_hour") or "").zfill(6) for r in new_rows)
            earliest = times[0]
            if earliest <= "090100":
                break
            cursor = earliest
        records = []
        for row in all_rows:
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
