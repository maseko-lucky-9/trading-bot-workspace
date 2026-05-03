"""Parallel book-research pipeline.

Spawns one async agent per quant-finance book in the Obsidian vault, extracts
trading strategies via Claude API, runs backtests on EURUSD M15 2020-2024, and
produces a unified comparison MOC ranked by deflated Sharpe ratio.

See ``/Users/ltmas/.claude/plans/i-wanna-make-the-steady-eagle.md`` for the design.
"""
