document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('dl-form');
  const overlay = document.getElementById('loading-overlay');
  const bar = document.querySelector('.loading-bar');
  if (!form || !overlay || !bar) return;
  form.addEventListener('submit', (e) => {
    overlay.classList.add('active');
    bar.style.width = '0%';
    const downloadDuration = 11000;
    const conversionDuration = 3000;
    let elapsed = 0;
    const interval = setInterval(() => {
      elapsed += 100;
      let percent = 0;
      if (elapsed <= downloadDuration) {
        percent = (elapsed / downloadDuration) * 80;
      } else if (elapsed <= downloadDuration + conversionDuration) {
        percent = 80 + ((elapsed - downloadDuration) / conversionDuration) * 20;
      } else {
        percent = 100;
        clearInterval(interval);
        // allow overlay until new page loads
      }
      bar.style.width = percent + '%';
    }, 100);
  });
});