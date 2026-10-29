"""Loopback-only disposable server for synthetic call recovery browser checks."""
import os,time,asyncio
import tempfile
os.environ.setdefault('STELLIS_DATA_DIR', tempfile.mkdtemp(prefix='stellis-call-recovery-'))
os.environ['STELLIS_RUNTIME_PROFILE']='direct'
os.environ['STELLIS_ACCOUNT_ID']='2'
os.environ['STELLIS_SHARED_PROVIDER_KEYS']='1'
from stellis import auth,intake,intake_voice,processing,server,onboarding
import numpy as np
from types import SimpleNamespace
import uvicorn
if not auth.has_account():
    auth.register('recovery-owner','isolated-recovery-owner-password')
    auth.register('recovery-review','isolated-recovery-review-password')
auth.complete_onboarding(2)
onboarding.reopen(2);onboarding.choose(2,[],'call')
os.environ['STELLIS_AUTH_MODE']='required'
class Detector:
    def __init__(self):self.samples=[];self.start=0;self.position=0;self.segments=[]
    def accept_waveform(self,window):
        if np.max(np.abs(window))>.0001:
            if not self.samples:self.start=self.position
            self.samples.extend(window)
        self.position+=len(window)
    def is_speech_detected(self):return bool(self.samples)
    def empty(self):return not self.segments
    @property
    def front(self):return self.segments[0]
    def pop(self):self.segments.pop(0)
    def flush(self):
        if self.samples:self.segments.append(SimpleNamespace(start=self.start,samples=np.array(self.samples)));self.samples=[]
    def reset(self):self.samples=[];self.position=0;self.segments=[]
class Engine:
    def vad(self):return Detector()
    def transcribe(self,samples):
        level=float(np.max(np.abs(samples)))
        if level>.05:time.sleep(.2);raise RuntimeError('Synthetic recognition failure')
        return 'Manage my appointments' if level>.02 else ''
    def speak(self,text):
        stats['synthesis_calls'] += 1
        return np.zeros(640,dtype='<i2').tobytes(),16000
original_reply = intake.reply
attempts = {}
stats = {'model_calls':0, 'synthesis_calls':0}
async def reply(timings=None):
    last = next((t['text'] for t in reversed(intake.load()['turns']) if t['role']=='person'), '')
    if last == 'Test delayed reply':
        attempts[last] = attempts.get(last, 0) + 1
        if attempts[last] % 2:
            await asyncio.sleep(2)
        yield 'Your original answer is still saved.'
        return
    from stellis.call_lifecycle import wants_end
    if wants_end(last):
        async for piece in original_reply(timings):yield piece
    else:yield 'Which appointments would you like to organize?'
async def stream_tokens(messages, tools=None):
    stats['model_calls'] += 1
    yield 'You can keep relevant records in a notebook.'
intake.stream_tokens = stream_tokens
intake_voice.REPLY_PIECE_TIMEOUT_S = .5
@server.app.post('/fixture/reset-intake')
def reset_intake():
    attempts.clear()
    return intake.reset()
@server.app.get('/fixture/counts')
def counts():return stats
async def notes():return intake.load()
intake.reply=reply;intake.update_notes=notes
intake_voice.acquire=lambda:Engine();intake_voice.release=lambda:None
processing.start_worker=lambda:None;processing.index_missing_voices=lambda:None
uvicorn.run(server.app,host='127.0.0.1',port=8784,access_log=False)
