"""Stateless route-bound WebRTC signalling; no raw GPS fixes are sent here."""
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse,Response
from collections import OrderedDict,deque
import os,json,time,secrets,base64,hmac,hashlib
from backend.voice_provider import OpenAIProvider,VoiceUnavailable
from route_context import RouteContext,InvalidRoute,instructions
WINDOWS=OrderedDict();USED=OrderedDict()
def voice_router(base):
 router=APIRouter(prefix='/voice');context=RouteContext(base)
 def key():return os.environ.get('OPENAI_API_KEY','')
 def sign(payload):
  body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
  return body+'.'+hmac.new(key().encode(),body.encode(),hashlib.sha256).hexdigest()
 def verify(token):
  if len(token)>4096:raise ValueError()
  body,sig=token.rsplit('.',1)
  if not hmac.compare_digest(sig,hmac.new(key().encode(),body.encode(),hashlib.sha256).hexdigest()):raise ValueError()
  return json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
 def valid_origin(request):
  origin=request.headers.get('origin')
  return not origin or origin=='https://'+request.headers.get('host','')
 def ip_hash(request):
  who=request.headers.get('x-forwarded-for','').split(',')[0].strip()
  return hmac.new(key().encode(),who.encode(),hashlib.sha256).hexdigest()[:20]
 async def packet(request):
  if not valid_origin(request):raise InvalidRoute('Neispravan izvor zahtjeva.')
  raw=await request.body()
  if not 2<=len(raw)<=900000:raise InvalidRoute('Ruta je prevelika.')
  return context.prepare(json.loads(raw).get('route'))
 @router.get('/status')
 async def status():
  p=OpenAIProvider()
  return {'configured':bool(key()),'provider':p.name,'transport':'webrtc-selected-route-gps','version':'route-gps-v1'}
 @router.post('/context')
 async def route_context(request:Request):
  if not valid_origin(request):return JSONResponse({'detail':'Neispravan izvor zahtjeva.'},403)
  try:return await packet(request)
  except (ValueError,TypeError,KeyError,AttributeError):return JSONResponse({'detail':'Odabrana ruta nije valjana. Vrati se u planer i odaberi šetnju.'},422)
 @router.post('/sessions')
 async def session(request:Request):
  if not key():return JSONResponse({'detail':'Glasovni servis trenutačno nije povezan.'},503)
  if not valid_origin(request):return JSONResponse({'detail':'Neispravan izvor zahtjeva.'},403)
  try:route=await packet(request)
  except (ValueError,TypeError,KeyError,AttributeError):return JSONResponse({'detail':'Odaberi valjanu rutu u planeru.'},422)
  now=time.time();who=ip_hash(request);attempts=WINDOWS.setdefault(who,deque())
  while attempts and attempts[0]<now-3600:attempts.popleft()
  if len(attempts)>=4:return JSONResponse({'detail':'Dosegnut je satni limit pokretanja vodiča. Pokušaj kasnije.'},429)
  attempts.append(now);WINDOWS.move_to_end(who)
  while len(WINDOWS)>2048:WINDOWS.popitem(last=False)
  ids=[p['id'] for p in route['places']]
  token=sign({'t':int(now),'n':secrets.token_hex(12),'ip':who,'ids':ids,'rid':route['route_id']})
  return JSONResponse({'id':token,'route_id':route['route_id'],'instructions':instructions(route['places']),'realtime_model':OpenAIProvider().models['realtime']},headers={'Cache-Control':'no-store'})
 @router.post('/realtime/{sid}')
 async def realtime(sid:str,request:Request):
  if not key():return JSONResponse({'detail':'Glasovni servis nije povezan.'},503)
  if not valid_origin(request):return JSONResponse({'detail':'Neispravan izvor zahtjeva.'},403)
  try:
   info=verify(sid)
   if info['ip']!=ip_hash(request) or not 0<=time.time()-info['t']<=120 or sid in USED:raise ValueError()
   places=context.selected(info['ids'])
   raw=await request.body()
   if not 0<len(raw)<=65536:raise ValueError()
   sdp=raw.decode()
   if not sdp.startswith('v=0'):raise ValueError()
  except (ValueError,KeyError,UnicodeDecodeError,TypeError):return JSONResponse({'detail':'Pokrenite novu glasovnu šetnju.'},403)
  USED[sid]=time.time()
  while len(USED)>4096:USED.popitem(last=False)
  try:answer,_=await OpenAIProvider().realtime_call(sdp,instructions(places))
  except VoiceUnavailable:return JSONResponse({'detail':'Glasovna veza trenutačno nije dostupna. Pokušaj ponovno.'},503)
  return Response(answer,media_type='application/sdp',headers={'Cache-Control':'no-store'})
 return router
