/** One conversation DOM with docked, movable, expanded and minimized presentations. */
(() => {
 let active=null;
 function attach(node,{head,title='Ask Stellis',initialMode}={}){
  active?.destroy();
  const dom=window.StellisDom,marker=document.createComment('chat position');node.before(marker);
  node.classList.add('stellis-chat-window');node.setAttribute('aria-label',title);
  let mode='docked',previous='docked',resume='docked',destroyed=false,drag=null;
  const controls=dom.el('div',null,'chat-window-controls');controls.setAttribute('role','group');controls.setAttribute('aria-label','Chat window');
  const control=(label,glyph,run)=>{const b=dom.button(null,'chat-window-control');b.title=label;b.setAttribute('aria-label',label);b.append(dom.icon(glyph));b.onclick=run;controls.append(b);return b;};
  control('Minimize chat','minus',()=>setMode('minimized'));
  const pop=control('Open chat window','arrows-move',()=>setMode(mode==='docked'?'floating':'docked'));
  const expand=control('Expand chat','arrows-diagonal',()=>setMode(mode==='expanded'?previous:'expanded'));
  head.append(controls);head.classList.add('chat-window-head');
  const launcher=dom.button(title,'chat-window-launcher');launcher.hidden=true;launcher.setAttribute('aria-label','Restore '+title);launcher.onclick=()=>setMode(resume);
  const placeholder=dom.el('div',null,'chat-window-placeholder');placeholder.hidden=true;
  const returnButton=dom.button('Dock chat here','coach-button is-quiet');returnButton.onclick=()=>setMode('docked');placeholder.append(returnButton);
  marker.after(placeholder);document.body.append(launcher);
  function viewport(){
   const v=window.visualViewport,nav=document.getElementById('compact-nav');
   // On phones the report action belongs in the heading so it cannot cover source rows.
   const reportHead=document.querySelector('#document-view .document-head');
   if(innerWidth<=760&&reportHead){if(launcher.parentElement!==reportHead)reportHead.append(launcher);}
   else if(launcher.parentElement!==document.body)document.body.append(launcher);
   const navigation=innerWidth<=1000&&nav?.checkVisibility()?nav.getBoundingClientRect().height:0;
   node.style.setProperty('--chat-viewport-height',(v?.height||innerHeight)+'px');
   node.style.setProperty('--chat-viewport-top',(v?.offsetTop||0)+'px');
   node.style.setProperty('--chat-navigation-height',navigation+'px');
   // A docked chat in the page (a phone's Ask Stellis page too) fills the room down to the bottom bar
   // and a docked demo tour.
   if(mode==='docked'&&(innerWidth>760||getComputedStyle(node).position!=='fixed')){
    const top=Math.max(16,node.getBoundingClientRect().top+scrollY);
    const sheet=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--demo-sheet'))||0;
    node.style.setProperty('--chat-docked-height',Math.max(320,(v?.height||innerHeight)-navigation-sheet-top-16)+'px');
   }
   if(mode==='floating'&&innerWidth>760){
    const box=node.getBoundingClientRect();if(box.right>innerWidth-12)node.style.left=Math.max(12,innerWidth-box.width-12)+'px';
    if(box.bottom>innerHeight-navigation-12)node.style.top=Math.max(12,innerHeight-navigation-box.height-12)+'px';
   }
  }
  function setMode(next){
   if(destroyed)return;
   if(next==='expanded'&&mode!=='expanded'&&mode!=='minimized')previous=mode;
   if(next==='minimized'&&mode!=='minimized')resume=mode;
   mode=next;node.dataset.chatMode=mode;
   launcher.hidden=mode!=='minimized';node.hidden=mode==='minimized';
   if(mode==='docked'){marker.after(node);placeholder.hidden=true;node.removeAttribute('role');}
   else{document.body.append(node);placeholder.hidden=mode==='minimized';node.setAttribute('role','region');}
   pop.setAttribute('aria-label',mode==='docked'?'Open chat window':'Dock chat');pop.title=pop.getAttribute('aria-label');
   expand.setAttribute('aria-label',mode==='expanded'?'Restore chat size':'Expand chat');expand.title=expand.getAttribute('aria-label');
   if(mode!=='floating'){for(const prop of ['left','top','right','bottom','width','height'])node.style.removeProperty(prop);}
   viewport();requestAnimationFrame(viewport);node.dispatchEvent(new CustomEvent('chat-window-mode',{detail:{mode}}));
   if(mode!=='minimized')requestAnimationFrame(()=>node.querySelector('textarea,input')?.focus({preventScroll:true}));
  }
  node.addEventListener('keydown',event=>{
   if(event.key==='Escape'&&(mode==='floating'||mode==='expanded'||innerWidth<=760)){
    event.preventDefault();event.stopPropagation();setMode('minimized');launcher.focus();
   }
  });
  head.addEventListener('pointerdown',event=>{
   if(mode!=='floating'||innerWidth<=760||event.button!==0||event.target.closest('button,a,input,textarea'))return;
   const r=node.getBoundingClientRect();drag={x:event.clientX,y:event.clientY,left:r.left,top:r.top};head.setPointerCapture(event.pointerId);
  });
  head.addEventListener('pointermove',event=>{
   if(!drag)return;const r=node.getBoundingClientRect();
   node.style.left=Math.max(12,Math.min(innerWidth-r.width-12,drag.left+event.clientX-drag.x))+'px';
   node.style.top=Math.max(12,Math.min(innerHeight-r.height-12,drag.top+event.clientY-drag.y))+'px';node.style.right='auto';node.style.bottom='auto';
  });
  const release=()=>{drag=null;};head.addEventListener('pointerup',release);head.addEventListener('pointercancel',release);
  function destroy(){if(destroyed)return;destroyed=true;watch.disconnect();navigationSize.disconnect();window.removeEventListener('resize',viewport);window.removeEventListener('scroll',onScroll);window.visualViewport?.removeEventListener('resize',viewport);window.visualViewport?.removeEventListener('scroll',viewport);launcher.remove();placeholder.remove();node.remove();if(active?.node===node)active=null;}
  const navigationSize=new ResizeObserver(viewport);
  const navigationNode=document.getElementById('compact-nav');if(navigationNode)navigationSize.observe(navigationNode);
  const watch=new MutationObserver(()=>{if(!marker.isConnected)destroy();});watch.observe(document.body,{childList:true,subtree:true});
  window.addEventListener('resize',viewport);window.visualViewport?.addEventListener('resize',viewport);window.visualViewport?.addEventListener('scroll',viewport);
  // The docked panel refits as the page scrolls, so its composer is always on screen (#258).
  let refit=0;const onScroll=()=>{if(!refit)refit=requestAnimationFrame(()=>{refit=0;viewport();});};window.addEventListener('scroll',onScroll,{passive:true});
  setMode(initialMode||(innerWidth<=760?'minimized':'docked'));
  const controller={node,setMode,restore:()=>setMode(mode==='minimized'?resume:mode),destroy,get mode(){return mode;}};active=controller;return controller;
 }
 window.StellisChatWindow={attach};
})();
