#!/usr/bin/env python3
"""
JARVIS Web Search — DuckDuckGo HTML scraper.

No API key. No external deps beyond the stdlib. Hits DDG's HTML endpoint,
extracts title / URL / snippet from the result blocks, and returns them as a
plain list of dicts. Called from jarvis_chat._search_worker().

SAFETY: every public function catches all exceptions and returns an empty
result / error string — the caller must never crash due to a search failure.
"""
from __future__ import annotations

import html as _html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

_DDG_HTML   = "https://html.duckduckgo.com/html/"
_FETCH_UA   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0.0.0 Safari/537.36")

# DDG HTML structure (simplified — the real page has more attributes but these
# class names have been stable for years):
#   <a class="result__a" href="...">Title text</a>
#   <a class="result__snippet" ...>Snippet text</a>
#   <a class="result__url" ...>domain.com/path</a>
_TITLE_RE   = re.compile(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',   re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>',                 re.DOTALL)
_URL_RE     = re.compile(r'class="result__url"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',  re.DOTALL)
_TAG_RE     = re.compile(r'<[^>]+>')
_WS_RE      = re.compile(r'\s+')

# Rate-limit guard: don't fire two searches within 2 s of each other.
_last_search: float = 0.0


def _clean(text: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def search_ddg(query: str, max_results: int = 5) -> list[dict]:
    """Return up to `max_results` results for `query` as a list of
    {title, url, snippet} dicts.  Returns [] on any error."""
    global _last_search
    try:
        # Gentle rate-limit
        gap = time.time() - _last_search
        if gap < 2.0:
            time.sleep(2.0 - gap)
        _last_search = time.time()

        params = urllib.parse.urlencode({"q": query, "kl": "us-en"})
        req = urllib.request.Request(
            f"{_DDG_HTML}?{params}",
            headers={
                "User-Agent":      _FETCH_UA,
                "Accept":          "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", "ignore")

        titles   = _TITLE_RE.findall(raw)    # [(href, title_html), ...]
        snippets = _SNIPPET_RE.findall(raw)  # [snippet_html, ...]
        urls_dsp = _URL_RE.findall(raw)      # [(href, display_html), ...]

        results = []
        for i, (href, title_html) in enumerate(titles[:max_results]):
            title   = _clean(title_html)
            snippet = _clean(snippets[i]) if i < len(snippets) else ""
            # Prefer the display URL string; fall back to the raw href
            if i < len(urls_dsp):
                url = _clean(urls_dsp[i][1]) or _clean(href)
            else:
                url = _clean(href)
            if title:
                results.append({"title": title, "url": url, "snippet": snippet})
        return results

    except Exception:
        return []


def format_results(results: list[dict], query: str) -> str:
    """Format search results as the context block injected into the system
    prompt before JARVIS answers. Empty results → short error note."""
    if not results:
        return (
            f"WEB SEARCH: query was '{query}' but no results were returned "
            f"(network issue or DDG blocked the request). "
            f"Answer from your training knowledge and note you couldn't fetch live results."
        )
    lines = [f"LIVE WEB SEARCH RESULTS for: {query!r}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    {r['url']}")
        if r['snippet']:
            lines.append(f"    {r['snippet']}")
    lines.append(
        "\nAnswer the user's question using these results. "
        "Cite sources inline by number [1] [2] etc. "
        "If the results don't contain the answer, say so clearly rather than guessing."
    )
    return "\n".join(lines)


def search_status() -> str:
    """Quick connectivity check — 'ok' or an error message."""
    try:
        req = urllib.request.Request(
            _DDG_HTML,
            headers={"User-Agent": _FETCH_UA},
        )
        urllib.request.urlopen(req, timeout=5).close()
        return "ok"
    except Exception as e:
        return f"unreachable: {e}"
