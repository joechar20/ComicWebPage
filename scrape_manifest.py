"""
Scrape comic image URLs and write them to manifest/YYYY-MM-DD.json.
Images are never downloaded — only the CDN URL is recorded.

Usage:
  python scrape_manifest.py                          # today
  python scrape_manifest.py 2026 08 13               # specific date (Y M D)
  python scrape_manifest.py --backfill 2026-08-10 2026-08-13  # inclusive range
"""

import argparse
import datetime
import json
import re
import socket
import sys
from urllib.parse import quote_plus
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import cloudscraper
import requests
from bs4 import BeautifulSoup

# Hard wall-clock timeout for any single HTTP request (SSL hangs bypass normal timeouts)
_REQUEST_TIMEOUT = 25

# Number of daily manifest files to keep before cleanup removes older files.
MANIFEST_KEEP_DAYS = 90

socket.setdefaulttimeout(20)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    )
}

_FEATURE_ASSET_RE = re.compile(
    r"https?://(?:featureassets|assets)\.gocomics\.com/assets/[a-zA-Z0-9]+(?:\?[^\s\"'<>)]+)?"
)


def _log(verbose, message):
    if verbose:
        print(message)


def _do_fetch(page_url, source):
    """Blocking fetch — called inside a thread so the caller can enforce a hard timeout."""
    if source == 'gocomics':
        scraper = cloudscraper.create_scraper()
        return scraper.get(page_url, headers=HEADERS, timeout=(8, 15))
    return requests.get(page_url, headers=HEADERS, timeout=(8, 15))

def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_page_url(comic, date):
    base = comic['url'].rstrip('/')
    if comic['source'] == 'gocomics':
        return f"{base}/{date.strftime('%Y/%m/%d')}/"
    else:  # comicskingdom
        return f"{base}/{date.strftime('%Y-%m-%d')}"

def _extract_image_from_html(html, source):
    soup = BeautifulSoup(html, 'html.parser')

    # og:image works for both GoComics and Comics Kingdom
    meta = soup.find('meta', property='og:image')
    if meta and meta.get('content'):
        url = meta['content'].strip()
        if url and not url.endswith('default') and 'placeholder' not in url.lower():
            return url

    # GoComics fallback: preload link with imagesrcset
    if source == 'gocomics':
        link = soup.find('link', attrs={'rel': 'preload', 'as': 'image'})
        if link:
            srcset = link.get('imagesrcset') or link.get('href', '')
            if srcset:
                first = srcset.split(',')[0].strip().split(' ')[0]
                return first.partition('?')[0]

    # Text fallback for non-HTML proxy outputs (e.g., markdown from r.jina.ai)
    found = _FEATURE_ASSET_RE.search(html)
    if found:
        return found.group(0).partition('?')[0]

    return None

def _is_challenge_page(html):
    lowered = html.lower()
    return any(
        marker in lowered
        for marker in (
            'cf-challenge',
            'just a moment',
            'captcha',
            'attention required',
            '/cdn-cgi/challenge-platform/',
        )
    )


def extract_image_url(page_url, source, verbose=False):
    """Return the comic image URL from the page, or None on failure."""
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_fetch, page_url, source)
            resp = future.result(timeout=_REQUEST_TIMEOUT)
        _log(verbose, f"    HTTP {resp.status_code} from {resp.url}")
        _log(verbose, f"    Content-Type: {resp.headers.get('Content-Type', '<missing>')}")
        _log(verbose, f"    Body bytes: {len(resp.text)}")
        resp.raise_for_status()
        if source == 'gocomics' and _is_challenge_page(resp.text):
            _log(verbose, "    Detected challenge page; moving to proxy fallbacks.")
        parsed = _extract_image_from_html(resp.text, source)
        if parsed:
            return parsed
        _log(verbose, "    Direct scrape parse failed; trying proxy fallbacks.")
    except FuturesTimeout:
        print(f"    TIMEOUT: {page_url}")
    except Exception as e:
        print(f"    FETCH ERROR: {e}")

    # Best-effort proxies for GoComics challenge pages.
    if source == 'gocomics':
        proxy_urls = [
            f"https://api.allorigins.win/raw?url={quote_plus(page_url)}",
            f"https://r.jina.ai/http://{page_url.replace('https://', '').replace('http://', '')}"
        ]

        for proxy_url in proxy_urls:
            try:
                _log(verbose, f"    Proxy attempt: {proxy_url}")
                proxy_resp = requests.get(proxy_url, headers=HEADERS, timeout=(8, 15))
                _log(verbose, f"      Proxy HTTP {proxy_resp.status_code}")
                if not proxy_resp.ok:
                    continue
                parsed = _extract_image_from_html(proxy_resp.text, source)
                if parsed:
                    _log(verbose, "      Proxy parse succeeded.")
                    return parsed
                _log(verbose, "      Proxy parse had no image match.")
            except Exception:
                _log(verbose, "      Proxy request failed.")

    print(f"    WARN: no image URL found at {page_url}")
    return None

def fallback_previous_manifest_url(comic_id, date, manifest_dir, lookback_days=7):
    """Use the most recent known URL for this comic when today's scrape is blocked."""
    for i in range(1, lookback_days + 1):
        prev_date = (date - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        path = Path(manifest_dir) / f"{prev_date}.json"
        if not path.exists():
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            url = data.get(comic_id)
            if url:
                return url
        except (json.JSONDecodeError, OSError):
            continue
    return None

def scrape_date(config, date, manifest_dir='manifest', verbose=False):
    date_str = date.strftime('%Y-%m-%d')
    print(f"\nScraping {date_str}")
    manifest = {}

    for comic in config['comics']:
        page_url = build_page_url(comic, date)
        print(f"  {comic['id']}")
        image_url = extract_image_url(page_url, comic['source'], verbose=verbose)
        if not image_url and comic['source'] == 'gocomics':
            image_url = fallback_previous_manifest_url(comic['id'], date, manifest_dir=manifest_dir)
            if image_url:
                print(f"    Fallback: reused previous manifest URL for {comic['id']}")
        manifest[comic['id']] = image_url
        print(f"    -> {image_url}")

    Path(manifest_dir).mkdir(exist_ok=True)
    out_path = Path(manifest_dir) / f"{date_str}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f"  Written: {out_path}")

    # Keep latest.json pointing to the most recent scraped date
    latest_path = Path(manifest_dir) / 'latest.json'
    try:
        with open(latest_path, 'r', encoding='utf-8') as f:
            latest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        latest = {'latest': ''}

    if date_str > latest.get('latest', ''):
        latest['latest'] = date_str
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(latest, f)

def cleanup_old_manifests(manifest_dir='manifest', keep_days=MANIFEST_KEEP_DAYS):
    cutoff = datetime.date.today() - datetime.timedelta(days=keep_days)
    for f in sorted(Path(manifest_dir).glob('????-??-??.json')):
        try:
            if datetime.date.fromisoformat(f.stem) < cutoff:
                f.unlink()
                print(f"  Removed old manifest: {f.name}")
        except ValueError:
            pass

def daterange(start_str, end_str):
    start = datetime.date.fromisoformat(start_str)
    end   = datetime.date.fromisoformat(end_str)
    current = start
    while current <= end:
        yield current
        current += datetime.timedelta(days=1)

def main():
    parser = argparse.ArgumentParser(description='Scrape comic image URLs into date-based manifests.')
    parser.add_argument('--backfill', nargs=2, metavar=('START', 'END'))
    parser.add_argument('--config', default='comics.json', help='Path to comics config JSON.')
    parser.add_argument('--manifest-dir', default='manifest', help='Output manifest directory.')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose scrape diagnostics.')
    parser.add_argument('legacy_date', nargs='*', help='Legacy positional date args: YYYY MM DD')
    args = parser.parse_args()

    config = load_config(args.config)

    if args.backfill:
        for date in daterange(args.backfill[0], args.backfill[1]):
            scrape_date(config, date, manifest_dir=args.manifest_dir, verbose=args.verbose)
    elif len(args.legacy_date) == 3:
        # Legacy Y M D positional args kept for GitHub Actions compatibility
        date = datetime.date(int(args.legacy_date[0]), int(args.legacy_date[1]), int(args.legacy_date[2]))
        scrape_date(config, date, manifest_dir=args.manifest_dir, verbose=args.verbose)
    elif len(args.legacy_date) == 0:
        scrape_date(config, datetime.date.today(), manifest_dir=args.manifest_dir, verbose=args.verbose)
    else:
        print("Usage: scrape_manifest.py [--backfill YYYY-MM-DD YYYY-MM-DD] [--config PATH] [--manifest-dir DIR] [--verbose] [YYYY MM DD]")
        sys.exit(1)

    cleanup_old_manifests(manifest_dir=args.manifest_dir, keep_days=MANIFEST_KEEP_DAYS)

if __name__ == '__main__':
    main()
