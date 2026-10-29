/** User-approved notebook selection shared by setup and the workspace. */
(function(root) {
  'use strict';
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1' + path, {method,
      headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined});
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Could not load notebooks');
    return data;
  }
  function button(text, action) {
    const b = el('button', text); b.type = 'button'; b.addEventListener('click', action); return b;
  }
  async function open({onSelect, selected = []} = {}) {
    const trigger = document.activeElement;
    const dialog = el('dialog', null, 'health-catalogue');
    dialog.setAttribute('aria-label', 'Add Notebook');
    const title = el('h2', 'Add Notebook');
    const searchLabel = el('label', 'Search notebooks');
    const search = el('input'); search.type = 'search'; searchLabel.append(search);
    const status = el('p', 'Loading notebooks'); status.role = 'status';
    const list = el('div', null, 'catalogue-list');
    const choices = new Map();
    const selectedList = el('ul',null,'catalogue-selected');
    function paintSelection() {
      selectedList.replaceChildren();
      for (const [key, choice] of choices) {
        const row = el('li'); row.append(el('span',choice.name),button('Remove',() => {choices.delete(key);paintSelection();paintOptions?.();})); selectedList.append(row);
      }
      confirm.disabled = !choices.size;
      status.textContent = choices.size ? `${choices.size} selected` : '';
    }
    let paintOptions = null;
    const footer = el('footer');
    const actions = el('div', null, 'dialog-actions');
    const cancel = button('Cancel', () => dialog.close());
    const confirm = button('Add notebooks', async () => {
      confirm.disabled = true; status.textContent = '';
      try {
        const selections = [...choices.values()];
        if (!selections.length) throw Error('Choose a notebook');
        if (onSelect) await onSelect(selections);
        else await api('/health-catalogue/activate', 'POST', {selections});
        dialog.close();
        if (!onSelect) location.reload();
      } catch (e) { status.textContent = e.message; }
      finally { confirm.disabled = !choices.size; }
    });
    confirm.className = 'primary invert-button'; confirm.disabled = true;
    actions.append(cancel, confirm);
    const content = el('div',null,'catalogue-content');
    content.append(selectedList,list,status,footer);
    dialog.append(title,searchLabel,content,actions);
    document.body.append(dialog);
    dialog.addEventListener('close', () => {dialog.remove();
      const target = trigger?.getClientRects().length ? trigger : document.getElementById('open-notebooks');
      target?.focus();}, {once:true});
    dialog.showModal();
    try {
      const [catalogue, recommendations] = await Promise.all([
        api('/health-catalogue'), api('/health-catalogue/recommendations').catch(() => ({groups:[]}))]);
      if (!dialog.isConnected) return;
      const recommended = new Map(recommendations.groups.map(g => [g.template_id, g]));
      const registered = new Set(selected.filter(Boolean).map(id=>catalogue.template_aliases?.[id]||id));
      const groups = [...new Set(catalogue.collections.map(c => c.group))];
      const link = el('a', catalogue.attribution); link.href = catalogue.source_url;
      link.target = '_blank'; link.rel = 'noopener noreferrer'; footer.append(link);
      function paint() {
        const needle = search.value.trim().toLocaleLowerCase();
        list.replaceChildren();
        for (const grouping of groups) {
          const section = el('section'); section.append(el('h3', grouping));
          let count = 0;
          for (const collection of catalogue.collections.filter(c => c.group === grouping)) {
            const templates = catalogue.templates.filter(t => t.collection_id === collection.collection_id &&
              (!needle || (t.display_name+' '+t.description+' '+collection.display_name+' '+t.clinical_concepts.join(' ')).toLocaleLowerCase().includes(needle)));
            if (!templates.length) continue;
            count += templates.length;
            const flat = templates.length===1 && templates[0].display_name===collection.display_name;
            const details = el(flat?'div':'details',null,'catalogue-category');
            if(!flat){details.open = Boolean(needle) || templates.some(t => recommended.has(t.template_id) || choices.has(t.template_id));details.append(el('summary', collection.display_name),el('p', collection.description));}
            for (const template of templates) {
              const label = el('label', null, 'catalogue-option');
              const checkbox = el('input'); checkbox.type = 'checkbox';
              checkbox.disabled = Boolean(template.notebook_id) || registered.has(template.template_id);
              checkbox.checked = checkbox.disabled || choices.has(template.template_id);
              const body = el('span'); body.append(el('strong', template.display_name), el('small', template.description));
              const recommendation = recommended.get(template.template_id);
              if (recommendation) body.append(el('small', 'Recommended: '+recommendation.reason));
              if (checkbox.disabled) body.append(el('small', 'Added'));
              checkbox.addEventListener('change', () => {
                if (checkbox.checked) choices.set(template.template_id, {template_id:template.template_id,
                  collection_id:template.collection_id, name:template.display_name, description:template.description,
                  items:recommendation?.items || [], goal:recommendation?.goal || ''});
                else choices.delete(template.template_id);
                paintSelection();
              });
              label.append(checkbox, body); details.append(label);
            }
            section.append(details);
          }
          if (count) list.append(section);
        }
        if (!list.childElementCount) list.append(el('p', 'No matching notebooks'));
      }
      paintOptions = paint;
      search.addEventListener('input', paint); paint();
      const custom = el('details'); custom.append(el('summary', 'Custom notebook'));
      const field = (text, input) => {input.setAttribute('aria-label',text);const label = el('label',text); label.append(input); custom.append(label); return input;};
      const name = field('Name', el('input')); name.maxLength = 80;
      const description = field('Description', el('textarea')); description.maxLength = 1000;
      const parent = field('Collection', el('select'));
      for (const c of catalogue.collections) parent.add(new Option(c.display_name,c.collection_id));
      custom.append(button('Select custom notebook', () => {
        if (!name.value.trim()) { status.textContent = 'Enter a notebook name'; name.focus(); return; }
        choices.set('custom:'+name.value.trim().toLocaleLowerCase(), {name:name.value.trim(), description:description.value.trim(), collection_id:parent.value});
        paintSelection();
        name.value = ''; description.value = '';
      }));
      footer.prepend(custom);
      if (catalogue.archived.length && !onSelect) {
        const archived = el('details'); archived.append(el('summary','Archived notebooks'));
        for (const book of catalogue.archived) {
          const row = el('div',null,'catalogue-archived'); row.append(el('span',book.name),button('Restore',async event => {
            event.target.disabled = true;
            try {await api('/notebooks/'+encodeURIComponent(book.id),'PATCH',{archived:false}); location.reload();}
            catch(e) {status.textContent=e.message; event.target.disabled=false;}
          })); archived.append(row);
        }
        footer.prepend(archived);
      }
      status.textContent = ''; search.focus();
    } catch (e) {status.textContent = e.message;}
  }
  root.StellisHealthCatalogue = {open};
})(window);
