/** Local reference search that prepares an answer for explicit review and sending. */
(function(root) {
'use strict';
function render({node, api, onSelect, category: initialCategory = 'goals'}) {
  const panel = node('section', null, 'intake-library');
  const medicine=initialCategory==='medicines',conditions=initialCategory==='conditions';
  panel.setAttribute('aria-label', medicine?'Medicines and supplements':conditions?'Preexisting conditions':'Goal ideas');
  const category=initialCategory;let request=0,timer;
  const input = node('input');
  input.type = 'search';
  input.placeholder = medicine?'Search a medicine or supplement':conditions?'Search a condition':'Search goals';
  input.setAttribute('aria-label', medicine?'Search medicines and supplements':conditions?'Search preexisting conditions':'Search goals');
  input.maxLength = 100;
  const status = node('p', '', 'intake-library-status');
  status.setAttribute('role', 'status');
  const results = node('div', null, 'intake-library-results');
  const attribution = node('details', null, 'intake-library-source');
  attribution.append(node('summary', 'Library source'));
  const sourceText = node('p');
  attribution.append(sourceText);
  async function load() {
    const current = ++request;
    status.textContent = 'Searching';
    try {
      const data = await api('/intake/library?category=' + category + '&q=' + encodeURIComponent(input.value));
      if (current !== request) return;
      results.replaceChildren();
      for (const item of data.items) {
        const button = node('button', null, 'intake-library-item');
        button.type = 'button';
        button.append(node('strong', item.label), node('span', item.detail));
        button.addEventListener('click', () => onSelect(item));
        results.append(button);
      }
      status.textContent = !input.value && (medicine||conditions) ? (medicine?'Search a generic or brand name':'Search a condition, or enter your answer above') :
        data.total ? (data.total > data.items.length ? 'Showing ' + data.items.length + ' of ' + data.total : data.total + ' results') :
        'No matches';
      sourceText.textContent = data.attribution;
    } catch {
      if (current !== request) return;
      status.textContent = 'Search unavailable';
      results.replaceChildren();
    }
  }
  input.addEventListener('input', () => {++request; clearTimeout(timer); timer = setTimeout(load, 180);});
  panel.append(input, status, results, attribution);
  load();
  return panel;
}
/** One searchable answer field, selected values, and an exclusive None choice. */
let pickerId=0;
/** Suggestions are tapped before anything is typed: each toggles one value, beside the exclusive None. */
function answerPicker({node,api,category,noneLabel,value='',onChange,label:labelText,suggestions=[]}) {
  const id='intake-answer-'+(++pickerId), panel=node('section',null,'intake-answer-picker');
  const chips=node('div',null,'intake-answer-values');chips.setAttribute('aria-label','Selected answers');
  const label=node('label',labelText||(category==='conditions'?'Conditions':'Medicines and supplements'));label.htmlFor=id;
  const input=node('input');input.id=id;input.type='text';input.autocomplete='off';input.maxLength=2000;input.placeholder=category==='conditions'?'Search or type a condition':'Search or type a medicine';
  input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');input.setAttribute('aria-controls',id+'-options');
  const results=node('div',null,'intake-library-results');results.id=id+'-options';results.setAttribute('role','listbox');results.hidden=true;
  const status=node('p','','intake-answer-status');status.setAttribute('role','status');
  const none=node('button',noneLabel,'intake-answer-none');none.type='button';none.setAttribute('aria-pressed','false');
  const taps=node('div',null,'intake-answer-suggestions');taps.setAttribute('role','group');taps.setAttribute('aria-label','Common answers');
  const tapButtons=suggestions.map(text=>{const tap=node('button',text,'intake-answer-suggestion');tap.type='button';tap.addEventListener('click',()=>{values=values.includes(text)?values.filter(v=>v!==text):unique([...values.filter(v=>v!==noneLabel),text]);paint();onChange(getValue());});return tap;});
  let values=[],sequence=0,timer,active=-1;
  const unique=items=>[...new Map(items.filter(Boolean).map(item=>[item.toLocaleLowerCase(),item])).values()];
  function close(){results.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;}
  function paint(){chips.replaceChildren();for(const tap of tapButtons)tap.setAttribute('aria-pressed',String(values.includes(tap.textContent)));for(const text of values.filter(v=>v!==noneLabel&&!suggestions.includes(v))){const chip=node('button',text,'intake-answer-value');chip.type='button';chip.setAttribute('aria-label','Remove '+text);const cross=node('span','×');cross.setAttribute('aria-hidden','true');chip.append(cross);chip.addEventListener('click',()=>{values=values.filter(v=>v!==text);paint();onChange(getValue());input.focus();});chips.append(chip);}none.setAttribute('aria-pressed',String(values.includes(noneLabel)));}
  function getValue(){return unique([...values.filter(v=>!input.value.trim()||v!==noneLabel),input.value.trim()]).join('\n');}
  function choose(text){values=unique([...values.filter(v=>v!==noneLabel),text.trim()]);input.value='';++sequence;clearTimeout(timer);close();status.textContent='';paint();onChange(getValue());input.focus();}
  function option(text,labelText){const option=node('button',labelText||text,'intake-library-item');option.type='button';option.tabIndex=-1;option.setAttribute('role','option');option.id=id+'-option-'+results.children.length;option.setAttribute('aria-selected','false');option.addEventListener('mousedown',event=>event.preventDefault());option.addEventListener('click',()=>choose(text));results.append(option);}
  async function load(){const query=input.value.trim(),request=++sequence;if(!query){close();status.textContent='';return;}status.textContent='Searching…';try{const data=await api('/intake/library?category='+category+'&q='+encodeURIComponent(query));if(request!==sequence||!panel.isConnected)return;results.replaceChildren();for(const item of data.items.filter(item=>!values.some(v=>v.toLocaleLowerCase()===item.label.toLocaleLowerCase())))option(item.label);if(!data.items.some(item=>item.label.toLocaleLowerCase()===query.toLocaleLowerCase()))option(query,'Use “'+query+'”');status.textContent='';}catch{if(request!==sequence||!panel.isConnected)return;results.replaceChildren();option(query,'Use “'+query+'”');status.textContent='Search unavailable. You can still type an answer.';}results.hidden=!results.children.length;input.setAttribute('aria-expanded',String(!results.hidden));active=-1;}
  input.addEventListener('input',()=>{++sequence;clearTimeout(timer);close();if(input.value.trim())values=values.filter(v=>v!==noneLabel);paint();onChange(getValue());timer=setTimeout(load,180);});
  input.addEventListener('keydown',event=>{const options=[...results.children];if(['ArrowDown','ArrowUp'].includes(event.key)&&options.length&&!results.hidden){event.preventDefault();event.stopPropagation();active=active<0?(event.key==='ArrowDown'?0:options.length-1):(active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;options.forEach((item,i)=>item.setAttribute('aria-selected',String(i===active)));input.setAttribute('aria-activedescendant',options[active].id);options[active].scrollIntoView({block:'nearest'});}else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}else if(event.key==='Enter'&&input.value.trim()){event.preventDefault();event.stopPropagation();if(active>=0&&!results.hidden)options[active].click();else choose(input.value);}});
  input.addEventListener('focus',()=>{if(input.value.trim())load();});input.addEventListener('blur',close);
  none.addEventListener('click',()=>{values=values.includes(noneLabel)?[]:[noneLabel];input.value='';++sequence;clearTimeout(timer);close();status.textContent='';paint();onChange(getValue());});
  function setValue(text){++sequence;clearTimeout(timer);status.textContent='';values=unique(String(text||'').split('\n').map(v=>v.trim()));if(values.includes(noneLabel))values=[noneLabel];input.value='';close();paint();}
  function commit(){if(input.value.trim())choose(input.value);return getValue();}
  if(suggestions.length){taps.append(...tapButtons,none);panel.append(label,taps,chips,input,results,status);input.placeholder=category==='conditions'?'Search for another condition':'Search for another medicine';}
  else panel.append(label,chips,input,results,status,none);
  setValue(value);
  return {element:panel,input,setValue,commit,value:getValue,destroy(){++sequence;clearTimeout(timer);}};
}

root.StellisIntakeLibrary = {render,answerPicker};
})(window);
