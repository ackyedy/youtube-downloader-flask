document.addEventListener('DOMContentLoaded', () => {
  // 右クリック禁止
  document.addEventListener('contextmenu', e => e.preventDefault());

  // セクションの並び替え
  const container = document.querySelector('.container');
  const alertEl = container.querySelector('.alert.alert-success');
  const dlSection = container.querySelector('.download-section');
  const resultSection = container.querySelector('.result-section');
  if (container && alertEl && dlSection && resultSection) {
    const alertMsg = alertEl.querySelector('.alert-message')?.textContent || '';
    if (alertMsg.includes('ダウンロードが完了しました')) {
      container.append(dlSection, alertEl, resultSection);
    const resultCard = container.querySelector('.result-card');
    if (resultCard) {
      resultCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    } else {
      container.append(alertEl, dlSection, resultSection);
    }
  }

  // 既存のフォーマット選択処理
  document.querySelectorAll('.format-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
    });
  });
});