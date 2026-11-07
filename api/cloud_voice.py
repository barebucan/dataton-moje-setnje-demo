"""Stateless WebRTC signalling. Tour control stays in the visitor's browser."""
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse,Response
from pathlib import Path
from collections import OrderedDict,deque
import os,json,time,secrets,base64,hmac,hashlib,re
from backend.voice import realtime_instructions
from backend.voice_provider import OpenAIProvider,VoiceUnavailable
WINDOWS=OrderedDict();USED=OrderedDict()
def voice_router(base):
 router=APIRouter(prefix='/voice')
 places=json.loads((base/'data/cloud-places.json').read_text())
 def key():return os.environ.get('OPENAI_API_KEY','')
 def sign(payload):
  body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
  return body+'.'+hmac.new(key().encode(),body.encode(),hashlib.sha256).hexdigest()
 def verify(token):
  body,sig=token.rsplit('.',1)
  if not hmac.compare_digest(sig,hmac.new(key().encode(),body.encode(),hashlib.sha256).hexdigest()):raise ValueError()
  return json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
 def valid_origin(request):
  origin=request.headers.get('origin')
  return not origin or origin=='https://'+request.headers.get('host','')
 def ip(request):return request.headers.get('x-forwarded-for','').split(',')[0].strip()
 def ip_hash(request):return hmac.new(key().encode(),ip(request).encode(),hashlib.sha256).hexdigest()[:20]
 @router.get('/status')
 async def status():
  p=OpenAIProvider()
  return {'configured':bool(key()),'provider':p.name,'models':p.models,'voice':p.voice,'transport':'webrtc-browser-control','live_validated':False}
 @router.post('/sessions')
 async def session(request:Request):
  if not key():return JSONResponse({'detail':'Glasovni API još nije povezan s javnom verzijom.'},503)
  if not valid_origin(request):return JSONResponse({'detail':'Neispravan izvor zahtjeva.'},403)
  now=time.time();who=ip_hash(request)
  attempts=WINDOWS.setdefault(who,deque())
  while attempts and attempts[0]<now-3600:attempts.popleft()
  if len(attempts)>=4:return JSONResponse({'detail':'Dosegnut je satni limit pokretanja vodiča.'},429)
  attempts.append(now);WINDOWS.move_to_end(who)
  while len(WINDOWS)>2048:WINDOWS.popitem(last=False)
  token=sign({'t':int(now),'n':secrets.token_hex(12),'ip':who})
  return {'id':token,'mode':'realtime','instructions':realtime_instructions(places),'realtime_model':OpenAIProvider().models['realtime']}
 @router.post('/realtime/{sid}')
 async def realtime(sid:str,request:Request):
  if not key():return JSONResponse({'detail':'Glasovni API još nije povezan.'},503)
  if not valid_origin(request):return JSONResponse({'detail':'Neispravan izvor zahtjeva.'},403)
  try:
   info=verify(sid)
   if info['ip']!=ip_hash(request) or not 0<=time.time()-info['t']<=120 or sid in USED:raise ValueError()
   raw=await request.body()
   if not 0<len(raw)<=65536:raise ValueError()
   sdp=raw.decode()
   if not sdp.startswith('v=0'):raise ValueError()
  except (ValueError,KeyError,UnicodeDecodeError):return JSONResponse({'detail':'Pokrenite novu probu.'},403)
  USED[sid]=time.time()
  while len(USED)>4096:USED.popitem(last=False)
  try:answer,location=await OpenAIProvider().realtime_call(sdp,realtime_instructions(places))
  except VoiceUnavailable:return JSONResponse({'detail':'Glasovna veza trenutačno nije dostupna.'},503)
  return Response(answer,media_type='application/sdp',headers={'Cache-Control':'no-store'})
 return router
