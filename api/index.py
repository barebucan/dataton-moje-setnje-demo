from pathlib import Path
import os,sys,asyncio,subprocess,shutil,threading,time,json
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import Response,JSONResponse
BASE=Path(os.environ.get('WALK_RUNTIME',str(Path(__file__).resolve().parent/'runtime')))
sys.path.insert(0,str(BASE))
os.environ['LAGANO_GH_URL']='http://127.0.0.1:8314'
from backend.app import app as planner
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
LOCK=threading.Lock()
def graph_ready():
 try:
  import urllib.request
  with urllib.request.urlopen('http://127.0.0.1:8314/info',timeout=1) as r:return r.status==200
 except Exception:return False
def ensure_graph():
 with LOCK:
  if graph_ready():return
  cache=Path('/tmp/moje-setnje-graph')
  if not cache.exists():shutil.copytree(BASE/'data/city-graph-cache',cache)
  config=(BASE/'backend/graphhopper.yml').read_text().replace('/home/bartol_bucan/Roko/Dataton/docs/teme/A3_adrese/setnje/wordpress',str(BASE)).replace(str(BASE/'data/city-graph-cache'),str(cache))
  path=Path('/tmp/moje-setnje-graphhopper.yml');path.write_text(config)
  with open('/tmp/moje-setnje-graphhopper.log','ab') as log:
   subprocess.Popen([str(BASE/'vendor/jre/bin/java'),'-Xms64m','-Xmx700m','-jar',str(BASE/'vendor/graphhopper.jar'),'server',str(path)],cwd='/tmp',stdout=log,stderr=log)
  for _ in range(150):
   if graph_ready():return
   time.sleep(.2)
  raise RuntimeError('Pješački motor nije spreman.')
@app.api_route('/wp-json/lagano/v1/{endpoint}',methods=['GET','POST'])
async def walk_api(endpoint:str,request:Request):
 methods={'search':'GET','health':'GET','plan':'POST','gpx':'POST'}
 if methods.get(endpoint)!=request.method:return JSONResponse({'detail':'Nije dostupno.'},404)
 if endpoint in ['plan','health']:
  try:await asyncio.to_thread(ensure_graph)
  except Exception:return JSONResponse({'detail':'Pješački motor se još priprema. Pokušaj ponovno.'},503)
 body=await request.body()
 if len(body)>900000:return JSONResponse({'detail':'Prevelik zahtjev.'},413)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=planner),base_url='http://planner',timeout=70) as client:
  result=await client.request(request.method,'/'+endpoint,params=list(request.query_params.multi_items()),content=body,headers={'Content-Type':'application/json'})
 return Response(result.content,status_code=result.status_code,media_type='application/json',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
@app.post('/wp-admin/admin-post.php')
async def gpx_export(request:Request):
 from urllib.parse import parse_qs
 raw=await request.body()
 if len(raw)>900000:return JSONResponse({'detail':'Prevelik zahtjev.'},413)
 try:
  data=parse_qs(raw.decode())
  if data.get('action')!=['lagano_gpx']:raise ValueError()
  payload={'name':data.get('name',['Moja šetnja'])[0],'coordinates':json.loads(data.get('coordinates',['null'])[0])}
 except (ValueError,TypeError):return JSONResponse({'detail':'Neispravan zahtjev.'},400)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=planner),base_url='http://planner') as client:
  result=await client.post('/gpx',json=payload)
 if result.status_code!=200:return Response(result.content,status_code=result.status_code,media_type='application/json')
 return Response(result.json()['gpx'],media_type='application/gpx+xml',headers={'Content-Disposition':'attachment; filename="moje-setnje.gpx"','X-Content-Type-Options':'nosniff'})
