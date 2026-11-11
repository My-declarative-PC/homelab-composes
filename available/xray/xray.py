#!/usr/bin/env python3
"""Единая точка входа для управления Xray."""

from pathlib import Path

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent))
