"""Small, escaped live-price payload; no history, persistence or API calls."""
from html import escape


def live_price_table(rows: list[tuple[str, str, str, str, str, str]]) -> str:
    headers = ("종목", "현재가", "진입가 / T1", "Hard Stop", "현재 조건", "수신 KST / 경로")
    head = "".join(f"<th>{title}</th>" for title in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div style="overflow-x:auto"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
