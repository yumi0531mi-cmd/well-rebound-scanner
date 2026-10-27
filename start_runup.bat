@echo off
cd /d C:\Users\cj123\Documents\Codex\2026-08-27\e\work\strategy-filter-reentry
start "" /min .\.venv\Scripts\pythonw.exe -m streamlit run runup_app.py --server.address 0.0.0.0 --server.port 8502 --server.headless true --browser.gatherUsageStats false
