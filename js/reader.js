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
  const date = new Date(y, m - 1, d + n);
  return date.toISOString().slice(0, 10);
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

// Keep Image objects alive so the browser cache retains the pixels
const preloadedImages = {};

// ── DOM refs ──────────────────────────────────────────────────────────────────

const headerTitle  = document.getElementById('header-title');
const comicImg     = document.getElementById('comic-img');
const readerStatus = document.getElementById('reader-status');
const navDate      = document.getElementById('nav-date');
const btnPrev      = document.getElementById('btn-prev');
const btnNext      = document.getElementById('btn-next');

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
  const imageUrl  = manifest ? manifest[comicId] : null;

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
  const nextDate   = addDays(currentDate, 1);
  const atStart    = currentDate <= startDate;
  const atEnd      = latestDate && currentDate >= latestDate;

  btnPrev.disabled    = atStart;
  btnNext.disabled    = false;
  btnNext.textContent = atEnd ? 'Done ✓' : 'Next →';
  btnNext.classList.toggle('done-btn', !!atEnd);
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

let touchStartX = 0;

document.addEventListener('touchstart', e => {
  touchStartX = e.touches[0].clientX;
}, { passive: true });

document.addEventListener('touchend', e => {
  const dx = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(dx) < 50) return;    // ignore short swipes
  if (dx > 0 && !btnPrev.disabled) btnPrev.click();
  if (dx < 0) btnNext.click();
});

// ── Keyboard ──────────────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft')  btnPrev.click();
  if (e.key === 'ArrowRight') btnNext.click();
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function showStatus(html) { readerStatus.innerHTML = html; readerStatus.style.display = 'block'; }
function hideStatus()     { readerStatus.style.display = 'none'; }

init();
