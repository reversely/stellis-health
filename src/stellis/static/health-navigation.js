/** Keeps the notebook tree available in a compact modal on narrow screens. */
(() => {
  const drawer = document.getElementById('notebooks-drawer');
  const trigger = document.getElementById('open-notebooks');
  let marker;
  const status = document.getElementById('notebooks-drawer-status');
  async function load() {
    try {await window.reloadLibrary();status.replaceChildren();}
    catch {
      const retry = document.createElement('button'); retry.type='button'; retry.textContent='Try again';
      retry.addEventListener('click',load);
      status.replaceChildren('Notebooks could not load ',retry);
    }
  }
  function refresh() {
    const path = location.pathname;
    const health = ['/', '/summary', '/timeline', '/records', '/trends'].includes(path);
    const chat = path === '/chat' || path.startsWith('/chat/');
    for (const [id,current] of [['rail-meetings',health],['rail-chat',chat]]) {
      const link = document.getElementById(id); link.toggleAttribute('aria-current',current);
      if (current) link.setAttribute('aria-current','page');
    }
    for (const link of document.querySelectorAll('#compact-nav a')) {
      const current = link.dataset.destination === 'health' ? health : chat;
      if(current)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
    trigger.setAttribute('aria-current',path.startsWith('/notebook/') ? 'page' : 'false');
    const owner=path.startsWith('/document/')?document.getElementById('document-view').dataset.notebookId:null;
    for (const link of document.querySelectorAll('#conversation-list a')) {
      if (new URL(link.href).pathname === path || (owner&&new URL(link.href).pathname==='/notebook/'+owner)) link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    }
  }
  trigger.addEventListener('click',async () => {
    await window.StellisAppReady;
    if (drawer.open) return;
    const rail = document.getElementById('home-rail');
    marker = document.createComment('notebook navigation position'); rail.before(marker);
    document.getElementById('notebooks-drawer-body').append(rail);
    drawer.showModal(); refresh();
    // Detail pages can open before their library projection has loaded.
    await load();
  });
  document.getElementById('close-notebooks').addEventListener('click',()=>drawer.close());
  drawer.addEventListener('close',()=>{
    const rail = document.getElementById('home-rail');
    if(marker?.isConnected)marker.replaceWith(rail);
    (trigger.getClientRects().length ? trigger : document.getElementById('rail-meetings')).focus();
  });
  drawer.addEventListener('click',event=>{
    if(event.target.closest('a'))drawer.close();
  });
  document.getElementById('new-collection').addEventListener('click',()=>{
    if(drawer.open)drawer.close();
  });
  window.StellisNavigation = {refresh};
})();
