/* Selected route + browser Geolocation + one OpenAI WebRTC conversation. */
(() => {
'use strict';
const $=s=>document.querySelector(s),KEY='moje-setnje:selected-route:v1';
const app={route:[],places:[],responses:new Map(),transcripts:new Map(),events:[],announced:new Set(),
 ready:false,active:false,starting:false,epoch:0,watchId:null,lastFix:null,audioPlaying:false,userTalking:false,awaitingAnswer:false};
window.laganoVoice=app;
const record=(kind,data={})=>{app.events.push({kind,client_ms:performance.now(),...data});if(app.events.length>400)app.events.shift();};
function message(role,text,id=null){
 if(!text)return;
 let n=id?document.getElementById('message-'+id):null;
 if(!n){n=document.createElement('div');n.className='message '+role;if(id)n.id='message-'+id;
  const label=document.createElement('strong');label.className='speaker';label.textContent=role==='user'?'Ti':'AI vodič';
  const body=document.createElement('span');body.className='message-text';n.append(label,body);$('#conversation').append(n);}
 n.querySelector('.message-text').textContent=text;
 $('#conversation-hint').hidden=true;
 const box=$('#conversation');box.scrollTop=box.scrollHeight;
}
function send(event){if(app.dc?.readyState!=='open')return false;app.dc.send(JSON.stringify(event));return true;}
function cancelSpeech(){if(app.responses.size)send({type:'response.cancel'});if(app.audioPlaying)send({type:'output_audio_buffer.clear'});}
function sessionUI(){document.body.classList.toggle('guide-active',app.active);const live=app.active||app.starting;$('#start').hidden=app.active||app.exampleOnly;$('#start').disabled=!app.ready||app.starting;
 $('#session-actions').hidden=!live;$('#question').disabled=!app.active;$('#send').disabled=!app.active;$('#stop').disabled=!app.active;
 $('#locate').hidden=!app.lastFix||!live;$('#demo-options').hidden=live;$('#start-demo').disabled=!app.ready||live||!app.places.length;$('#mode-note').hidden=!live;$('#microphone-state').hidden=!app.active;}
function clearGPS(){if(app.watchId!==null){navigator.geolocation.clearWatch(app.watchId);app.watchId=null;}clearInterval(app.tick);clearTimeout(app.gpsWaitTimer);app.rejectGPS?.(Error('cancelled'));app.rejectGPS=null;}
function cleanup(text){
 app.epoch++;app.active=false;app.starting=false;clearGPS();clearTimeout(app.endTimer);clearTimeout(app.connectionTimer);
 app.abort?.abort();app.abort=null;app.micStream?.getTracks().forEach(t=>t.stop());app.pc?.close();app.remoteAudio?.remove();
 app.micStream=app.pc=app.dc=app.remoteAudio=null;app.responses.clear();app.transcripts.clear();app.audioPlaying=false;
 app.demoPlace=null;$('#mode-badge').textContent=app.exampleOnly?'Pripremljeni primjer · demonstracija':'Tvoja ruta · GPS uživo';app.pendingOffer=null;app.offerAnswered=false;app.pendingStory=null;app.pendingQuestion=null;app.cancelPending=false;app.locationNote=null;app.lastStoryName=null;app.userTalking=app.awaitingAnswer=false;app.lastFix=null;app.progress=null;
 app.marker?.setStyle({opacity:0,fillOpacity:0});$('#locate').hidden=true;$('#gps-status').textContent='GPS i mikrofon su isključeni.';
 $('#status').textContent=text;$('#start').textContent='Pokreni vodiča';sessionUI();
}
function project(point,previous){
 const sx=111320*Math.cos(point[1]*Math.PI/180),sy=111320,x=point[0]*sx,y=point[1]*sy;
 let travelled=0,parts=[];
 for(let i=1;i<app.route.length;i++){const a=app.route[i-1],b=app.route[i],ax=a[0]*sx,ay=a[1]*sy,dx=(b[0]-a[0])*sx,dy=(b[1]-a[1])*sy,length=Math.hypot(dx,dy);
  const f=length?Math.max(0,Math.min(1,((x-ax)*dx+(y-ay)*dy)/(length*length))):0;
  parts.push({offset:Math.hypot(x-ax-f*dx,y-ay-f*dy),along:travelled+f*length});travelled+=length;
 }
 const min=Math.min(...parts.map(p=>p.offset));let near=parts.filter(p=>p.offset<=min+3);
 if(previous!==null&&previous!==undefined)near.sort((a,b)=>Math.abs(a.along-previous)-Math.abs(b.along-previous));
 else near.sort((a,b)=>a.along-b.along);
 return near[0];
}
function distance(a,b){return L.latLng(a[1],a[0]).distanceTo([b[1],b[0]]);}
function gpsQuality(){
 if(app.demoPlace)return {ok:true,along:app.demoPlace.along_m,label:'Demonstracija odabranog mjesta · stvarni GPS je isključen'};
 const fix=app.lastFix;
 if(!fix)return {ok:false,code:'missing',label:'Čekam GPS lokaciju…'};
 if(Math.abs(Date.now()-fix.timestamp)>15000)return {ok:false,code:'stale',label:'GPS signal je zastario. Priče čekaju novu lokaciju.'};
 if(fix.accuracy>50)return {ok:false,code:'inaccurate',label:'GPS je neprecizan (±'+Math.round(fix.accuracy)+' m). Priče čekaju precizniju lokaciju.'};
 const p=project(fix.point,app.progress);app.progress=p.along;
 if(p.offset>85)return {ok:false,code:'off_route',label:'Izvan odabrane rute · oko '+Math.round(p.offset)+' m do trase. Možeš pitati o mjestima uz rutu.'};
 return {ok:true,along:p.along,label:'GPS uživo · preciznost ±'+Math.round(fix.accuracy)+' m'};
}
function flushQuestion(){
 if(!app.active||!app.pendingQuestion||app.responses.size||app.audioPlaying||app.userTalking||app.dc?.readyState!=='open')return;
 const text=app.pendingQuestion;app.pendingQuestion=null;app.awaitingAnswer=true;
 message('user',text);
 send({type:'conversation.item.create',item:{type:'message',role:'user',content:[{type:'input_text',text}]}});
 send({type:'response.create',response:{output_modalities:['audio'],metadata:{purpose:'question'}}});
}
function updateLocationContext(note){
 if(note===app.locationNote)return;app.locationNote=note;
 const offer=app.pendingOffer;
 const consent=offer?'\nAktualna ponuda: ponudio si korisniku kratku priču o mjestu '+JSON.stringify(offer.place.name)+'. Pričekaj odgovor. Ako prihvati (npr. može, reci, da, ispričaj), odmah ispričaj dvije do tri kratke rečenice, najviše 60 riječi, o tom mjestu iz dostavljenih činjenica. Ne postavljaj ponovno isto pitanje. Ako odbije, kratko prihvati i pričekaj. Ako postavi drugo pitanje, odgovori na njega. Potpitanja nastavljaju isti razgovor. GPS određuje smiješ li tvrditi da je korisnik trenutačno uz mjesto.':'';
 send({type:'session.update',session:{type:'realtime',instructions:app.baseInstructions+'\nInformacija aplikacije o GPS-u: '+note+consent}});
}
function clearOffer(reason){
 if(!app.pendingOffer)return;
 record('offer_finished',{poi_id:app.pendingOffer.place.id,reason});app.pendingOffer=null;app.offerAnswered=false;
 const note=app.locationNote;app.locationNote=null;if(note)updateLocationContext(note);
}
function tick(){
 if(!app.active)return;
 if(app.dc?.readyState!=='open')return;
 const quality=gpsQuality();$('#gps-status').textContent=quality.label;
 if(app.pendingOffer&&!app.offerAnswered&&performance.now()-app.pendingOffer.at>60000)clearOffer('timeout');
 if(!quality.ok){
  const note=quality.code==='off_route'?'Korisnik je izvan odabrane rute. Nemoj tvrditi da je uz bilo koje mjesto. Precizni položaj nije proslijeđen.':
   'GPS trenutačno nije dovoljno pouzdan ili svjež. Nemoj tvrditi da je korisnik uz prethodno ili neko novo mjesto.';
  updateLocationContext(note);app.candidate=null;flushQuestion();return;
 }
 const fix=app.lastFix;
 if(app.pendingOffer&&!app.offerAnswered&&distance(fix.point,app.pendingOffer.place.coordinates)>app.pendingOffer.place.trigger_m+40)clearOffer('left_place');
 const nearby=app.places.filter(p=>(!app.demoPlace||p.id===app.demoPlace.id)&&Math.abs(p.along_m-quality.along)<130&&distance(fix.point,p.coordinates)<=p.trigger_m)
  .sort((a,b)=>distance(fix.point,a.coordinates)-distance(fix.point,b.coordinates)||a.along_m-b.along_m);
 updateLocationContext(app.demoPlace?'Ovo je jasno označena demonstracija mjesta '+JSON.stringify(app.demoPlace.name)+'. Mjesto je ručno odabrano; korisnikova stvarna lokacija nije uključena. Govori prirodno o samom mjestu, bez uvoda o aplikaciji ili prikazu. Stvarnu korisnikovu lokaciju ne pretpostavljaj.':nearby.length?'GPS potvrđuje blizinu mjesta '+JSON.stringify(nearby[0].name)+'. Ostala mjesta nisu potvrđena trenutačna lokacija.':
  'GPS potvrđuje da korisnik prati odabranu trasu, ali blizina određenog mjesta nije potvrđena.');
 flushQuestion();
 if(app.paused||app.pendingOffer||app.userTalking||app.awaitingAnswer||app.audioPlaying||app.responses.size)return;
 const options=nearby.filter(p=>!app.announced.has(p.id)&&p.story);
 const target=options[0];
 if(!target){app.candidate=null;return;}
 if(app.candidate?.id!==target.id)app.candidate={id:target.id,since:performance.now()};
 if(performance.now()-app.candidate.since<2000)return;
 app.pendingStory=target;
 if(app.responses.size||app.audioPlaying){
  if(!app.cancelPending){app.cancelPending=true;cancelSpeech();}
  return;
 }
 app.cancelPending=false;app.pendingStory=null;app.announced.add(target.id);
 app.pendingOffer={place:target,at:performance.now()};app.offerAnswered=false;
 const currentNote=app.locationNote;app.locationNote=null;updateLocationContext(currentNote);
 $('#status').textContent='Vodič nudi priču: '+target.name;
 const invitation=(app.demoPlace?'Na redu je ':'U blizini je ')+target.name+'. Želiš li čuti nešto više?';
 send({type:'response.create',response:{output_modalities:['audio'],metadata:{purpose:'offer',poi_id:target.id},
  instructions:'Izgovori doslovno samo ovu kratku ponudu na hrvatskom, zatim stani i pričekaj korisnikov odgovor. Priča počinje tek nakon njegova pristanka. Ponuda: '+JSON.stringify(invitation)}});
 record('offer_requested',{poi_id:target.id});
}
function realtimeEvent(e){
 record('realtime_server_event',{type:e.type});
 if(e.type==='input_audio_buffer.speech_started'){if(app.pendingOffer)app.offerAnswered=true;message('user','Slušam tvoje pitanje…',e.item_id);$('#microphone-state').textContent='🎙 Slušam tvoje pitanje…';app.userTalking=true;app.awaitingAnswer=true;$('#status').textContent='Slušam…';}
 if(e.type==='input_audio_buffer.speech_stopped'){$('#microphone-state').textContent='🎙 Mikrofon uključen · pripremam odgovor';app.userTalking=false;$('#status').textContent='Pripremam odgovor…';}
 if(e.type==='conversation.item.input_audio_transcription.completed')message('user',(e.transcript||'').trim(),e.item_id);
 if(e.type==='response.created'){app.latestPurpose=e.response.metadata?.purpose||'question';app.responses.set(e.response.id,e.response.metadata||{});if(app.paused&&['story','offer'].includes(app.latestPurpose)){cancelSpeech();return;}$('#status').textContent='Vodič govori. Možeš ga prekinuti.';}
 if(e.type==='output_audio_buffer.started'){app.audioPlaying=true;app.audioPurpose=app.latestPurpose||'question';if(app.paused&&['story','offer'].includes(app.audioPurpose))cancelSpeech();}
 if(e.type==='output_audio_buffer.stopped'||e.type==='output_audio_buffer.cleared'){app.audioPlaying=false;app.audioPurpose=null;app.cancelPending=false;flushQuestion();}
 if(e.type==='response.output_audio_transcript.delta'){const text=(app.transcripts.get(e.response_id)||'')+(e.delta||'');app.transcripts.set(e.response_id,text);message('assistant',text,e.response_id);}
 if(e.type==='response.output_audio_transcript.done')app.transcripts.set(e.response_id,e.transcript||'');
 if(e.type==='response.done'){
  const r=e.response,meta=app.responses.get(r.id)||r.metadata||{};
  const text=app.transcripts.get(r.id)||(r.output||[]).flatMap(x=>x.content||[]).map(c=>c.transcript||c.text||'').join(' ');
  if(text)message('assistant',text,r.id);$('#microphone-state').textContent='🎙 Mikrofon sluša · govori prirodno';
  app.responses.delete(r.id);app.transcripts.delete(r.id);app.cancelPending=false;
  if(!['story','offer'].includes(meta.purpose)){app.awaitingAnswer=false;if(app.offerAnswered)clearOffer('user_turn_answered');}
  $('#send').disabled=!app.active||!!app.pendingQuestion;flushQuestion();
  if(r.status==='failed'&&meta.purpose==='offer')clearOffer('offer_failed');
  if(r.status==='failed')$('#status').textContent='Odgovor nije uspio. Pokušaj ponovno postaviti pitanje.';
  else $('#status').textContent=app.pendingOffer&&!app.offerAnswered?'Reci „može” za priču ili postavi pitanje.':app.paused?'Automatske ponude su pauzirane. I dalje možeš postaviti pitanje.':'Vodič prati tvoju šetnju.';
 }
 if(e.type==='error'){
  app.awaitingAnswer=false;record('provider_error',{code:e.error?.code});
  $('#status').textContent='Glasovni servis javio je pogrešku. Završi šetnju i pokušaj ponovno.';
 }
}
function startGPS(epoch){
 return new Promise((resolve,reject)=>{
  if(app.demoPlace){app.lastFix={point:app.demoPlace.coordinates,accuracy:0,timestamp:Date.now()};app.marker?.setLatLng([app.demoPlace.coordinates[1],app.demoPlace.coordinates[0]]).setStyle({opacity:1,fillOpacity:1});$('#gps-status').textContent=gpsQuality().label;resolve();return;}
  if(!navigator.geolocation){reject(Error('gps_unsupported'));return;}
  let resolved=false;app.rejectGPS=reject;
  app.gpsWaitTimer=setTimeout(()=>{if(!resolved)reject(Error('gps_timeout'));},20000);
  app.watchId=navigator.geolocation.watchPosition(position=>{
   if(epoch!==app.epoch)return;
   const c=position.coords;
   if(!Number.isFinite(c.latitude)||!Number.isFinite(c.longitude)||!Number.isFinite(c.accuracy)||c.accuracy<0||!Number.isFinite(position.timestamp))return;
   app.lastFix={point:[c.longitude,c.latitude],accuracy:c.accuracy,timestamp:position.timestamp};
   app.marker?.setLatLng([c.latitude,c.longitude]).setStyle({opacity:1,fillOpacity:1});
   $('#locate').hidden=false;
   const quality=gpsQuality();$('#gps-status').textContent=quality.label;
   if(!resolved&&Math.abs(Date.now()-position.timestamp)<=15000){
    resolved=true;clearTimeout(app.gpsWaitTimer);app.rejectGPS=null;resolve();
   }
   tick();
  },error=>{
   if(epoch!==app.epoch)return;
   app.lastFix=null;app.candidate=null;
   if(!resolved){reject(Error(error.code===1?'gps_denied':'gps_unavailable'));return;}
   if(error.code===1)cleanup('Lokacija je isključena. Dopusti pristup lokaciji i ponovno pokreni vodiča.');
   else {$('#gps-status').textContent='GPS signal trenutačno nije dostupan. Priče čekaju novu lokaciju.';tick();}
  },{enableHighAccuracy:true,maximumAge:0,timeout:12000});
 });
}
function waitOpen(dc,epoch){
 return new Promise((resolve,reject)=>{
  if(dc.readyState==='open')return resolve();
  const timer=setTimeout(()=>reject(Error('connection_timeout')),15000);app.connectionTimer=timer;
  dc.addEventListener('open',()=>{clearTimeout(timer);epoch===app.epoch?resolve():reject(Error('cancelled'));},{once:true});
  dc.addEventListener('error',()=>{clearTimeout(timer);reject(Error('connection'));},{once:true});
 });
}
async function startRealtime(session,epoch){
 const pc=new RTCPeerConnection();app.pc=pc;
 const audio=document.createElement('audio');audio.autoplay=true;audio.hidden=true;document.body.append(audio);app.remoteAudio=audio;
 pc.ontrack=e=>{audio.srcObject=e.streams[0];audio.play().catch(()=>{$('#status').textContent='Dodirni kartu ako preglednik zaustavi reprodukciju zvuka.';});};
 pc.onconnectionstatechange=()=>{if(epoch===app.epoch&&app.active&&['failed','closed'].includes(pc.connectionState))cleanup('Glasovna veza je prekinuta. Ponovno pokreni vodiča.');};
 const dc=pc.createDataChannel('oai-events');app.dc=dc;
 dc.onmessage=e=>{if(epoch===app.epoch)realtimeEvent(JSON.parse(e.data));};
 const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});
 if(epoch!==app.epoch){stream.getTracks().forEach(t=>t.stop());throw Error('cancelled');}
 app.micStream=stream;pc.addTrack(stream.getAudioTracks()[0],stream);
 const offer=await pc.createOffer();await pc.setLocalDescription(offer);
 const response=await fetch('/voice/realtime/'+session.id,{method:'POST',headers:{'Content-Type':'application/sdp'},body:offer.sdp,signal:app.abort.signal});
 if(!response.ok)throw Error('realtime');
 const answer=await response.text();
 if(epoch!==app.epoch)throw Error('cancelled');
 await pc.setRemoteDescription({type:'answer',sdp:answer});await waitOpen(dc,epoch);
}
async function beginSession(demoPlace=null){
 if(!app.ready||app.starting||app.active)return;
 app.demoPlace=demoPlace;$('#mode-badge').textContent=demoPlace?'Demonstracija odabranog mjesta':'Tvoja ruta · GPS uživo';$('#mode-note').textContent=demoPlace?'Probni susret: '+demoPlace.name+' · stvarna lokacija je isključena.':'GPS prati odabranu šetnju.';const epoch=++app.epoch;app.starting=true;app.paused=false;app.announced.clear();app.pendingOffer=null;app.offerAnswered=false;app.abort=new AbortController();$('#start').textContent='Čekam GPS…';sessionUI();
 try{
  await startGPS(epoch);if(epoch!==app.epoch)return;
  $('#start').textContent='Povezujem vodiča…';
  const response=await fetch('/voice/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({route:app.packet}),signal:app.abort.signal});
  if(!response.ok){const data=await response.json();const error=Error('session');error.userMessage=data.detail||'Glasovna sesija nije dostupna.';throw error;}
  const session=await response.json();if(epoch!==app.epoch)return;
  app.baseInstructions=session.instructions;await startRealtime(session,epoch);if(epoch!==app.epoch)return;
  app.starting=false;app.active=true;app.tick=setInterval(tick,1000);
  app.endTimer=setTimeout(()=>cleanup('Glasovna sesija je završena nakon 55 minuta. Ponovno pokreni vodiča za nastavak iste rute.'),55*60000);
  $('#status').textContent='Vodič je spreman. Kreni po ruti ili postavi pitanje.';$('#stop').textContent='Pauziraj ponude';sessionUI();tick();
 }catch(error){
  if(epoch!==app.epoch)return;
  record('start_error',{message:String(error.message)});
  const messages={gps_denied:'Dopusti lokaciju u pregledniku pa ponovno pokreni vodiča.',
   gps_timeout:'Nismo dobili svježu GPS lokaciju. Pričekaj na otvorenom i pokušaj ponovno.',
   gps_unavailable:'GPS lokacija nije dostupna. Uključi lokaciju na uređaju i pokušaj ponovno.',
   gps_unsupported:'Ovaj preglednik ne podržava lokaciju.',
   NotAllowedError:'Dopusti mikrofon u pregledniku pa ponovno pokreni vodiča.'};
  cleanup(messages[error.message]||messages[error.name]||error.userMessage||'Vodič se nije povezao. Provjeri mikrofon i internetsku vezu pa pokušaj ponovno.');
 }
}
$('#start').onclick=()=>beginSession();
$('#start-demo').onclick=()=>beginSession(app.places.find(p=>p.id===$('#demo-place').value));
$('#end').onclick=()=>cleanup('Šetnja je završena.');
$('#stop').onclick=()=>{app.paused=!app.paused;if(app.paused)cancelSpeech();$('#stop').textContent=app.paused?'Nastavi ponude':'Pauziraj ponude';$('#status').textContent=app.paused?'Automatske ponude su pauzirane. I dalje možeš postaviti pitanje.':'Automatske ponude su uključene.';};
$('#locate').onclick=()=>{if(app.lastFix)app.map.setView([app.lastFix.point[1],app.lastFix.point[0]],17);};
$('#map').onclick=()=>app.remoteAudio?.play().catch(()=>{});
$('#question-form').onsubmit=e=>{
 e.preventDefault();const text=$('#question').value.trim();if(!text||!app.active)return;
 if(app.pendingOffer)app.offerAnswered=true;app.pendingQuestion=text;app.awaitingAnswer=true;$('#question').value='';$('#send').disabled=true;
 $('#status').textContent='Pripremam odgovor…';cancelSpeech();flushQuestion();
};
window.addEventListener('pagehide',()=>cleanup('Šetnja je završena.'));
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&app.active)tick();});
async function init(){
 try{
  app.exampleOnly=new URLSearchParams(location.search).get('demo')==='paviljon';
  const raw=app.exampleOnly?JSON.stringify(await fetch('/voice/demo-route.json').then(r=>{if(!r.ok)throw Error('demo_unavailable');return r.json()})):sessionStorage.getItem(KEY);
  if(!raw)throw Error('no_route');
  const packet=JSON.parse(raw);app.packet=packet;
  const [status,response]=await Promise.all([fetch('/voice/status').then(r=>r.json()),fetch('/voice/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({route:packet})})]);
  if(!response.ok)throw Error('no_route');
  const context=await response.json();app.context=context;app.route=context.geometry.coordinates;app.places=context.places;
  $('#route-name').textContent=context.name;$('#route-summary').textContent=(context.distance_m/1000).toLocaleString('hr-HR',{maximumFractionDigits:1})+' km · '+context.places.length+' mjesta s opisom';
  $('#map-section').hidden=false;app.map=L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors'}).addTo(app.map);
  app.line=L.polyline(app.route.map(p=>[p[1],p[0]]),{color:'#405c42',weight:5}).addTo(app.map);
  app.map.fitBounds(app.line.getBounds(),{padding:[26,26],maxZoom:16,animate:false});
  app.marker=L.circleMarker([app.route[0][1],app.route[0][0]],{radius:9,color:'#194e3d',fillColor:'#b8cc85',fillOpacity:0,opacity:0}).addTo(app.map);
  app.places.forEach((p,i)=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.name;$('#demo-place').append(option);L.circleMarker([p.coordinates[1],p.coordinates[0]],{radius:6,color:'#a25d40',fillColor:'#fff',fillOpacity:1,weight:2}).addTo(app.map).bindTooltip((i+1)+'. '+p.name);
   const li=document.createElement('li'),a=document.createElement('a');a.textContent=p.name;a.href=p.source_url;a.target='_blank';a.rel='noreferrer';li.append(a);$('#place-list').append(li);});
  const defaultDemo=app.places.find(p=>p.name.toLowerCase().includes('umjetnički paviljon'));if(defaultDemo)$('#demo-place').value=defaultDemo.id;
  if(!app.places.length)$('#place-list').textContent='Uz ovu rutu nemamo dovoljno provjerenih opisa za automatske priče.';
  if(app.exampleOnly){$('#demo-options').open=true;$('#mode-badge').textContent='Pripremljeni primjer · demonstracija';$('#status').textContent='Demonstracija koristi mikrofon; stvarna lokacija ostaje isključena.';$('.start-help p').textContent='Pokreni demonstraciju i dopusti mikrofon. Vodič će prvi ponuditi priču; reci „može” za nastavak ili postavi pitanje.';}
  app.ready=!!status.configured;$('#availability').textContent=app.ready?'Odabrana ruta je spremna.':'Glasovni servis trenutačno nije dostupan.';sessionUI();
 }catch(e){
  app.ready=false;document.body.classList.add('no-route');$('#mode-badge').textContent='Odaberi šetnju';$('#start').hidden=true;$('#empty-route').hidden=false;$('#availability').textContent='';$('#status').textContent='Vodič se otvara iz odabrane rute u planeru.';
 }
}
init();
})();