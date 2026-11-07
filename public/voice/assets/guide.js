/* One persistent OpenAI Realtime WebRTC conversation plus local route control. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const app = {events: [], seq: 0, places: [], route: [], walking: false,
    micStream: null, responses: new Map(), transcripts: new Map()};
  window.laganoVoice = app;
  const record = (kind, data = {}) => app.events.push({kind, client_ms: performance.now(), ...data});
  const send = message => {
    app.control?.receive(message);
  };
  const sendRealtime = event => {
    if (app.dc?.readyState !== 'open') return false;
    app.dc.send(JSON.stringify(event));
    record('realtime_client_event', {type: event.type});
    return true;
  };
  const message = (role, text) => {
    if (!text) return;
    const node = document.createElement('div');
    node.className = `message ${role}`;
    node.textContent = text;
    $('#conversation').append(node);
    node.scrollIntoView({block: 'nearest'});
  };
  const waitOpen = target => new Promise((resolve, reject) => {
    if (target.readyState === 'open' || target.readyState === WebSocket.OPEN) return resolve();
    const timer = setTimeout(() => reject(Error('timeout')), 12000);
    target.addEventListener('open', () => { clearTimeout(timer); resolve(); }, {once: true});
    target.addEventListener('error', () => { clearTimeout(timer); reject(Error('connection')); }, {once: true});
  });
  function sessionInstructions(street) {
    return app.baseInstructions.replace(
      /TRENUTAČNA ULICA ILI TRG: [^.]+\./,
      `TRENUTAČNA ULICA ILI TRG: ${street}.`
    );
  }
  function cancelSpeech() {
    if (!app.dc || app.dc.readyState !== 'open' || !app.responses.size) return;
    sendRealtime({type: 'response.cancel'});
    sendRealtime({type: 'output_audio_buffer.clear'});
  }
  function reportRealtime(event_kind, extra = {}) {
    send({type: 'realtime_event', event_kind, ...extra});
  }
  function realtimeEvent(event) {
    record('realtime_server_event', {type: event.type});
    if (event.type === 'input_audio_buffer.speech_started') {
      $('#status').textContent = 'Slušam…';
      reportRealtime('speech_started');
    } else if (event.type === 'input_audio_buffer.speech_stopped') {
      $('#status').textContent = 'Pripremam odgovor…';
      reportRealtime('speech_stopped');
    } else if (event.type === 'conversation.item.input_audio_transcription.completed') {
      const text = (event.transcript || '').trim();
      if (text) { message('user', text); reportRealtime('user_transcript', {text}); }
    } else if (event.type === 'response.created') {
      const response = event.response || {};
      app.responses.set(response.id, response.metadata || {});
      reportRealtime('assistant_started', {response_id: response.id,
        utterance_id: response.metadata?.utterance_id,
        purpose: response.metadata?.purpose});
      $('#status').textContent = 'Vodič govori. Možeš ga prekinuti.';
    } else if (event.type === 'response.output_audio_transcript.delta') {
      const current = app.transcripts.get(event.response_id) || '';
      app.transcripts.set(event.response_id, current + (event.delta || ''));
    } else if (event.type === 'response.output_audio_transcript.done') {
      app.transcripts.set(event.response_id, event.transcript || app.transcripts.get(event.response_id) || '');
    } else if (event.type === 'response.done') {
      const response = event.response || {};
      const meta = app.responses.get(response.id) || response.metadata || {};
      const text = (app.transcripts.get(response.id) || '').trim();
      const completed = response.status === 'completed';
      if (completed) message('assistant', text);
      reportRealtime(completed ? 'assistant_completed' : 'assistant_cancelled', {
        text, response_id: response.id, utterance_id: meta.utterance_id, purpose: meta.purpose});
      app.responses.delete(response.id); app.transcripts.delete(response.id);
      if (completed) $('#status').textContent = 'Nastavljamo šetnju.';
    } else if (event.type === 'error') {
      reportRealtime('error', {text: event.error?.message || ''});
      $('#status').textContent = 'Glasovna veza javila je pogrešku. Pokušaj ponovno.';
    }
  }
  function backendEvent(event) {
    record('backend_event', {event});
    if (event.kind === 'clear') cancelSpeech();
    if (event.kind === 'realtime_prompt') {
      sendRealtime({type: 'response.create', response: {
        output_modalities: ['audio'], instructions: event.instructions,
        metadata: {utterance_id: event.utterance_id, purpose: event.purpose,
          poi_id: event.poi_id}
      }});
    }
    if (event.kind === 'street_changed' || event.kind === 'gps_processed') {
      const street = event.street;
      if (street) {
        $('#location').textContent = `Trenutačno: ${street}`;
        if (street !== app.street) {
          app.street = street;
          sendRealtime({type: 'session.update', session: {instructions: sessionInstructions(street)}});
        }
      }
    }
    if (event.kind === 'story_interrupted') {
      app.baseInstructions = app.baseInstructions.replace(
        /(?:Trenutačno nema prekinute teme za nastavak|Prekinuta tema koju možeš nastaviti samo ako korisnik pristane: [^.]+)\./,
        `Prekinuta tema koju možeš nastaviti samo ako korisnik pristane: ${event.from_poi_name}.`
      );
      sendRealtime({type: 'session.update', session: {instructions: sessionInstructions(app.street)}});
      $('#status').textContent = `Stigli smo do ${event.to_poi_name}; poslije ću ponuditi nastavak priče o ${event.from_poi_name}.`;
    }
    if (event.kind === 'stopped') {
      cancelSpeech();
      $('#status').textContent = 'Govor je zaustavljen. I dalje te slušam.';
    }
    if (event.kind === 'error') $('#status').textContent = event.message;
  }
  function createTourControl() {
    const control={active:app.places[0],announced:new Set(),enteredAt:null,auto:true,paused:false,target:null,interrupted:null,street:null,lastSeq:-1};
    const anchors=app.streetAnchors||[];
    const distance=(a,b)=>L.latLng(a[1],a[0]).distanceTo([b[1],b[0]]);
    const emit=event=>backendEvent(event);
    control.receive=msg=>{
      if(msg.type==='settings'){control.auto=!!msg.auto;return;}
      if(msg.type==='stop'){control.paused=true;emit({kind:'stopped'});return;}
      if(msg.type==='realtime_event')return;
      if(msg.type!=='gps'||msg.sample_seq<=control.lastSeq)return;
      control.lastSeq=msg.sample_seq;
      const point=[msg.lon,msg.lat];
      const anchor=anchors.length?[...anchors].sort((a,b)=>distance(point,a[0])-distance(point,b[0]))[0]:null;
      const street=anchor?.[1]||'Zagreb';
      const nearest=[...app.places].sort((a,b)=>distance(point,a.coordinates)-distance(point,b.coordinates))[0];
      const meters=distance(point,nearest.coordinates);
      if(nearest.id!==control.active.id){control.active=nearest;control.enteredAt=null;}
      emit({kind:'gps_processed',street,poi_id:nearest.id,distance_m:meters});
      if(!control.auto||meters>(nearest.trigger_m||60)||msg.accuracy_m>15){control.enteredAt=null;return;}
      control.enteredAt??=performance.now();
      const entries=[...app.responses.values()];
      const blocking=entries.some(meta=>meta.purpose!=='story');
      if(performance.now()-control.enteredAt<2000||control.paused||control.announced.has(nearest.id)||blocking)return;
      control.announced.add(nearest.id);
      if(entries.some(meta=>meta.purpose==='story')&&control.target){
        control.interrupted=control.target;
        emit({kind:'story_interrupted',from_poi_name:control.target.name,to_poi_name:nearest.name});
      }
      emit({kind:'clear'});control.target={id:nearest.id,name:nearest.name};
      const resume=control.interrupted?' Nakon priče pitaj želi li korisnik nastavak prekinute priče o mjestu '+control.interrupted.name+'.':'';
      emit({kind:'realtime_prompt',utterance_id:crypto.randomUUID(),purpose:'story',poi_id:nearest.id,
        instructions:'Ispričaj sljedeću unaprijed provjerenu priču o mjestu '+nearest.name+' prirodno i bez dodavanja činjenica: '+nearest.story+resume});
    };
    return control;
  }

  function position(point) {
    app.position = point;
    app.marker?.setLatLng([point[1], point[0]]);
    send({type: 'gps', sample_seq: ++app.seq, lon: point[0], lat: point[1],
      accuracy_m: 8, speed_mps: app.walking ? 1.3 : 0});
  }
  function along(fraction) {
    if (!app.route.length) return app.places[0].coordinates;
    let left = app.total * fraction;
    for (let index = 1; index < app.route.length; index++) {
      const a = app.route[index - 1], b = app.route[index];
      const length = L.latLng(a[1], a[0]).distanceTo([b[1], b[0]]);
      if (left <= length) {
        const part = length ? left / length : 0;
        return [a[0] + part * (b[0] - a[0]), a[1] + part * (b[1] - a[1])];
      }
      left -= length;
    }
    return app.route.at(-1);
  }
  function progressFor(point) {
    let best = Infinity, bestAlong = 0, travelled = 0;
    app.route.forEach((current, index) => {
      const gap = L.latLng(point[1], point[0]).distanceTo([current[1], current[0]]);
      if (gap < best) { best = gap; bestAlong = travelled; }
      if (index + 1 < app.route.length) travelled += L.latLng(current[1], current[0])
        .distanceTo([app.route[index + 1][1], app.route[index + 1][0]]);
    });
    return app.total ? Math.round(1000 * bestAlong / app.total) : 0;
  }
  function sessionUI(active) {
    $('#start').hidden = active; $('#session-actions').hidden = !active;
    $('#microphone-note').hidden = active; $('#question').disabled = !active;
  }
  function cleanup(text) {
    clearInterval(app.gpsTimer); app.walking = false;
    app.micStream?.getTracks().forEach(track => track.stop());
    app.pc?.close(); app.remoteAudio?.remove(); app.control=null; clearTimeout(app.endTimer); app.responses.clear(); app.transcripts.clear();
    app.pc = app.ws = app.dc = app.micStream = null;
    sessionUI(false); $('#status').textContent = text;
    $('#start').disabled = !app.ready; $('#start').textContent = 'Pokreni šetnju';
    $('#walk').textContent = 'Nastavi hodanje';
    for (const id of ['stop', 'send', 'walk', 'end']) $('#' + id).disabled = true;
    app.starting = false;
  }
  async function startRealtime(session) {
    app.pc = new RTCPeerConnection();
    app.remoteAudio = document.createElement('audio');
    app.remoteAudio.autoplay = true;
    app.pc.ontrack = event => { app.remoteAudio.srcObject = event.streams[0]; };
    app.dc = app.pc.createDataChannel('oai-events');
    app.dc.onmessage = message => realtimeEvent(JSON.parse(message.data));
    app.micStream = await navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: true, noiseSuppression: true, channelCount: 1
    }});
    app.pc.addTrack(app.micStream.getAudioTracks()[0], app.micStream);
    const offer = await app.pc.createOffer();
    await app.pc.setLocalDescription(offer);
    const answer = await fetch(`/voice/realtime/${session.id}`, {method: 'POST',
      headers: {'Content-Type': 'application/sdp'}, body: offer.sdp});
    if (!answer.ok) throw Error('realtime');
    await app.pc.setRemoteDescription({type: 'answer', sdp: await answer.text()});
    await waitOpen(app.dc);
  }
  $('#start').onclick = async () => {
    if (app.starting) return;
    app.starting = true; $('#start').disabled = true; $('#start').textContent = 'Pokrećem…';
    try {
      const response = await fetch('/voice/sessions', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode: 'realtime'})});
      if (!response.ok) throw Error('session');
      const session = await response.json(); app.baseInstructions = session.instructions;
      app.control=createTourControl();
      await startRealtime(session);
      send({type: 'settings', auto: $('#auto').checked});
      app.endTimer=setTimeout(()=>cleanup('Probna šetnja je završena nakon pet minuta.'),300000);
      app.starting = false; sessionUI(true);
      for (const id of ['stop', 'send', 'walk', 'end']) $('#' + id).disabled = false;
      const startPoint = app.places[Number($('#place').value)].coordinates;
      $('#progress').value = progressFor(startPoint);
      app.walking=true;$('#walk').textContent='Pauziraj hodanje';
      position(startPoint); app.gpsTimer = setInterval(() => position(app.position), 1000);
      $('#status').textContent = 'Šetnja je krenula. Realtime vodič te sluša.';
    } catch (error) {
      record('start_error', {message: String(error)});
      cleanup('Mikrofon ili Realtime veza nisu dostupni. Provjeri dopuštenje pa pokušaj ponovno.');
    }
  };
  $('#stop').onclick = () => { cancelSpeech(); send({type: 'stop'}); };
  $('#end').onclick = () => cleanup('Šetnja je završena.');
  window.addEventListener('pagehide', () => cleanup('Šetnja je završena.'));
  $('#question-form').onsubmit = event => {
    event.preventDefault(); const text = $('#question').value.trim();
    if (!text) return;
    message('user', text); reportRealtime('user_transcript', {text});
    sendRealtime({type: 'conversation.item.create', item: {type: 'message', role: 'user',
      content: [{type: 'input_text', text}]}});
    sendRealtime({type: 'response.create', response: {output_modalities: ['audio']}});
    $('#question').value = '';
  };
  $('#auto').onchange = () => send({type: 'settings', auto: $('#auto').checked});
  $('#place').onchange = () => {
    const place = app.places[Number($('#place').value)];
    $('#progress').value = progressFor(place.coordinates); position(place.coordinates);
    app.map?.panTo([place.coordinates[1], place.coordinates[0]]);
  };
  $('#progress').oninput = () => position(along(Number($('#progress').value) / 1000));
  $('#walk').onclick = () => {
    app.walking = !app.walking;
    $('#walk').textContent = app.walking ? 'Pauziraj hodanje' : 'Nastavi hodanje';
  };
  setInterval(() => {
    if (!app.walking || !app.total) return;
    const next = Math.min(1000, Number($('#progress').value) +1300/app.total);
    $('#progress').value = next; position(along(next / 1000));
    if (next >= 1000) { app.walking = false; $('#walk').textContent = 'Ruta završena'; }
  }, 1000);
  async function init() {
    try {
      const [status, places, route, streets] = await Promise.all(['/voice/status', '/voice/places', '/voice/route', '/voice/streets.json']
        .map(async path => { const response = await fetch(path); if (!response.ok) throw Error(path); return response.json(); }));
      app.places = places; app.route = route.coordinates; app.streetAnchors=streets;
      $('#availability').textContent = status.configured ? 'Realtime vodič spreman za probu.' : 'Vodič trenutačno nije povezan.';
      places.forEach((place, index) => { const option = document.createElement('option');
        option.value = index; option.textContent = place.name; $('#place').append(option); });
      app.map = L.map('map').setView([places[0].coordinates[1], places[0].coordinates[0]], 15);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution: '© OpenStreetMap contributors'}).addTo(app.map);
      app.line = L.polyline(app.route.map(point => [point[1], point[0]]), {color: '#405c42', weight: 5}).addTo(app.map);
      app.map.fitBounds(app.line.getBounds(), {padding: [26, 26], maxZoom: 16, animate: false});
      places.forEach(place => L.circleMarker([place.coordinates[1], place.coordinates[0]],
        {radius: 6, color: '#a25d40', fillColor: '#fff', fillOpacity: 1, weight: 2})
        .addTo(app.map).bindTooltip(place.name));
      app.marker = L.circleMarker([places[0].coordinates[1], places[0].coordinates[0]],
        {radius: 10, color: '#284f3d', fillColor: '#b8cc85', fillOpacity: 1}).addTo(app.map);
      app.total = app.route.slice(1).reduce((sum, point, index) => sum + L.latLng(point[1], point[0])
        .distanceTo([app.route[index][1], app.route[index][0]]), 0);
      app.position = places[0].coordinates; app.ready = status.configured;
      $('#start').disabled = !app.ready;
    } catch (error) {
      $('#availability').textContent = 'Podaci vodiča nisu dostupni. Pokušaj osvježiti stranicu.';
      $('#start').disabled = true;
    }
  }
  init();
})();
