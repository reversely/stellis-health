/** Editable health dates share one source with the month calendar. */
(function(root) {
  'use strict';
  const dates=root.StellisCalendarDates;
  let nextId=0;
  function mount(host,{onChange,getSelectedDate}={}) {
    const dom=root.StellisDom,el=dom.el;
    const today=()=>root.StellisDemoDate||dates.formatDate(new Date());
    const dateLabel=value=>dates.parseDate(value).toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
    const api=(path='',options)=>dom.api('/calendar/milestones'+path,options);
    const button=(label,action,className='coach-button')=>{
      const node=dom.button(label,className);node.type='button';node.addEventListener('click',action);return node;
    };
    let milestones=[],destroyed=false,requestId=0,activeDialog=null;
    const status=el('p',null,'health-calendar-status');status.setAttribute('role','status');
    const heading=el('div',null,'health-milestone-heading');
    const add=button('Add milestone',()=>edit());const title=el('h3','Milestones');heading.append(title,add);
    const list=el('div',null,'health-milestone-list');host.replaceChildren(heading,status,list);
    async function load() {
      const request=++requestId;
      try {
        const result=await api();if(destroyed||request!==requestId)return;
        milestones=result.milestones;status.textContent='';paint();
      }catch(error){if(!destroyed&&request===requestId)status.replaceChildren(el('span',error.message),button('Try again',load));}
    }
    function paint() {
      list.replaceChildren();
      // No milestones: only the Add button shows, with no heading or empty text (owner, 2026-10-27). It
      // joins the section's own header actions rather than standing on a row by itself.
      title.hidden=!milestones.length;heading.classList.toggle('is-empty',!milestones.length);
      const sectionHead=host.closest('.dashboard-section')?.querySelector('.dashboard-section-head');
      if(!milestones.length&&sectionHead){sectionHead.append(add);heading.hidden=true;}else{heading.append(add);heading.hidden=false;}
      if(!milestones.length)return;
      for(const milestone of milestones) {
        const card=el('article',null,'health-milestone-card');card.dataset.milestoneId=milestone.id;
        const title=el('h4',milestone.title);
        const summary=root.StellisHealthDates.summarizeMilestone(milestone);
        const caption=summary.dateLabel+' '+dateLabel(summary.anchor);
        const figure=root.StellisHealthDates.createFigure({value:summary.today?'Today':summary.value,
          unit:summary.today?'':summary.unit,context:summary.today?milestone.title:summary.context,caption});
        const actions=el('div',null,'health-milestone-actions');
        actions.append(button('Edit',()=>edit(milestone)));
        if(!milestone.actual_date)actions.append(button('Happened today',async event=>{
          const control=event.currentTarget;control.disabled=true;
          try {
            await api('/'+milestone.id,{method:'PUT',json:{...milestone,actual_date:today()}});
            await load();onChange?.();
            status.textContent=milestone.title+' confirmed';
            list.querySelector(`[data-milestone-id="${milestone.id}"] button`)?.focus();
          }catch(error){status.textContent=error.message;control.disabled=false;}
        }));
        else actions.append(button('Undo confirmation',async event=>{
          const control=event.currentTarget;control.disabled=true;
          try {await api('/'+milestone.id,{method:'PUT',json:{...milestone,actual_date:''}});await load();onChange?.();list.querySelector(`[data-milestone-id="${milestone.id}"] button`)?.focus();}
          catch(error){status.textContent=error.message;control.disabled=false;}
        }));
        card.append(title,figure);
        if(milestone.note)card.append(el('p',milestone.note,'health-milestone-note'));
        card.append(actions);list.append(card);
      }
    }
    function edit(existing=null) {
      activeDialog?.close();
      const opener=document.activeElement;
      const dialog=el('dialog',null,'dashboard-dialog health-milestone-dialog');activeDialog=dialog;
      const head=el('div',null,'dashboard-section-head');
      const title=el('h2',existing?'Edit milestone':'Add milestone');title.id='milestone-dialog-'+(++nextId);
      dialog.setAttribute('aria-labelledby',title.id);head.append(title,button('Close',()=>dialog.close()));
      const form=el('form',null,'health-milestone-form');
      const controls={};
      function field(name,label,type,value,choices) {
        const wrapper=el('label',label);
        const input=el(choices?'select':type==='textarea'?'textarea':'input');input.name=name;input.setAttribute('aria-label',label);
        if(choices)for(const [value,label] of choices){const option=el('option',label);option.value=value;input.append(option);}
        else if(type!=='textarea')input.type=type;
        input.value=value||'';input.required=['title','target_date','date_kind'].includes(name);
        if(type==='text')input.maxLength=120;
        if(type==='textarea'){input.maxLength=500;input.rows=3;}
        wrapper.append(input);form.append(wrapper);controls[name]=input;return input;
      }
      const defaults=existing||{target_date:getSelectedDate?.()||today(),date_kind:'scheduled'};
      const presets=el('div',null,'health-milestone-actions');presets.setAttribute('aria-label','Milestone suggestions');
      for(const [name,kind] of [['Surgery','scheduled'],['Delivery','estimated'],['Treatment','scheduled'],['Custom','scheduled']]) {
        presets.append(button(name,()=>{controls.title.value=name==='Custom'?'':name;controls.date_kind.value=kind;controls.title.focus();}));
      }
      if(!existing)form.append(presets);
      field('title','Milestone name','text',defaults.title);
      field('target_date','Target date','date',defaults.target_date);
      field('date_kind','Date type','select',defaults.date_kind,[['scheduled','Scheduled'],['estimated','Estimated']]);
      field('actual_date','Confirmed date','date',defaults.actual_date).max=today();
      form.append(el('p','Set a confirmed date after the event happens','dashboard-meta'));
      field('note','Notes','textarea',defaults.note);
      const error=el('p',null,'health-milestone-error');error.setAttribute('role','alert');
      const actions=el('div',null,'health-milestone-actions');
      const save=dom.button('Save','coach-button is-primary');save.type='submit';actions.append(save);
      const remove=existing?button('Delete',async()=>{
        if(!root.confirm('Delete this milestone?'))return;
        remove.disabled=true;
        try{await api('/'+existing.id,{method:'DELETE'});dialog.close();await load();onChange?.();add.focus();}
        catch(failure){error.textContent=failure.message;remove.disabled=false;}
      }):null;
      if(remove)actions.append(remove);
      form.addEventListener('submit',async event=>{
        event.preventDefault();save.disabled=true;if(remove)remove.disabled=true;error.textContent='';
        try {
          const values=Object.fromEntries(Object.entries(controls).map(([key,input])=>[key,input.value]));
          const saved=await api(existing?'/'+existing.id:'',{method:existing?'PUT':'POST',json:values});
          dialog.close();await load();onChange?.();
          list.querySelector(`[data-milestone-id="${saved.id}"] button`)?.focus();
        }catch(failure){error.textContent=failure.message;save.disabled=false;if(remove)remove.disabled=false;}
      });
      form.append(error,actions);dialog.append(head,form);
      dialog.addEventListener('close',()=>{dialog.remove();if(activeDialog===dialog)activeDialog=null;if(!destroyed)(opener?.isConnected?opener:add).focus();},{once:true});
      document.body.append(dialog);dialog.showModal();
    }
    load();
    return {reload:load,open:async id=>{await load();const milestone=milestones.find(item=>item.id===id);if(milestone)edit(milestone);},
      destroy:()=>{destroyed=true;requestId++;activeDialog?.close();host.replaceChildren();}};
  }
  root.StellisHealthMilestones={mount};
})(window);
