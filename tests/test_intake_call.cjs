const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function fixture() {
  const events=[], sockets=[], contexts=[], timers=new Map(); let timer=0;
  class Socket {
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
    send(data){this.sent.push(JSON.parse(data));}
    close(){this.onclose?.({code:1000});}
    message(value){this.onmessage({data:value instanceof ArrayBuffer ? value : JSON.stringify(value)});}
  }
  class Audio {
    constructor(){this.currentTime=0;this.sources=[];this.destination={};contexts.push(this);}
    createGain(){return {gain:{value:1},connect(){}};}
    createAnalyser(){return {fftSize:512,connect(){},getByteTimeDomainData(a){a.fill(128);}};}
    createBuffer(channels,length,rate){return {duration:length/rate,getChannelData(){return new Float32Array(length);}};}
    createBufferSource(){const source={connect(){},start(){},stop(){this.stopped=true;}};this.sources.push(source);return source;}
    close(){}
  }
  const root={};
  vm.runInNewContext(fs.readFileSync('src/stellis/static/intake-call.js','utf8'),{
    window:root, location:{protocol:'http:',host:'localhost',origin:'http://localhost'},
    URL,WebSocket:Socket,AudioContext:Audio,ArrayBuffer,Int16Array,Uint8Array,
    requestAnimationFrame(){},setTimeout(fn){const id=++timer;timers.set(id,()=>{timers.delete(id);fn();});return id;},clearTimeout(id){timers.delete(id);},
  });
  const call=root.StellisIntakeCall.start(event=>events.push(event),{listen:false});
  return {call,events,socket:sockets[0],audio:contexts[0],timers};
}

test('natural completion drains every queued audio piece before ending once',()=>{
 const f=fixture();
 for(let i=0;i<2;i++){f.socket.message({type:'say',text:'Goodbye',turn:1,audio:true,rate:16000});f.socket.message(new Int16Array(16000).buffer);}
 f.socket.message({type:'ending'});f.socket.message({type:'ended'});f.socket.onclose({code:1000});
 assert.equal(f.events.filter(e=>e.type==='ended').length,0);
 f.audio.sources[0].onended();assert.equal(f.events.filter(e=>e.type==='ended').length,0);
 f.socket.message({type:'stop'});assert.equal(f.audio.sources[1].stopped,undefined);
 f.audio.sources[1].onended();f.socket.message({type:'ended'});f.socket.onclose({code:1000});
 assert.equal(f.events.filter(e=>e.type==='ended').length,1);
 assert.equal(f.events.filter(e=>e.type==='closed').length,0);f.call.end();
});

test('listening waits until queued speech finishes',()=>{
 const f=fixture();f.socket.message({type:'say',text:'A complete sentence',turn:1,audio:true,rate:16000});f.socket.message(new Int16Array(16000).buffer);
 f.socket.message({type:'state',state:'listening'});assert.equal(f.events.filter(e=>e.type==='state').length,0);
 f.audio.sources[0].onended();assert.equal(f.events.at(-1).state,'listening');f.call.end();
});

test('text-only completion needs no audio timer or speech initialization',()=>{
 const f=fixture();assert.equal(new URL(f.socket.url).searchParams.get('voice'),'0');
 f.socket.message({type:'ended'});assert.equal(f.events.at(-1).type,'ended');assert.equal(f.timers.size,0);f.call.end();
});

test('retry uses the accepted turn rather than sending another answer',()=>{
 const f=fixture();f.call.retryReply();assert.equal(f.socket.sent.at(-1).type,'retry_reply');
 f.socket.message({type:'ending'});const count=f.socket.sent.length;f.call.sendText('late answer');assert.equal(f.socket.sent.length,count);f.call.end();
});


test('the gap before another reply segment shows Thinking',()=>{
 const f=fixture();f.socket.message({type:'say',text:'First clause',turn:1,audio:true,rate:16000});f.socket.message(new Int16Array(16000).buffer);
 f.audio.sources[0].onended();assert.equal(f.events.at(-1).state,'thinking');
 f.socket.message({type:'state',state:'listening'});assert.equal(f.events.at(-1).state,'listening');f.call.end();
});


test('a retry starts fresh playback timing for the same accepted turn',()=>{
 const f=fixture();
 function piece(){f.socket.message({type:'say',text:'Reply',turn:1,audio:true,rate:16000});f.socket.message(new Int16Array(16000).buffer);for(const fn of [...f.timers.values()])fn();}
 piece();f.audio.sources[0].onended();f.socket.message({type:'retrying',turn:1});piece();
 assert.equal(f.socket.sent.filter(e=>e.type==='playing').length,2);f.call.end();
});
