/** Retained PDF and image attachments shared by both chat composers. */
(function(root) {
'use strict';
const dom=()=>root.StellisDom;
const path=(kind,id)=>`/chat/${kind}/${encodeURIComponent(id)}/attachments`;
function cards(items,{kind,id,books=[],notebookId=()=>'',remove,onChange=()=>{},onFiled=()=>{}}) {
  const list=dom().el('div',null,'chat-file-list');
  for(const item of items) {
    const row=dom().el('div',null,'chat-file');
    const link=dom().el('a',item.filename);link.href='/document/'+encodeURIComponent(item.document_id);link.dataset.document=item.document_id;
    const mark=dom().el('span',null,'chat-file-icon');mark.append(dom().icon('file-text'));row.append(mark,link);
    if(item.status==='needs_text')row.append(dom().el('span','Needs text','chat-file-status'));
    if(item.notebook_id) {
      const book=books.find(entry=>entry.id===item.notebook_id);
      const saved=dom().el('a',book?'Saved to '+book.name:'Saved to notebook','chat-file-status chat-file-saved');saved.href='/notebook/'+encodeURIComponent(item.notebook_id);
      // The notebook's own colour marks where the file was saved.
      if(book?.color){const dot=dom().el('span',null,'chat-file-dot');dot.style.background=book.color;saved.prepend(dot);}
      row.append(saved);
    } else {
      const select=dom().el('select');select.setAttribute('aria-label','Notebook for '+item.filename);
      select.append(new Option('Choose notebook',''));
      for(const book of books)select.append(new Option(book.name,book.id));
      select.value=notebookId()||'';row.append(select);
      const save=dom().button('Save to notebook','chat-file-save');save.disabled=!select.value;
      select.addEventListener('change',()=>save.disabled=!select.value);
      save.addEventListener('click',async()=>{
        save.disabled=true;notice.textContent='';
        try {
          const updated=await dom().api(path(kind,id)+'/'+encodeURIComponent(item.id)+'/notebook',{method:'POST',json:{notebook_id:select.value}});
          Object.assign(item,updated);onChange();onFiled(updated);root.dispatchEvent(new CustomEvent('stellis:attachment-filed',{detail:updated}));const replacement=cards([item],{kind,id,books,notebookId,remove,onChange,onFiled}).firstElementChild;row.replaceWith(replacement);replacement.querySelector('a').focus();
        }catch(error){notice.textContent=error.message;save.disabled=false;}
      });row.append(save);
    }
    if(remove) {
      const button=dom().button('Remove','chat-file-remove');button.setAttribute('aria-label','Remove '+item.filename);
      button.addEventListener('click',async()=>{
        button.disabled=true;
        try {await remove(item);onChange();}catch(error){notice.textContent=error.message;button.disabled=false;}
      });row.append(button);
    }
    const notice=dom().el('span',null,'chat-file-status');notice.setAttribute('role','status');row.append(notice);list.append(row);
  }
  return list;
}
function create(options) {
  let items=[],busy=false,sending=false;
  const host=dom().el('div',null,'chat-attachments');
  const picker=dom().el('input');picker.type='file';picker.multiple=true;picker.hidden=true;picker.accept='.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg';
  const attach=dom().button('Attach','chat-attach');
  const notice=dom().el('p',null,'chat-file-status');notice.setAttribute('role','status');
  const draft=dom().el('div');host.append(attach,picker,draft,notice);
  const pending=()=>items.filter(item=>!item.message_id);
  function paint(){
    draft.replaceChildren(cards(pending(),{...options,remove:async item=>{
      await dom().api(path(options.kind,options.id)+'/'+encodeURIComponent(item.id),{method:'DELETE'});
      items=items.filter(row=>row.id!==item.id);paint();attach.focus();
    },onChange:()=>options.onChange?.()}));options.onChange?.();
  }
  async function load(){items=await dom().api(path(options.kind,options.id));paint();}
  attach.addEventListener('click',()=>picker.click());
  picker.addEventListener('change',async()=>{
    busy=true;attach.disabled=true;options.onChange?.();
    try {
      for(const file of [...picker.files]) {
        notice.textContent='Attaching '+file.name;
        const body=new FormData();body.append('file',file);
        const item=await dom().api(path(options.kind,options.id),{method:'POST',body});
        // A list load that finishes after the upload may already hold this attachment; keep one copy.
        items=[...items.filter(row=>row.id!==item.id),item];paint();
      }
      notice.textContent='';
    }catch(error){notice.textContent=error.message;}
    finally{busy=false;attach.disabled=false;picker.value='';options.onChange?.();}
  });
  return {element:host,load,pending,ids:()=>pending().map(item=>item.id),busy:()=>busy||sending,setSending:value=>{sending=value;attach.disabled=value||busy;draft.querySelectorAll('button,select').forEach(control=>control.disabled=value);if(!value)paint();},
    messages:messageId=>cards(items.filter(item=>item.message_id===messageId),options)};
}
root.StellisChatAttachments={create,cards,path};
})(window);
