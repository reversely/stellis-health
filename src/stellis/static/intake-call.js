/**
 * @fileoverview The intake call's connection: microphone audio to the
 * server, the agent's speech back, and call events for the call screen.
 * Speech pieces play in order; the server's "stop" cuts them off when the
 * person talks over the agent.
 */
(function(root) {
'use strict';
const STATUS = Object.freeze({
  ready:'Ready when you are', connecting:'Connecting', listening:'Listening',
  hearing:'Listening', transcribing:'Processing your answer', thinking:'Thinking',
  speaking:'Speaking', ending:'Finishing the call', typing:'Type your answer below',
  ended:'Call ended', lost:"Sorry we couldn't reach the agent",
});

/**
 * Starts a call.
 * @param {function(!Object)} onEvent Receives the server's messages, plus
 *     {type: 'caption', text, turn} as each piece starts playing,
 *     {type: 'level', person, agent},
 *     {type: 'mic', ok}, {type: 'closed'} and, once the goodbye has
 *     played, {type: 'ended'}.
 * @param {{path: (string|undefined), listen: (boolean|undefined)}=} options
 *     The call's WebSocket path, by default the intake call's, and whether
 *     to ask for the microphone; a call by text alone never asks.
 * @return {!Object} Controls: setMuted, setSpeaker, finishAnswer, sendText, end.
 */
function start(
    onEvent, {path = '/api/v1/intake/call', listen: useMic = true} = {}) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const address = new URL(path, location.origin);
  if (!useMic) address.searchParams.set('voice', '0');
  const socket = new WebSocket(`${scheme}://${location.host}${address.pathname}${address.search}`);
  socket.binaryType = 'arraybuffer';
  const context = new AudioContext();
  const output = context.createGain();
  const meter = context.createAnalyser();
  meter.fftSize = 512;
  output.connect(meter);
  meter.connect(context.destination);
  let playhead = 0;
  let sources = [];
  let pending = null;
  let muted = false;
  let speakerEnabled = true;
  let stream = null;
  let worklet = null;
  let personLevel = 0;
  let closed = false;
  let finishing = false;
  let voiceAvailable = true;
  let finishRequested = false;
  let finished = false;
  let idleState = null;
  const timers = new Set();

  const later = (fn, ms) => {
    const id = setTimeout(() => {
      timers.delete(id);
      fn();
    }, Math.max(0, ms));
    timers.add(id);
  };
  const send = (data) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
  };

  async function listen() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1
        },
      });
      if (closed || finishing || !voiceAvailable) {stopMicrophone(); return;}
      await context.audioWorklet.addModule('/static/intake-mic.worklet.js');
      if (closed || finishing || !voiceAvailable) {stopMicrophone(); return;}
      worklet = new AudioWorkletNode(context, 'stellis-mic');
      worklet.port.onmessage = ({data}) => {
        personLevel = muted ? 0 : data.level;
        if (!muted && socket.readyState === WebSocket.OPEN)
          socket.send(data.pcm);
      };
      context.createMediaStreamSource(stream).connect(worklet);
      onEvent({type: 'mic', ok: true});
    } catch {
      voiceAvailable = false;
      stopMicrophone();
      if (!closed && !finishing) onEvent({type: 'mic', ok: false});
    }
  }

  /** Queues one piece of the agent's speech. */
  function play(bytes, piece) {
    const samples = new Int16Array(bytes);
    const buffer = context.createBuffer(1, samples.length, piece.rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(output);
    const at = Math.max(context.currentTime + 0.02, playhead);
    source.start(at);
    playhead = at + buffer.duration;
    sources.push(source);
    source.onended = () => {
      sources = sources.filter((item) => item !== source);
      if (!sources.length) {
        send({type: 'played'});
        if (finishRequested) finishWhenDrained();
        else if (idleState) {onEvent(idleState); idleState = null;}
        else if (!finishing) onEvent({type:'state', state:'thinking'});
      }
    };
    const wait = (at - context.currentTime) * 1000;
    later(() => {
      onEvent({type: 'caption', text: piece.text, turn: piece.turn, audio:true});
      if (piece.first) send({type: 'playing', turn: piece.turn});
    }, wait);
  }

  function stopMicrophone() {
    for (const track of stream?.getTracks() || []) track.stop();
    worklet?.disconnect();
    personLevel = 0;
  }

  function finishWhenDrained() {
    if (!finishRequested || sources.length || closed || finished) return;
    finished = true;
    onEvent({type:'ended'});
  }

  function stopSpeech() {
    lastTurn = -1;
    for (const source of sources) {
      try {
        source.onended = null;
        source.stop();
      } catch {
        // A source that already ended needs no stop.
      }
    }
    sources = [];
    pending = null;
    idleState = null;
    playhead = 0;
    for (const id of timers) clearTimeout(id);
    timers.clear();
    onEvent({type: 'caption', text: ''});
  }

  let lastTurn = -1;
  socket.onmessage = ({data}) => {
    if (closed) return;
    if (data instanceof ArrayBuffer) {
      if (pending) play(data, pending);
      pending = null;
      return;
    }
    const message = JSON.parse(data);
    if (message.type === 'ready') {
      voiceAvailable = Boolean(message.voice);
      if (useMic && voiceAvailable) listen();
    }
    if (message.type === 'say') {
      const first = message.turn !== lastTurn;
      lastTurn = message.turn;
      if (message.audio) {
        pending = {...message, first};
      } else {
        onEvent({type: 'caption', text: message.text, turn: message.turn, audio:false});
      }
    } else if (message.type === 'retrying') {
      stopSpeech();
    } else if (message.type === 'mic' && !message.ok) {
      voiceAvailable = false;
      stopMicrophone();
    } else if (message.type === 'stop') {
      if (!finishing) stopSpeech();
    } else if (message.type === 'ending' || message.type === 'ended') {
      if (!finishing) {
        finishing = true;
        stopMicrophone();
        onEvent({type:'ending'});
      }
      if (message.type === 'ended') {
        finishRequested = true;
        finishWhenDrained();
      }
      return;
    } else if (message.type === 'state') {
      if (finishing) return;
      if (message.state === 'listening' || message.state === 'hearing') {
        if (sources.length || pending) {idleState = message; return;}
      } else idleState = null;
    }
    onEvent(message);
  };
  socket.onopen = () => {
    send({type:'speaker', on:speakerEnabled && useMic});
  };
  socket.onclose = (event) => {
    // 4401: the session ended or the records locked; sign in again and come back.
    if (event.code === 4401) location.assign('/auth?next=' + encodeURIComponent(location.pathname));
    else if (!closed && !finishing) onEvent({type: 'closed'});
  };

  const levels = new Uint8Array(meter.fftSize);
  (function measure() {
    if (closed) return;
    meter.getByteTimeDomainData(levels);
    let sum = 0;
    for (const value of levels) sum += ((value - 128) / 128) ** 2;
    onEvent({
      type: 'level',
      person: personLevel,
      agent: Math.sqrt(sum / levels.length),
      speaking: playhead > context.currentTime
    });
    requestAnimationFrame(measure);
  })();

  return {
    /** @param {boolean} value */
    setMuted(value) {
      muted = value;
      for (const track of stream?.getAudioTracks() || [])
        track.enabled = !value;
    },
    /** @param {boolean} value */
    setSpeaker(value) {
      speakerEnabled = value;
      output.gain.value = value ? 1 : 0;
      send({type: 'speaker', on: value});
    },
    retryReply() {
      if (!finishing) send({type:'retry_reply'});
    },
    finishAnswer() {
      send({type: 'finish_answer'});
    },
    /** @param {string} text */
    sendText(text) {
      if (!finishing) send({type: 'text', text});
    },
    end() {
      closed = true;
      send({type: 'end'});
      stopSpeech();
      socket.close();
      for (const track of stream?.getTracks() || []) track.stop();
      context.close();
    },
  };
}

root.StellisIntakeCall = {start, STATUS};
})(window);
