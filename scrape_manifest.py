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
    """Blocking fetch — called inside a thread so the caller can enforce a hard timeout.

    source='requests' forces plain requests regardless of comic source, used
    for the GoComics no-date URL which doesn't need cloudscraper.
    """
    if source == 'gocomics':
        scraper = cloudscraper.create_scraper()
        return scraper.get(page_url, headers=HEADERS, timeout=(8, 15))
    return requests.get(page_url, headers=HEADERS, timeout=(8, 15))


def _fetch_gocomics_with_session(no_date_url, dated_url, verbose=False):
    """Two-step GoComics fetch that works on GitHub-hosted runners.

    Step 1: Use cloudscraper on the no-date URL to solve BunnyShield PoW
            and obtain the session cookies (bunny_shield_id + INGRESSCOOKIE).
    Step 2: Reuse those cookies via a requests.Session on the dated URL.
            The cookies make GoComics return the full page regardless of IP.
    """
    try:
        scraper = cloudscraper.create_scraper()
        _log(verbose, f"    [session] Solving BunnyShield challenge via {no_date_url}")
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(scraper.get, no_date_url, timeout=(8, 20))
            warm_resp = future.result(timeout=_REQUEST_TIMEOUT + 5)
        _log(verbose, f"    [session] Step 1 HTTP {warm_resp.status_code} — cookies: {list(dict(warm_resp.cookies).keys())}")
        if warm_resp.status_code != 200 or _is_challenge_page(warm_resp.text):
            _log(verbose, "    [session] Step 1 blocked; falling back to standard fetch.")
            return None, None

        # Build a requests.Session carrying the solved cookies
        session_ua = warm_resp.request.headers.get('User-Agent', HEADERS['User-Agent'])
        sess = requests.Session()
        sess.headers.update({
            'User-Agent': session_ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.gocomics.com/',
        })
        sess.cookies.update(dict(warm_resp.cookies))

        _log(verbose, f"    [session] Step 2 fetching dated URL {dated_url}")
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(sess.get, dated_url, timeout=(8, 15))
            dated_resp = future.result(timeout=_REQUEST_TIMEOUT)
        _log(verbose, f"    [session] Step 2 HTTP {dated_resp.status_code} body={len(dated_resp.text)}")
        return warm_resp, dated_resp

    except FuturesTimeout:
        _log(verbose, "    [session] Timeout during session fetch.")
        return None, None
    except Exception as e:
        _log(verbose, f"    [session] Session fetch error: {e}")
        return None, None

def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_page_url(comic, date):
    base = comic['url'].rstrip('/')
    if comic['source'] == 'gocomics':
        return f"{base}/{date.strftime('%Y/%m/%d')}/"
    else:  # comicskingdom
        return f"{base}/{date.strftime('%Y-%m-%d')}"

def build_no_date_url(comic):
    """Return the dateless GoComics URL (always redirects to today's strip)."""
    return comic['url'].rstrip('/')

def _extract_image_from_html(html, source):
    soup = BeautifulSoup(html, 'html.parser')

    # og:image works for both GoComics and Comics Kingdom.
    # For GoComics no-date pages, og:image is a static social preview, NOT the strip —
    # so we skip it when it points to gocomicscmsassets (the CMS/static asset host).
    meta = soup.find('meta', property='og:image')
    if meta and meta.get('content'):
        url = meta['content'].strip()
        if (url
                and not url.endswith('default')
                and 'placeholder' not in url.lower()
                and 'gocomicscmsassets' not in url):
            return url

    # GoComics: first featureassets <img> src is the actual strip image.
    # This works on both dated and no-date pages.
    if source == 'gocomics':
        for img in soup.find_all('img'):
            src = img.get('src', '')
            m = _FEATURE_ASSET_RE.match(src)
            if m:
                return m.group(0).partition('?')[0]

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
    """Return True only if the page is a bot-challenge interstitial, not real content."""
    lowered = html.lower()
    # Must match a structural challenge marker, not just the word appearing in content
    challenge_markers = (
        'cf-challenge',
        'just a moment</title>',
        'attention required</title>',
        '/cdn-cgi/challenge-platform/',
        'bunny-shield/assets/shield-challenge',
        'bunny_shield_id',
        'establishing a secure connection</title>',
    )
    return any(marker in lowered for marker in challenge_markers)


def extract_image_url(page_url, source, verbose=False, no_date_url=None):
    """Return the comic image URL from the page, or None on failure.

    For GoComics today: use the two-step session approach —
      1. cloudscraper on the no-date URL to solve BunnyShield and get cookies.
      2. Plain requests with those cookies on the dated URL.
    This bypasses the datacenter-IP 403 that GitHub-hosted runners hit.

    For GoComics backfill or if no_date_url is not provided: standard cloudscraper fetch.
    """
    # --- GoComics today: two-step session strategy ---
    if source == 'gocomics' and no_date_url:
        warm_resp, dated_resp = _fetch_gocomics_with_session(no_date_url, page_url, verbose=verbose)
        if dated_resp is not None and dated_resp.status_code == 200:
            parsed = _extract_image_from_html(dated_resp.text, source)
            if parsed:
                return parsed
            _log(verbose, "    [session] Dated page parse found no image; trying no-date page.")
            # Fall back to extracting from the already-fetched no-date page
            if warm_resp is not None:
                parsed = _extract_image_from_html(warm_resp.text, source)
                if parsed:
                    return parsed
        _log(verbose, "    [session] Two-step session failed; trying standard fetch.")

    # --- Standard fetch (backfill, or session approach failed) ---
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_fetch, page_url, source)
            resp = future.result(timeout=_REQUEST_TIMEOUT)
        _log(verbose, f"    [direct] HTTP {resp.status_code} from {resp.url}")
        _log(verbose, f"    [direct] Content-Type: {resp.headers.get('Content-Type', '<missing>')}")
        _log(verbose, f"    [direct] Body bytes: {len(resp.text)}")
        resp.raise_for_status()
        if source == 'gocomics' and _is_challenge_page(resp.text):
            _log(verbose, "    [direct] Detected challenge page; moving to proxy fallbacks.")
        parsed = _extract_image_from_html(resp.text, source)
        if parsed:
            return parsed
        _log(verbose, "    [direct] Parse found no image; trying proxy fallbacks.")
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

def fallback_previous_manifest_url(comic_id, date, manifest_dirs, lookback_days=30):
    """Use the most recent known URL for this comic when today's scrape is blocked."""
    for manifest_dir in manifest_dirs:
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

def scrape_date(config, date, manifest_dir='manifest', fallback_manifest_dir=None, verbose=False):
    date_str = date.strftime('%Y-%m-%d')
    is_today = (date == datetime.date.today())
    print(f"\nScraping {date_str}" + (" [today — no-date URL strategy active]" if is_today else ""))
    manifest = {}

    for comic in config['comics']:
        page_url = build_page_url(comic, date)
        # For today's GoComics scrape, pass the no-date URL so the scraper
        # tries it first with plain requests — avoids the datacenter-IP 403
        # that the dated URL triggers on GitHub-hosted runners.
        no_date_url = build_no_date_url(comic) if (is_today and comic['source'] == 'gocomics') else None
        print(f"  {comic['id']}")
        image_url = extract_image_url(page_url, comic['source'], verbose=verbose, no_date_url=no_date_url)
        if not image_url and comic['source'] == 'gocomics':
            fallback_dirs = [manifest_dir]
            if fallback_manifest_dir and fallback_manifest_dir not in fallback_dirs:
                fallback_dirs.append(fallback_manifest_dir)
            image_url = fallback_previous_manifest_url(comic['id'], date, manifest_dirs=fallback_dirs)
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
    parser.add_argument(
        '--fallback-manifest-dir',
        default=None,
        help='Optional secondary manifest directory to use for historical URL fallback.',
    )
    parser.add_argument('--verbose', action='store_true', help='Enable verbose scrape diagnostics.')
    parser.add_argument('legacy_date', nargs='*', help='Legacy positional date args: YYYY MM DD')
    args = parser.parse_args()

    config = load_config(args.config)

    if args.backfill:
        for date in daterange(args.backfill[0], args.backfill[1]):
            scrape_date(
                config,
                date,
                manifest_dir=args.manifest_dir,
                fallback_manifest_dir=args.fallback_manifest_dir,
                verbose=args.verbose,
            )
    elif len(args.legacy_date) == 3:
        # Legacy Y M D positional args kept for GitHub Actions compatibility
        date = datetime.date(int(args.legacy_date[0]), int(args.legacy_date[1]), int(args.legacy_date[2]))
        scrape_date(
            config,
            date,
            manifest_dir=args.manifest_dir,
            fallback_manifest_dir=args.fallback_manifest_dir,
            verbose=args.verbose,
        )
    elif len(args.legacy_date) == 0:
        scrape_date(
            config,
            datetime.date.today(),
            manifest_dir=args.manifest_dir,
            fallback_manifest_dir=args.fallback_manifest_dir,
            verbose=args.verbose,
        )
    else:
        print("Usage: scrape_manifest.py [--backfill YYYY-MM-DD YYYY-MM-DD] [--config PATH] [--manifest-dir DIR] [--verbose] [YYYY MM DD]")
        sys.exit(1)

    cleanup_old_manifests(manifest_dir=args.manifest_dir, keep_days=MANIFEST_KEEP_DAYS)

if __name__ == '__main__':
    main()
