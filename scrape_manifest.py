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
import datetime
from pathlib import Path
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    )
}

# GoComics uses Cloudflare; cloudscraper handles the JS challenge automatically
_gocomics_scraper = cloudscraper.create_scraper()

def load_config():
    with open('comics.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def build_page_url(comic, date):
    base = comic['url'].rstrip('/')
    if comic['source'] == 'gocomics':
        return f"{base}/{date.strftime('%Y/%m/%d')}/"
    else:  # comicskingdom
        return f"{base}/{date.strftime('%Y-%m-%d')}"

def extract_image_url(page_url, source):
    """Return the comic image URL from the page, or None on failure."""
    try:
        if source == 'gocomics':
            resp = _gocomics_scraper.get(page_url, timeout=20)
        else:
            resp = requests.get(page_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    FETCH ERROR: {e}")
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

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
                # imagesrcset may be "url 1x, url 2x" — take the first
                first = srcset.split(',')[0].strip().split(' ')[0]
                return first.partition('?')[0]  # strip query string

    print(f"    WARN: no image URL found at {page_url}")
    return None

def scrape_date(config, date):
    date_str = date.strftime('%Y-%m-%d')
    print(f"\nScraping {date_str}")
    manifest = {}

    for comic in config['comics']:
        page_url = build_page_url(comic, date)
        print(f"  {comic['id']}")
        image_url = extract_image_url(page_url, comic['source'])
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

def cleanup_old_manifests(keep_days=30):
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

    cleanup_old_manifests(keep_days=30)

if __name__ == '__main__':
    main()
