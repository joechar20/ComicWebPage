// Shared manifest fetch cache — avoids duplicate network requests within a page load
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
    const data = await resp.json();
    return data.latest || null;
  } catch {
    return null;
  }
}

function addDays(dateStr, n) {
  // Parse without timezone shift
  const [y, m, d] = dateStr.split('-').map(Number);
  const date = new Date(y, m - 1, d + n);
  return date.toISOString().slice(0, 10);
}

function formatDateShort(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ── Dashboard ────────────────────────────────────────────────────────────────

let config = null;

async function init() {
  const [cfg, latestDate] = await Promise.all([
    fetch('comics.json').then(r => r.json()),
    fetchLatestDate()
  ]);
  config = cfg;

  const progress = State.getProgress();
  const sorted   = sortComics(cfg.comics, progress, cfg.start_date, cfg.default_sort);
  renderList(sorted, progress, cfg.start_date, latestDate);
  populateJumpDropdown(cfg.comics);
}

function getNextDate(comicId, progress, startDate) {
  const last = progress[comicId];
  return last ? addDays(last, 1) : startDate;
}

function sortComics(comics, progress, startDate, sortKeys) {
  return [...comics].sort((a, b) => {
    for (const key of sortKeys) {
      let cmp = 0;
      if (key === 'orientation') {
        cmp = (a.orientation === 'portrait' ? 0 : 1) - (b.orientation === 'portrait' ? 0 : 1);
      } else if (key === 'title') {
        cmp = a.title.localeCompare(b.title);
      } else if (key === 'date') {
        cmp = getNextDate(a.id, progress, startDate)
            .localeCompare(getNextDate(b.id, progress, startDate));
      }
      if (cmp !== 0) return cmp;
    }
    return 0;
  });
}

function renderList(comics, progress, startDate, latestDate) {
  const container = document.getElementById('comic-list');
  container.innerHTML = '';

  let lastOrientation = null;

  comics.forEach(comic => {
    // Insert divider when orientation group changes
    if (comic.orientation !== lastOrientation) {
      const div = document.createElement('div');
      div.className = 'orientation-divider';
      div.textContent = comic.orientation === 'portrait' ? 'Portrait' : 'Landscape';
      container.appendChild(div);
      lastOrientation = comic.orientation;
    }

    const nextDate  = getNextDate(comic.id, progress, startDate);
    const caughtUp  = latestDate && nextDate > latestDate;

    const row = document.createElement(caughtUp ? 'div' : 'a');
    row.className = 'comic-row' + (caughtUp ? ' caught-up' : '');

    if (!caughtUp) {
      row.href = `reader.html?comic=${encodeURIComponent(comic.id)}&date=${nextDate}`;
    }

    row.innerHTML = `
      <span class="comic-badge"></span>
      <div class="comic-info">
        <span class="comic-title">${comic.title}</span>
        <span class="comic-next">${caughtUp
          ? '✓ Caught up'
          : `Next: ${formatDateShort(nextDate)}`}</span>
      </div>
      ${caughtUp ? '' : '<span class="comic-arrow">›</span>'}
    `;

    container.appendChild(row);
  });

  if (!latestDate) {
    const note = document.createElement('p');
    note.style.cssText = 'padding:20px 16px;color:#888;font-size:0.9rem;';
    note.textContent = 'No manifest data yet. Run the GitHub Actions scraper to populate.';
    container.appendChild(note);
  }
}

// ── Jump modal ───────────────────────────────────────────────────────────────

function populateJumpDropdown(comics) {
  const sel = document.getElementById('jump-comic');
  const sorted = [...comics].sort((a, b) => a.title.localeCompare(b.title));
  sorted.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.title;
    sel.appendChild(opt);
  });
}

document.getElementById('btn-jump').addEventListener('click', () => {
  document.getElementById('jump-modal').classList.add('open');
  // Default date to start_date when config is ready
  const dateInput = document.getElementById('jump-date');
  if (config && !dateInput.value) dateInput.value = config.start_date;
});

document.getElementById('btn-modal-close').addEventListener('click', () => {
  document.getElementById('jump-modal').classList.remove('open');
});

document.getElementById('jump-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.classList.remove('open');
});

document.getElementById('btn-jump-go').addEventListener('click', () => {
  const comic = document.getElementById('jump-comic').value;
  const date  = document.getElementById('jump-date').value;
  if (comic && date) {
    window.location.href = `reader.html?comic=${encodeURIComponent(comic)}&date=${date}`;
  }
});

init();
