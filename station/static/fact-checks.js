'use strict';
document.querySelectorAll('[data-claim-review]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('[type="submit"]');
  const status = form.querySelector('.fact-form-error');
  const payload = Object.fromEntries(new FormData(form));
  const option = form.elements.source_id.selectedOptions[0];
  payload.source_version = option?.dataset.sourceVersion;
  button.disabled = true;
  status.textContent = '正在保存…';
  try {
    const response = await fetch(`/api/pages/${encodeURIComponent(form.dataset.claimReview)}/claim-reviews`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '保存失败');
    window.wikiRememberNavigation?.(result.url, true);
    window.history.replaceState(window.history.state, '', result.url);
    window.location.reload();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}));
