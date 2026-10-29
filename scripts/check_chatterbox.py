"""Check the explicitly selected speech endpoint; credentials never enter output.

For the owner's ignored Nebius export use --nebius-env .env. This reads only
its public endpoint URL/token into this child process, never into the browser.
"""
import argparse,hashlib,json,os,shlex,time,wave
from pathlib import Path
from stellis import call_tts
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--nebius-env',type=Path);p.add_argument('--voice');p.add_argument('--output',type=Path,required=True)
p.add_argument('--text',default='Stellis connects your health records to the routines you choose. Review the source, approve the change, and keep your history together.')
a=p.parse_args()
if a.nebius_env:
 values={}
 for line in a.nebius_env.read_text().splitlines():
  if not line.strip() or line.lstrip().startswith('#'):continue
  key,sep,value=line.partition('=')
  if not sep:raise SystemExit('Expected an environment file with KEY=value lines')
  parts=shlex.split(value,comments=True);values[key.strip()]=parts[0] if len(parts)==1 else ''
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL=values.get('NEBIUS_PUBLIC_ENDPOINT_URL',''),STELLIS_CHATTERBOX_NEBIUS_TOKEN=values.get('NEBIUS_ENDPOINT_TOKEN',''),STELLIS_CHATTERBOX_VOICE=a.voice or 'Emily.wav')
if a.voice:os.environ['STELLIS_CHATTERBOX_VOICE']=a.voice
a.output.mkdir(parents=True,exist_ok=True);check=call_tts.check();result={'check':check,'synthesis':{'state':'not_started'}}
if check['state'] in {'voice_available','voice_unverified'}:
 start=time.perf_counter()
 try:
  pcm,rate=call_tts.speak(a.text);path=a.output/'voice-validation.wav'
  with wave.open(str(path),'wb') as audio:audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(rate);audio.writeframes(pcm)
  result['synthesis']={'state':'succeeded','client_ms':round((time.perf_counter()-start)*1000,2),'sample_rate':rate,'audio_seconds':len(pcm)/2/rate,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'text':a.text,'gpu_execution_ms':None}
 except call_tts.VoiceUnavailableError:
  result['check']={**check,'state':'voice_missing'}
  result['synthesis']={'state':'failed','message':'The selected voice is not installed on the speech endpoint.'}
 except Exception:
  result['synthesis']={'state':'failed','message':'The selected speech endpoint did not return valid audio.'}
(a.output/'speech-check.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
raise SystemExit(0 if result['synthesis']['state']=='succeeded' else 1)
