/* This component shares the app's session and APIs; it owns no workspace store. */
(() => {
 let pending=null, published='', theme='', day='', received=false,palette='';
 const send=(action,detail={})=>parent.postMessage({stellisMeasurements:true,action,...detail},location.origin);
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==parent||!event.data?.stellisMeasurements)return;
  pending={...event.data,category:event.data.category??pending?.category??null};received=true;
 });
 window.dash_clientside=Object.assign({},window.dash_clientside,{
  stellisMeasurements:{bridge(){
   if(pending&&window.dash_clientside.set_props){
    const key=JSON.stringify(pending.measures);
    if(key!==published){window.dash_clientside.set_props('measure-data',{data:pending.measures||[]});window.dash_clientside.set_props('measure-category-select',{data:[{label:'All measurements',value:'all'},...(pending.measures||[]).filter(m=>m.points?.length).map(m=>({label:m.label+' ('+m.unit+')',value:m.key}))]});published=key;}
    if(day!==pending.today){day=pending.today;window.dash_clientside.set_props('measure-today',{data:day});}
    if(palette!==JSON.stringify(pending.palette)){palette=JSON.stringify(pending.palette);window.dash_clientside.set_props('measure-palette',{data:pending.palette});}
    if(theme!==pending.theme){theme=pending.theme;window.dash_clientside.set_props('measure-theme',{forceColorScheme:theme});document.documentElement.dataset.theme=theme;}
    if(pending.category!=null){window.dash_clientside.set_props('measure-category-select',{value:String(pending.category)});pending.category=null;}
   }
   send('ready');return received?'Connected':'';
  }}
 });
 document.addEventListener('click',event=>{
  const source=event.target.closest('[data-measure-source]');
  if(source){event.preventDefault();send('navigate',{path:source.getAttribute('href')});return;}
  const control=event.target.closest('[data-measure-action]');
  if(control)send(control.dataset.measureAction,{measure:control.dataset.measure,id:control.dataset.readingId});
 });
 // A chart grows in place to the full row and a taller plot when pressed, and shrinks back on a second
 // press (owner, 2026-10-28: a chart is enlarged where it is, not sent to its own page). A press on a
 // reading opens the report it came from.
 const resize=card=>{
  const plot=card.querySelector('.js-plotly-plot'),height=card.classList.contains('is-expanded')?440:240;
  if(!plot||!window.Plotly)return;
  // The graph's wrapper carries its height inline (Dash), so it grows with the plot.
  const wrap=plot.closest('.dash-graph');if(wrap)wrap.style.height=height+'px';
  // After the card takes its new width, the plot fills it.
  requestAnimationFrame(()=>window.Plotly.relayout(plot,{height,width:plot.parentElement.clientWidth}));
 };
 const expand=card=>{
  const open=card.classList.toggle('is-expanded');
  const button=card.querySelector('[data-measure-expand]');
  if(button){button.setAttribute('aria-pressed',String(open));const name=open?'Shrink chart':'Expand chart';button.setAttribute('aria-label',name);}
  resize(card);
 };
 let pointPressed=0;
 setInterval(()=>{
  for(const plot of document.querySelectorAll('.js-plotly-plot:not([data-bridged])')){
   if(!plot.on)continue;plot.dataset.bridged='1';
   plot.on('plotly_click',data=>{const target=data?.points?.[0]?.customdata?.[3];pointPressed=Date.now();if(target)send('navigate',{path:target});});
   // A chart drawn again (a new range) keeps its card's size.
   if(plot.closest('.preview-card.is-expanded'))resize(plot.closest('.preview-card'));
  }
 },400);
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-measure-expand]');
  if(button){expand(button.closest('.preview-card'));return;}
  const plot=event.target.closest('.preview-plot');
  if(plot)setTimeout(()=>{if(Date.now()-pointPressed>300)expand(plot.closest('.preview-card'));},0);
 });
 let previous=0;
 setInterval(()=>{const height=Math.ceil(document.querySelector('.measurements-body')?.getBoundingClientRect().height||480);if(Math.abs(height-previous)>2){previous=height;send('resize',{height});}},300);
})();
