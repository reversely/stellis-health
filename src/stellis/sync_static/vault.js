/* Browser-owned text vault prototype. Keys and plaintext stay in this page's memory. */
const VaultUI = (() => {
  const {b64,bytes,random,aad,derive,seal,open} = window.StellisCrypto;
  const status = document.querySelector('#vault-status');
  const openPanel = document.querySelector('#vault-open');
  const notes = document.querySelector('#notes');
  let user = null;
  let key = null;
  let cursor = 0;
  let records = new Map();
  let mediaUrls = new Set();

  async function call(path, options={}) {
    const response = await fetch(`/sync/v1${path}`, options);
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.detail || 'Sync failed'); error.code = response.status; throw error; }
    return data;
  }
  function render() {
    for (const url of mediaUrls) URL.revokeObjectURL(url);
    mediaUrls.clear();
    notes.replaceChildren();
    const parts = new Map();
    const manifests = [];
    for (const [id,value] of records) {
      let parsed;
      try { parsed=JSON.parse(value); } catch {}
      if (parsed?.kind === 'conversation_part') { parts.set(id,parsed); continue; }
      if (parsed?.kind === 'conversation_manifest') { manifests.push(parsed); continue; }
      const row=document.createElement('li');row.textContent=value;notes.append(row);
    }
    let incomplete=0;
    for (const manifest of manifests) {
      const segments=window.StellisSnapshot.assemble(manifest,parts);
      if (!segments) { incomplete++;continue; }
      const row=document.createElement('li');const details=document.createElement('details');
      const hasAudio=typeof manifest.media_id==='string' && Number.isInteger(manifest.media_chunks) &&
        manifest.media_chunks>0 && manifest.media_chunks<=512 && Number.isInteger(manifest.media_bytes) &&
        manifest.media_bytes>0 && manifest.media_bytes<=512*256*1024 &&
        typeof manifest.media_type==='string' && /^audio\/[a-z0-9.+-]+$/i.test(manifest.media_type);
      const heading=document.createElement('summary');
      heading.textContent=`${manifest.title} (${segments.length} passages${hasAudio?', audio available':', text only'})`;
      details.append(heading);
      if(hasAudio){
        const load=document.createElement('button');load.type='button';load.textContent='Load audio';
        load.addEventListener('click',async()=>{
          load.disabled=true;
          try{
            const chunks=[];let total=0;
            for(let index=0;index<manifest.media_chunks;index++){
              status.textContent=`Loading audio ${index+1} of ${manifest.media_chunks}…`;
              const entry=await call(`/media/${encodeURIComponent(manifest.media_id)}/chunks/${index}`);
              const chunk=await window.StellisCrypto.openBytes(key,entry.ciphertext,bytes(entry.iv),
                aad('media-v1',user.id,manifest.media_id,String(index)));
              total+=chunk.length;if(total>manifest.media_bytes)throw Error('Encrypted audio length changed');
              chunks.push(chunk);
            }
            if(total!==manifest.media_bytes)throw Error('Encrypted audio is incomplete');
            const url=URL.createObjectURL(new Blob(chunks,{type:manifest.media_type}));mediaUrls.add(url);
            const audio=document.createElement('audio');audio.controls=true;audio.preload='none';
            audio.setAttribute('aria-label',`Audio for ${manifest.title}`);audio.src=url;
            load.replaceWith(audio);status.textContent='Audio ready';
          }catch{status.textContent='Audio unavailable or failed authentication';load.disabled=false;}
        });
        details.append(load);
      }
      for (const segment of segments) {
        const passage=document.createElement('p');
        const source=window.StellisSnapshot.sourceUrl(manifest,segment);
        const seek=document.createElement(source?'a':'span');
        if(source){seek.href=source;seek.target='_blank';seek.rel='noopener';seek.title='Open on recording device';}
        const seconds=Math.floor(segment.start_ms/1000);seek.textContent=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
        passage.append(seek,document.createTextNode(`, ${segment.speaker||'Speaker'}: ${segment.text}`));details.append(passage);
      }
      row.append(details);notes.append(row);
    }
    return incomplete;
  }
  function database() {
    return new Promise((resolve,reject) => {
      const request = indexedDB.open('stellis-encrypted-queue-v1',1);
      request.onupgradeneeded = () => request.result.createObjectStore('entries',{keyPath:'id'});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function queued() {
    const db = await database();
    return new Promise((resolve,reject) => {
      const request = db.transaction('entries').objectStore('entries').getAll();
      request.onsuccess = () => { db.close(); resolve(request.result.filter(item => item.user_id === user.id)); };
      request.onerror = () => { db.close(); reject(request.error); };
    });
  }
  async function queue(entry) {
    const db = await database();
    return new Promise((resolve,reject) => {
      const tx = db.transaction('entries','readwrite'); tx.objectStore('entries').put({...entry,user_id:user.id});
      tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = () => { db.close(); reject(tx.error); };
    });
  }
  async function unqueue(id) {
    const db = await database();
    return new Promise((resolve,reject) => {
      const tx = db.transaction('entries','readwrite'); tx.objectStore('entries').delete(id);
      tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = () => { db.close(); reject(tx.error); };
    });
  }
  async function sync() {
    if (!key || !user) return;
    const pending = await queued();
    for (const entry of pending) {
      const {user_id,...wire} = entry;
      await call('/entries',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(wire)});
      await unqueue(entry.id);
    }
    if (pending.length) { records.clear(); cursor=0; }
    while (true) {
      const page = await call(`/entries?after=${cursor}&limit=100`);
      for (const entry of page.entries) {
        let text;
        try { text = await open(key,entry.ciphertext, bytes(entry.iv), aad(user.id,entry.item_id,entry.id)); }
        catch { throw Error('Encrypted entry failed authentication'); }
        records.set(entry.item_id,text);
      }
      cursor = page.cursor;
      if (page.entries.length < 100) break;
    }
    const incomplete=render(); status.textContent = incomplete ? 'Transcript snapshot incomplete' : 'Synced';
  }
  async function unlock(passphrase) {
    if (passphrase.length < 12) throw Error('Use at least 12 characters');
    let vault;
    try { vault = await call('/vault'); }
    catch (error) {
      if (error.code !== 404) throw error;
      const salt = random(16); key = await derive(passphrase,salt);
      const iv = random(12);
      vault = await call('/vault',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({salt:b64(salt),check_iv:b64(iv),
          check_ciphertext:await seal(key,'stellis-vault-v1',iv,aad(user.id,'vault-check-v1'))})});
    }
    key = await derive(passphrase,bytes(vault.salt));
    try {
      if (await open(key,vault.check_ciphertext,bytes(vault.check_iv),aad(user.id,'vault-check-v1')) !== 'stellis-vault-v1')
        throw Error('Vault check failed');
    } catch { key=null; throw Error('Wrong passphrase or vault data changed'); }
    cursor=0;records.clear();openPanel.hidden=false;
    try { await sync(); }
    catch (error) { status.textContent = error.message; }
  }
  document.querySelector('#vault-form').onsubmit = async event => {
    event.preventDefault(); const input=document.querySelector('#vault-passphrase');
    const passphrase=input.value; input.value=''; status.textContent='Unlocking…';
    try { await unlock(passphrase); } catch (error) { status.textContent=error.message; }
  };
  document.querySelector('#note-form').onsubmit = async event => {
    event.preventDefault(); const input=document.querySelector('#note-text'); const value=input.value.trim();
    if (!value || !key) return;
    const entry={id:crypto.randomUUID(),item_id:crypto.randomUUID(),iv:b64(random(12))};
    entry.ciphertext=await seal(key,value,bytes(entry.iv),aad(user.id,entry.item_id,entry.id));
    await queue(entry); records.set(entry.item_id,value); render(); input.value='';
    try { await sync(); } catch { status.textContent='Saved on this browser. Sync when online.'; }
  };
  document.querySelector('#vault-sync').onclick = () => sync().catch(error => { status.textContent=error.message; });
  return {
    mount(current) { if (user?.id === current.id) return; user=current;key=null;cursor=0;records.clear();render();openPanel.hidden=true;status.textContent=''; },
    lock() { user=null;key=null;cursor=0;records.clear();render();openPanel.hidden=true;status.textContent=''; },
  };
})();
window.VaultUI=VaultUI;
