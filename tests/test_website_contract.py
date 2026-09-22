"""Контракт лендинга: файлы сайта и вызовы API, которые ждёт bot-payment."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"


@pytest.fixture(scope="module")
def index_html() -> str:
    path = WEBSITE / "index.html"
    assert path.exists(), f"нет {path}"
    return path.read_text(encoding="utf-8")


def test_website_required_files():
    for name in (
        "index.html",
        "offer.html",
        "success.html",
        "vercel.json",
        "robots.txt",
        "sitemap.xml",
    ):
        assert (WEBSITE / name).exists(), name


def test_website_calls_bot_api_endpoints(index_html: str):
    assert "LIFE_API" in index_html
    assert "/api/intent" in index_html
    assert "/api/behavior" in index_html
    assert "/api/dates" in index_html
    assert "million.up.railway.app" in index_html or "LIFE_API" in index_html


def test_website_has_telegram_cta(index_html: str):
    assert "t.me" in index_html or "telegram" in index_html.lower()


def test_offer_mentions_payment_terms():
    text = (WEBSITE / "offer.html").read_text(encoding="utf-8")
    assert len(text) > 500
