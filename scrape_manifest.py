"""
Scrape comic image URLs and write them to manifest/YYYY-MM-DD.json.
Images are never downloaded — only the CDN URL is recorded.

Usage:
  python scrape_manifest.py                          # today
  python scrape_manifest.py 2026 08 13               # specific date (Y M D)
  python scrape_manifest.py --backfill 2026-08-10 2026-08-13  # inclusive range
"""

import requests
import cloudscraper
import json
import sys
import socket
import datetime
from urllib.parse import quote_plus
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
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


def _do_fetch(page_url, source):
    """Blocking fetch — called inside a thread so the caller can enforce a hard timeout."""
    if source == 'gocomics':
        scraper = cloudscraper.create_scraper()
        return scraper.get(page_url, timeout=(8, 15))
    return requests.get(page_url, headers=HEADERS, timeout=(8, 15))

def load_config():
    with open('comics.json', 'r', encoding='utf-8') as f:
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

    return None

def extract_image_url(page_url, source):
    """Return the comic image URL from the page, or None on failure."""
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_fetch, page_url, source)
            resp = future.result(timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        parsed = _extract_image_from_html(resp.text, source)
        if parsed:
            return parsed
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
                proxy_resp = requests.get(proxy_url, headers=HEADERS, timeout=(8, 15))
                if not proxy_resp.ok:
                    continue
                parsed = _extract_image_from_html(proxy_resp.text, source)
                if parsed:
                    return parsed
            except Exception:
                pass

    print(f"    WARN: no image URL found at {page_url}")
    return None

def fallback_previous_manifest_url(comic_id, date, lookback_days=7):
    """Use the most recent known URL for this comic when today's scrape is blocked."""
    for i in range(1, lookback_days + 1):
        prev_date = (date - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        path = Path('manifest') / f"{prev_date}.json"
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

def scrape_date(config, date):
    date_str = date.strftime('%Y-%m-%d')
    print(f"\nScraping {date_str}")
    manifest = {}

    for comic in config['comics']:
        page_url = build_page_url(comic, date)
        print(f"  {comic['id']}")
        image_url = extract_image_url(page_url, comic['source'])
        if not image_url and comic['source'] == 'gocomics':
            image_url = fallback_previous_manifest_url(comic['id'], date)
            if image_url:
                print(f"    Fallback: reused previous manifest URL for {comic['id']}")
        manifest[comic['id']] = image_url
        print(f"    -> {image_url}")

    Path('manifest').mkdir(exist_ok=True)
    out_path = Path('manifest') / f"{date_str}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f"  Written: {out_path}")

    # Keep latest.json pointing to the most recent scraped date
    latest_path = Path('manifest') / 'latest.json'
    try:
        with open(latest_path, 'r', encoding='utf-8') as f:
            latest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        latest = {'latest': ''}

    if date_str > latest.get('latest', ''):
        latest['latest'] = date_str
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(latest, f)

def cleanup_old_manifests(keep_days=MANIFEST_KEEP_DAYS):
    cutoff = datetime.date.today() - datetime.timedelta(days=keep_days)
    for f in sorted(Path('manifest').glob('????-??-??.json')):
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
    config = load_config()
    args = sys.argv[1:]

    if args and args[0] == '--backfill':
        if len(args) != 3:
            print("Usage: scrape_manifest.py --backfill YYYY-MM-DD YYYY-MM-DD")
            sys.exit(1)
        for date in daterange(args[1], args[2]):
            scrape_date(config, date)

    elif len(args) == 3:
        # Legacy Y M D positional args kept for GitHub Actions compatibility
        date = datetime.date(int(args[0]), int(args[1]), int(args[2]))
        scrape_date(config, date)

    else:
        scrape_date(config, datetime.date.today())

    cleanup_old_manifests(keep_days=MANIFEST_KEEP_DAYS)

if __name__ == '__main__':
    main()
