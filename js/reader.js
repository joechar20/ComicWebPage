// Shared helpers (duplicated from dashboard.js — no build step, no imports)
const manifestCache = {};

async function fetchManifest(dateStr) {
  if (manifestCache[dateStr] !== undefined) return manifestCache[dateStr];
  try {
    const resp = await fetch(`manifest/${dateStr}.json`);
    manifestCache[dateStr] = resp.ok ? await resp.json() : null;
  } catch {
    manifestCache[dateStr] = null;
  }
  return manifestCache[dateStr];
}

async function fetchLatestDate() {
  try {
    const resp = await fetch('manifest/latest.json');
    if (!resp.ok) return null;
    return (await resp.json()).latest || null;
  } catch {
    return null;
  }
}

function addDays(dateStr, n) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  date.setUTCDate(date.getUTCDate() + n);
  const yy = date.getUTCFullYear();
  const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(date.getUTCDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd}`;
}

function formatDateShort(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ── Reader state ─────────────────────────────────────────────────────────────

const params    = new URLSearchParams(window.location.search);
let comicId     = params.get('comic') || '';
let currentDate = params.get('date')  || '';

let config     = null;
let comicMeta  = null;
let latestDate = null;
const SWIPE_NAV_ENABLED = false;

// Keep Image objects alive so the browser cache retains the pixels
const preloadedImages = {};

// ── DOM refs ──────────────────────────────────────────────────────────────────

const headerTitle  = document.getElementById('header-title');
const comicImg     = document.getElementById('comic-img');
const readerStatus = document.getElementById('reader-status');
const navDate      = document.getElementById('nav-date');
const btnPrev      = document.getElementById('btn-prev');
const btnNext      = document.getElementById('btn-next');
const datePicker   = document.getElementById('date-picker');

// Open native date picker when the date button is tapped
document.getElementById('nav-date').addEventListener('click', () => {
  datePicker.value = currentDate;
  if (datePicker.showPicker) datePicker.showPicker();
  else datePicker.click();
});

datePicker.addEventListener('change', () => {
  if (datePicker.value && datePicker.value !== currentDate) showDate(datePicker.value);
});

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  [config, latestDate] = await Promise.all([
    fetch('comics.json').then(r => r.json()),
    fetchLatestDate()
  ]);

  comicMeta = config.comics.find(c => c.id === comicId);
  if (!comicMeta || !currentDate) {
    showStatus('Comic not found.');
    return;
  }

  await showDate(currentDate);
}

// ── Display a date ────────────────────────────────────────────────────────────

async function showDate(date) {
  currentDate = date;
  State.setComicProgress(comicId, date);

  const title = comicMeta ? comicMeta.title : comicId;
  headerTitle.textContent = `${title} : ${date}`;
  navDate.textContent     = formatDateShort(date);

  updateNavButtons();
  showStatus('<div class="spinner"></div>Loading…');
  comicImg.classList.remove('loaded');

  const manifest  = await fetchManifest(date);
  let imageUrl     = manifest ? manifest[comicId] : null;

  if (!imageUrl) {
    showStatus('<div class="spinner"></div>Not in manifest — fetching from source…');
    imageUrl = await fetchImageUrlDynamic(comicId, date);
  }

  if (!imageUrl) {
    showStatus('No comic available for this date.');
    prefetch(date, config.prefetch_days || 3);
    return;
  }

  comicImg.onload  = () => { hideStatus(); comicImg.classList.add('loaded'); };
  comicImg.onerror = () => { showStatus('Image failed to load.'); };
  comicImg.referrerPolicy = 'no-referrer';
  comicImg.alt    = `${title} ${date}`;
  comicImg.src    = imageUrl;

  prefetch(date, config.prefetch_days || 3);
}

// ── Dynamic fetch (for dates not in manifest) ───────────────────────────────

// Cache dynamic lookups so back-navigation doesn't re-fetch
const dynamicCache = {};

async function fetchWithTimeout(url, timeoutMs) {
  if (typeof AbortController === 'undefined') return fetch(url);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

function extractImageUrlFromHtml(html, source) {
  const doc = new DOMParser().parseFromString(html, 'text/html');

  const og = doc.querySelector('meta[property="og:image"]');
  if (og && og.content && !og.content.includes('placeholder')) {
    return og.content;
  }

  if (source === 'gocomics') {
    const link = doc.querySelector('link[rel="preload"][as="image"]');
    if (link) {
      const src = link.getAttribute('imagesrcset') || link.getAttribute('href') || '';
      const url = src.split(',')[0].trim().split(' ')[0].split('?')[0];
      if (url) return url;
    }
  }

  // Regex fallback for proxy responses that transform the original HTML.
  const ogAttr = html.match(/<meta[^>]*property=["']og:image["'][^>]*content=["']([^"']+)["']/i)
    || html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*property=["']og:image["']/i);
  if (ogAttr && ogAttr[1] && !ogAttr[1].includes('placeholder')) {
    return ogAttr[1];
  }

  const ogLine = html.match(/og:image\s*[:=]\s*(https?:\/\/\S+)/i);
  if (ogLine && ogLine[1]) {
    return ogLine[1].replace(/["')\]]+$/, '');
  }

  return null;
}

function extractImageUrlFromRssItem(item) {
  const mediaContent = item.querySelector('media\\:content, content');
  if (mediaContent) {
    const mediaUrl = mediaContent.getAttribute('url');
    if (mediaUrl) return mediaUrl;
  }

  const description = item.querySelector('description');
  if (description && description.textContent) {
    const descDoc = new DOMParser().parseFromString(description.textContent, 'text/html');
    const img = descDoc.querySelector('img');
    if (img && img.src) return img.src;
  }

  const encoded = item.querySelector('content\\:encoded');
  if (encoded && encoded.textContent) {
    const encodedDoc = new DOMParser().parseFromString(encoded.textContent, 'text/html');
    const img = encodedDoc.querySelector('img');
    if (img && img.src) return img.src;
  }

  return null;
}

function toIsoDateUTC(dateObj) {
  const yy = dateObj.getUTCFullYear();
  const mm = String(dateObj.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(dateObj.getUTCDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd}`;
}

function toIsoDateLocal(dateObj) {
  const yy = dateObj.getFullYear();
  const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
  const dd = String(dateObj.getDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd}`;
}

function getGoComicsSlug(comic) {
  const path = new URL(comic.url).pathname.split('/').filter(Boolean);
  return path[path.length - 1] || comic.id;
}

async function fetchImageFromGoComicsRss(comic, dateStr) {
  const slug = getGoComicsSlug(comic);
  const feedUrl = `https://feeds.feedburner.com/uclick/${slug}`;

  try {
    const resp = await fetchWithTimeout(feedUrl, 12000);
    if (!resp.ok) return null;
    const xmlText = await resp.text();
    const doc = new DOMParser().parseFromString(xmlText, 'text/xml');
    const items = Array.from(doc.querySelectorAll('item'));

    for (const item of items) {
      const pubDate = item.querySelector('pubDate')?.textContent;
      if (!pubDate) continue;
      const dt = new Date(pubDate);
      if (Number.isNaN(dt.getTime())) continue;

      const isMatch = toIsoDateUTC(dt) === dateStr || toIsoDateLocal(dt) === dateStr;
      if (!isMatch) continue;

      const imageUrl = extractImageUrlFromRssItem(item);
      if (imageUrl) return imageUrl;
    }
  } catch {
    // RSS fallback is best-effort only.
  }

  return null;
}

async function fetchImageUrlDynamic(id, dateStr) {
  const key = `${id}:${dateStr}`;
  if (dynamicCache[key] !== undefined) return dynamicCache[key];

  const comic = config && config.comics.find(c => c.id === id);
  if (!comic) return (dynamicCache[key] = null);

  const [y, m, d] = dateStr.split('-');
  const pageUrl = comic.source === 'gocomics'
    ? `${comic.url}${y}/${m}/${d}/`
    : `${comic.url.replace(/\/$/, '')}/${dateStr}`;

  const proxyUrls = [
    `https://api.allorigins.win/raw?url=${encodeURIComponent(pageUrl)}`,
    `https://r.jina.ai/http://${pageUrl.replace(/^https?:\/\//, '')}`
  ];

  for (const proxyUrl of proxyUrls) {
    try {
      const resp = await fetchWithTimeout(proxyUrl, 12000);
      if (!resp.ok) continue;
      const html = await resp.text();
      const parsed = extractImageUrlFromHtml(html, comic.source);
      if (parsed) return (dynamicCache[key] = parsed);
    } catch {
      // Try the next proxy candidate.
    }
  }

  if (comic.source === 'gocomics') {
    const rssUrl = await fetchImageFromGoComicsRss(comic, dateStr);
    if (rssUrl) return (dynamicCache[key] = rssUrl);
  }

  return (dynamicCache[key] = null);
}

// ── Pre-fetch ─────────────────────────────────────────────────────────────────

function prefetch(fromDate, count) {
  for (let i = 1; i <= count; i++) {
    const d = addDays(fromDate, i);
    if (preloadedImages[d]) continue;
    fetchManifest(d).then(manifest => {
      const url = manifest ? manifest[comicId] : null;
      if (!url) return;
      const img = new Image();
      img.referrerPolicy = 'no-referrer';
      img.src = url;
      preloadedImages[d] = img; // hold reference to keep cache warm
    });
  }
}

// ── Navigation ────────────────────────────────────────────────────────────────

function updateNavButtons() {
  const startDate  = config ? config.start_date : '2000-01-01';
  const prevDate   = addDays(currentDate, -1);
  const atStart    = prevDate < startDate;
  const atEnd      = latestDate && currentDate >= latestDate;

  btnPrev.disabled    = false;
  btnNext.disabled    = false;
  btnNext.textContent = atEnd ? 'Done ✓' : 'Next →';
  btnNext.classList.toggle('done-btn', !!atEnd);
  btnPrev.classList.toggle('at-start', !!atStart);
}

btnPrev.addEventListener('click', () => {
  const prev = addDays(currentDate, -1);
  if (config && prev >= config.start_date) showDate(prev);
});

btnNext.addEventListener('click', () => {
  if (latestDate && currentDate >= latestDate) {
    window.location.href = 'index.html';
    return;
  }
  showDate(addDays(currentDate, 1));
});

// ── Swipe gestures ────────────────────────────────────────────────────────────

if (SWIPE_NAV_ENABLED) {
  let touchStartX = 0;

  document.addEventListener('touchstart', e => {
    touchStartX = e.touches[0].clientX;
  }, { passive: true });

  document.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - touchStartX;
    if (Math.abs(dx) < 50) return; // ignore short swipes
    if (dx > 0 && !btnPrev.disabled) btnPrev.click();
    if (dx < 0) btnNext.click();
  });
}

// ── Keyboard ──────────────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft')  btnPrev.click();
  if (e.key === 'ArrowRight') btnNext.click();
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function showStatus(html) { readerStatus.innerHTML = html; readerStatus.style.display = 'block'; }
function hideStatus()     { readerStatus.style.display = 'none'; }

init();
