#!/usr/bin/env bash
set -e
python3 -m pip install -q -r requirements.txt
exec uvicorn app.main:app --reload --port 8000
