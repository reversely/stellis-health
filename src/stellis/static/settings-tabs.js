/**
 * Settings in four short tabs instead of one long page (owner, 2026-10-27: cap every page's length).
 * Sections keep their own `hidden` logic; a tab only adds or removes `settings-off`.
 */
(() => {
  const view = document.querySelector('#settings-view');
  if (!view) return;
  const groups = [
    ['account', 'Account', ['settings-profile-title', 'settings-signin-title', 'settings-delete-title']],
    ['health', 'Health', ['settings-health-title', 'settings-vocabulary-title', 'settings-dates-title', 'example-workspace-settings']],
    ['ai', 'AI', ['settings-ai-title', 'settings-routing-title', 'settings-integrations-title', 'settings-traces-title']],
    ['data', 'Data and privacy', ['settings-debug-title', 'settings-privacy-title']],
  ];
  const sections = [...view.querySelectorAll(':scope > .settings-section')];
  const groupOf = section => groups.find(([, , ids]) =>
    ids.includes(section.id) || ids.includes(section.getAttribute('aria-labelledby')))?.[0] || 'account';
  const tabs = document.createElement('div');
  tabs.className = 'tracker-tabs settings-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Settings');
  let saved = null;
  try { saved = localStorage.getItem('stellis-settings-tab'); } catch {}
  const buttons = groups.map(([key, label]) => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'tracker-tab';
    tab.setAttribute('role', 'tab');
    tab.textContent = label;
    tab.dataset.group = key;
    tab.addEventListener('click', () => select(key));
    tabs.append(tab);
    return tab;
  });
  tabs.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const index = buttons.findIndex(tab => tab.getAttribute('aria-selected') === 'true');
    const next = buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
    select(next.dataset.group);
    next.focus();
  });
  function select(key) {
    for (const tab of buttons) {
      const on = tab.dataset.group === key;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    }
    for (const section of sections) section.classList.toggle('settings-off', groupOf(section) !== key);
    try { localStorage.setItem('stellis-settings-tab', key); } catch {}
  }
  view.querySelector('.settings-title').after(tabs);
  // A link to a setting (/settings#…) opens the tab that holds it.
  const fromHash = () => {
    const target = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    const section = target?.closest('#settings-view > .settings-section');
    return section ? groupOf(section) : null;
  };
  select(fromHash() || (groups.some(([key]) => key === saved) ? saved : 'account'));
  const follow = () => { const key = fromHash(); if (key) select(key); };
  addEventListener('hashchange', follow);
  // In-app navigation shows the view without a page load; follow the hash each time it opens.
  new MutationObserver(() => { if (!view.hidden) follow(); }).observe(view, {attributes: true, attributeFilter: ['hidden']});
})();
