// All localStorage access is centralised here so the key names never drift
const PROGRESS_KEY = 'cr_progress'; // { comicId: "YYYY-MM-DD" (last-viewed date) }

const State = {
  getProgress() {
    try { return JSON.parse(localStorage.getItem(PROGRESS_KEY)) || {}; }
    catch { return {}; }
  },

  getComicProgress(comicId) {
    return this.getProgress()[comicId] || null;
  },

  setComicProgress(comicId, date) {
    const p = this.getProgress();
    p[comicId] = date;
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(p));
  }
};
