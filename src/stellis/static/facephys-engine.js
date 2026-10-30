/** Local FacePhys adapter. Upstream crop, RGB scaling, dt smoothing and state map.
 * No frames or signals leave this module. One outstanding inference bounds latency.
 */
export class FacePhysEngine {
  async init(onResult, onError) {
    this.worker = new Worker('/static/vendor/facephys/inference_worker.js');
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.canvas.height = 36;
    this.ctx = this.canvas.getContext('2d', {willReadFrequently: true});
    this.dt = 1 / 30;
    const ready = new Promise((resolve, reject) => {
      this.worker.onerror = () => { reject(new Error('FacePhys could not load')); onError(); };
      this.worker.onmessage = ({data}) => {
        if (data.type === 'initDone') resolve();
        if (data.type === 'error') { reject(new Error(data.msg)); onError(); }
        if (data.type === 'result') { this.busy = false; onResult(data.payload); }
      };
    });
    const base = '/static/vendor/facephys/models/';
    const [model, proj, state] = await Promise.all(['model.tflite', 'proj.tflite', 'state.gz'].map(async name => {
      const response = await fetch(base + name);
      if (!response.ok) throw new Error('FacePhys assets unavailable');
      return response;
    }));
    const modelBuffer = await model.arrayBuffer(), projBuffer = await proj.arrayBuffer();
    const stateJson = await new Response(state.body.pipeThrough(new DecompressionStream('gzip'))).json();
    if (this.closed) return;
    this.worker.postMessage({type:'init', payload:{modelBuffer, projBuffer, stateJson}}, [modelBuffer, projBuffer]);
    await ready;
    this.worker.postMessage({type:'setMode', payload:{isLowPower:true}});
  }
  frame(video, box, timestamp) {
    if (this.closed || this.busy || !box || video.readyState < 2) return;
    if (this.lastVideoTime === video.currentTime) return;
    this.lastVideoTime = video.currentTime;
    // Preserve the upstream smoothed clock while exposing actual gaps separately.
    if (this.last) {
      this.dt = this.dt * .997 + .003 * (timestamp - this.last) / 1000;
      this.time = (this.time + this.dt * 1000) * .997 + timestamp * .003;
    } else this.time = timestamp;
    this.last = timestamp;
    const raw = [box[0], box[1], box[2]-box[0], box[3]-box[1]];
    if (!this.filters) this.filters = raw.map(x => ({x, p:1}));
    const [x,y,w,h] = raw.map((value,i) => {
      const f=this.filters[i], p=f.p+.01, k=p/(p+.5);
      f.x += k*(value-f.x); f.p=(1-k)*p; return f.x;
    });
    const sx=Math.max(0,x), sy=Math.max(0,y-h*1.2*.2);
    const sw=Math.min(w,video.videoWidth-sx), sh=Math.min(h*1.2,video.videoHeight-sy);
    if (sw<=0 || sh<=0) return;
    this.ctx.drawImage(video,sx,sy,sw,sh,0,0,36,36);
    const rgba=this.ctx.getImageData(0,0,36,36).data, imgData=new Float32Array(36*36*3);
    for(let i=0,j=0;i<rgba.length;i+=4) { imgData[j++]=rgba[i]/255; imgData[j++]=rgba[i+1]/255; imgData[j++]=rgba[i+2]/255; }
    this.busy=true;
    this.worker.postMessage({type:'run',payload:{imgData,dtVal:this.dt,timestamp:this.time}},[imgData.buffer]);
  }
  close() { this.closed=true; this.worker?.terminate(); }
}
