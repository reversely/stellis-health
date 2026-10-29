/* Keep MediaRecorder's track stable while replacing the physical microphone. */
(() => {
  const key='stellis-recording-microphone';
  const preference=()=>{try{return localStorage.getItem(key)||'';}catch{return '';}};
  const remember=id=>{try{localStorage.setItem(key,id);}catch{}};
  async function open(deviceId){
    const context=new AudioContext();
    const destination=context.createMediaStreamDestination();
    let input=null,source=null,closed=false;
    const result={stream:destination.stream,deviceId:'',label:'Microphone',onended:null,
      async switchTo(id){
        let next;
        try{
          next=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,...(id?{deviceId:{exact:id}}:{})},video:false});
          if(closed)throw new Error('Recording has stopped.');
          const track=next.getAudioTracks()[0];
          if(!track||track.readyState!=='live')throw new Error('The microphone is unavailable.');
          const nextSource=context.createMediaStreamSource(next);
          await context.resume();
          if(closed){nextSource.disconnect();throw new Error('Recording has stopped.');}
          const previous=input,previousSource=source;
          nextSource.connect(destination);input=next;source=nextSource;
          result.deviceId=id;result.label=track.label||'Microphone';
          track.addEventListener('ended',()=>{if(!closed&&input===next)result.onended?.();});
          previousSource?.disconnect();previous?.getTracks().forEach(t=>t.stop());
          remember(id);
        }catch(error){if(next!==input)next?.getTracks().forEach(t=>t.stop());throw error;}
      },
      close(){if(closed)return;closed=true;source?.disconnect();input?.getTracks().forEach(t=>t.stop());destination.stream.getTracks().forEach(t=>t.stop());context.close().catch(()=>{});}
    };
    try{await result.switchTo(deviceId);return result;}catch(error){result.close();throw error;}
  }
  window.StellisRecordingInput={open,preference,remember};
})();
