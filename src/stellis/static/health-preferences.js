/** One explicit period preference shared by Settings and onboarding. */
(function(root) {
  'use strict';
  const request=(method='GET',value)=>root.StellisDom.api('/health/preferences',{method,...(method==='PUT'?{json:{experiences_periods:value}}:{})});
  function question(value) {
    const field=document.createElement('label');field.className='health-preference-question';
    const label=document.createElement('span');label.textContent='Do you experience periods?';
    const select=document.createElement('select');select.setAttribute('aria-label',label.textContent);
    for(const [key,text] of [['','Not specified'],['yes','Yes'],['no','No']]) {
      const option=document.createElement('option');option.value=key;option.textContent=text;select.append(option);
    }
    select.value=value===true?'yes':value===false?'no':'';
    const hint=document.createElement('small');hint.textContent='Your answer controls period tracking';
    field.append(label,select,hint);
    return {element:field,value:()=>select.value===''?null:select.value==='yes'};
  }
  async function mountSettings() {
    const form=document.querySelector('#health-preferences-form');if(!form)return;
    const status=form.querySelector('[role=status]');const save=form.querySelector('button');save.disabled=true;
    try {
      const preferences=await request();const control=question(preferences.experiences_periods);
      form.querySelector('.health-preference-fields').replaceChildren(control.element);save.disabled=false;
      form.onsubmit=async event=>{
        event.preventDefault();save.disabled=true;status.textContent='';status.classList.remove('success');
        try {await request('PUT',control.value());status.textContent='Health preferences saved';status.classList.add('success');root.dispatchEvent(new Event('stellis:health-preferences-changed'));}
        catch(error){status.textContent=error.message;}
        finally {save.disabled=false;}
      };
    }catch(error){status.textContent=error.message;}
  }
  root.StellisHealthPreferences={question,load:()=>request(),save:value=>request('PUT',value),mountSettings};
})(window);
