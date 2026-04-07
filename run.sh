#!/usr/bin/env bash
export PYTHONPATH=.
uvicorn app.main:app --reload --ws-ping-interval 30 --ws-ping-timeout 120
