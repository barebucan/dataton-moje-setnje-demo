from pathlib import Path
import os,asyncio,json,math,time,csv,io,unicodedata,re,hashlib,xml.etree.ElementTree as ET
from typing import Literal
from collections import Counter,deque,OrderedDict
from fastapi import FastAPI,HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,Field,field_validator
import httpx
from shapely.geometry import shape,mapping,LineString,Point,Polygon,box
from shapely.ops import unary_union,transform
from shapely.strtree import STRtree
from pyproj import Transformer
from backend.loop_quality import assess_backtracking,loop_backtracking_ok,select_loop_candidates

BASE=Path(__file__).resolve().parents[1];DATA=BASE/'data/city-processed'
SCENE={k:json.loads((DATA/(k+'.geojson')).read_text()) for k in ['parks','pedestrian','poi']}
META=json.loads((DATA/'meta.json').read_text());SEARCH=json.loads((DATA/'search.json').read_text())
if (DATA/'landmarks.geojson').exists():SCENE['poi']=json.loads((DATA/'landmarks.geojson').read_text())
if (BASE/'data/curated-landmarks.json').exists():
 overrides=json.loads((BASE/'data/curated-landmarks.json').read_text())['features']
 for feature in SCENE['poi']['features']:
  feature['properties'].update(overrides.get(feature['properties']['id'],{}))
META['sources'][-1]['name']='Putovi, zelenilo i zanimljivosti'
SCENE['meta']=META
PARK_SOURCE=SCENE['parks']
if (DATA/'parks-display.geojson').exists():SCENE['parks']=json.loads((DATA/'parks-display.geojson').read_text())
TO_M=Transformer.from_crs(4326,3765,always_xy=True).transform
FROM_M=Transformer.from_crs(3765,4326,always_xy=True).transform
PED=unary_union([shape(f['geometry']) for f in SCENE['pedestrian']['features']]);GREEN=unary_union([shape(f['geometry']) for f in PARK_SOURCE['features']])
PED_M=transform(TO_M,PED).buffer(2);GREEN_M=transform(TO_M,GREEN).buffer(12)
GREEN_PARTS=list(getattr(GREEN_M,'geoms',[GREEN_M]));GREEN_TREE=STRtree(GREEN_PARTS)
POI=[(f,transform(TO_M,shape(f['geometry']))) for f in SCENE['poi']['features']]
POI_TREE=STRtree([p for f,p in POI])
ROUTING_ANCHORS=json.loads((DATA/'routing-anchors.json').read_text()) if (DATA/'routing-anchors.json').exists() else []
if ROUTING_ANCHORS:
 xs,ys=TO_M([p['coordinates'][0] for p in ROUTING_ANCHORS],[p['coordinates'][1] for p in ROUTING_ANCHORS])
 ROUTING_ANCHOR_POINTS=[Point(x,y) for x,y in zip(xs,ys)];ROUTING_ANCHOR_TREE=STRtree(ROUTING_ANCHOR_POINTS)
else:ROUTING_ANCHOR_POINTS=[];ROUTING_ANCHOR_TREE=None

GREEN_ROUTE=transform(FROM_M,GREEN_M);PED_ROUTE=transform(FROM_M,PED_M)
POOL_CACHE=OrderedDict();POOL_LOCKS={};GH_LIMIT=asyncio.Semaphore(6)
GH=os.environ.get('LAGANO_GH_URL','http://127.0.0.1:8314');LIMIT=asyncio.Semaphore(2);REQUEST_TIMES=deque(maxlen=100)
app=FastAPI(title='Lagano walking planner',docs_url=None,redoc_url=None)
from backend.voice import make_router
app.include_router(make_router(SCENE['poi']))
@app.exception_handler(RequestValidationError)
async def invalid_request(req,exc):
 outside=any('Outside pilot' in str(e.get('ctx',{}).get('error','')) for e in exc.errors())
 return JSONResponse(status_code=422,content={'detail':'Točka je izvan zagrebačkog područja koje pokrivamo. Odaberi mjesto na karti Zagreba.' if outside else 'Provjeri polazište, odredište i odabrano trajanje pa pokušaj ponovno.'})
class Plan(BaseModel):
 mode:Literal['loop','destination']='loop'
 start:list[float]=Field(min_length=2,max_length=2)
 end:list[float]|None=None
 duration:int=Field(default=30,ge=15,le=75)
 preference:Literal['balanced','green','pedestrian','culture']='balanced'
 @field_validator('start','end')
 @classmethod
 def valid_coordinates(cls,v):
  if v is None:return v
  if len(v)!=2 or not all(math.isfinite(n) for n in v):raise ValueError('Invalid coordinates')
  if not(META['bounds'][0]<=v[0]<=META['bounds'][2] and META['bounds'][1]<=v[1]<=META['bounds'][3]):raise ValueError('Outside pilot')
  return v
class Gpx(BaseModel):
 name:str=Field(default='Moja šetnja',max_length=120)
 coordinates:list[list[float]]=Field(min_length=2,max_length=12000)

def normalize(s):
 base=''.join(c for c in unicodedata.normalize('NFD',s.lower().replace('đ','d')) if unicodedata.category(c)!='Mn')
 return ' '.join(re.sub(r'[^\w\s]',' ',base).split())
ALIASES={'britanac':'britanac britanski trg','hnk':'hnk hrvatsko narodno kazaliste','tomislavac':'tomislavac kralja tomislava','stross':'stross strossmayerovo setaliste','ban-jelacic':'glavni trg jelacic plac'}
SEARCH_INDEX=[(p,normalize(p['name']+' '+p.get('subtitle','')+' '+ALIASES.get(p['id'],p['id']) if p['kind']=='landmark' else p['name']+' '+p.get('subtitle',''))) for p in SEARCH]
def hav(a,b):
 x,y,u,v=map(math.radians,[*a,*b]);d=math.sin((v-y)/2)**2+math.cos(y)*math.cos(v)*math.sin((u-x)/2)**2;return 6371008.8*2*math.atan2(math.sqrt(d),math.sqrt(max(0,1-d)))
@app.get('/scene')
def scene():return SCENE
@app.get('/search')
def search(q:str=''):
 q=normalize(q.strip()[:120]);words=q.split()
 if not q:return [p for p in SEARCH if p['kind']=='landmark'][:8]
 def score(p):
  n=normalize(p['name']);return (0 if n==q or (p['kind']=='landmark' and normalize(p['id'])==q) else 1 if n.startswith(q) else 2,0 if p['kind']=='landmark' else 1,len(n))
 matches=[p for p,n in SEARCH_INDEX if all(w in n for w in words)]
 return sorted(matches,key=score)[:8]
@app.get('/health')
async def health():
 try:
  async with httpx.AsyncClient(timeout=3) as client:r=await client.get(GH+'/info');r.raise_for_status()
  return {'ok':True,'engine':'GraphHopper 11.1','wordpress_proxy':True,'poi':len(POI),'parks':len(PARK_SOURCE['features']),'scope':META['scope'],'bounds':META['bounds']}
 except Exception:return JSONResponse(status_code=503,content={'ok':False,'detail':'Pješački motor se trenutačno priprema.'})

def custom_model(pref,points=None,distance=None):
 points=points or [[15.97887,45.80935]]
 radius=max(900,(distance or 3000)*.8+250) if len(points)==1 else max(700,hav(points[0],points[-1])*.12)
 midlat=sum(p[1] for p in points)/len(points);dy=radius/111320;dx=radius/(111320*math.cos(math.radians(midlat)))
 window=box(min(p[0] for p in points)-dx,min(p[1] for p in points)-dy,max(p[0] for p in points)+dx,max(p[1] for p in points)+dy)
 features=[];keys={'ped':[],'green':[]}
 def polygons(g):
  if g.geom_type=='Polygon':return [g]
  return [p for child in getattr(g,'geoms',[]) for p in polygons(child)]
 for typ,geom in [('ped',PED_ROUTE),('green',GREEN_ROUTE)]:
  for i,p in enumerate(polygons(geom.intersection(window))):
   if p.is_empty:continue
   pid=typ+str(i);keys[typ].append('in_'+pid);features.append(dict(type='Feature',id=pid,properties={},geometry=mapping(p.simplify(.000035,preserve_topology=True))))
 ped=' || '.join(keys['ped']) or 'false';green=' || '.join(keys['green']) or 'false'
 walkway='road_class == FOOTWAY || road_class == PEDESTRIAN || road_class == PATH || road_class == STEPS || road_class == CORRIDOR'
 if pref=='green':rules=[{'if':'!('+green+')','multiply_by':'.28'}]
 elif pref=='pedestrian':rules=[{'if':'!('+walkway+') && !('+ped+')','multiply_by':'.30'}]
 else:rules=[{'if':'!(('+ped+') || ('+green+') || ('+walkway+'))','multiply_by':'.82'}]
 return {'priority':rules,'areas':{'type':'FeatureCollection','features':features},'distance_influence':90}
async def gh_route(points,pref='plain',extra=None):
 payload={'profile':'foot','points':points,'points_encoded':False,'instructions':True,'locale':'hr','snap_preventions':['ferry'],'ch.disable':True,'details':['road_class']}
 if pref!='plain':payload['custom_model']=custom_model(pref,points,(extra or {}).get('round_trip.distance'))
 if extra:payload.update(extra)
 try:
  async with GH_LIMIT:
   async with httpx.AsyncClient(timeout=18) as client:r=await client.post(GH+'/route',json=payload)
  if r.status_code!=200:return None
  d=r.json();p=d.get('paths',[None])[0]
  if not p or len(p.get('points',{}).get('coordinates',[]))<2:return None
  snaps=p.get('snapped_waypoints',{}).get('coordinates',[])
  if snaps and hav(points[0],snaps[0][:2])>100:return None
  if len(points)>1 and len(snaps)==len(points) and any(hav(a,b[:2])>100 for a,b in zip(points,snaps)):return None
  return p
 except (httpx.HTTPError,ValueError,IndexError):return None

def indexed_intersection_length(line,parts,tree):
 return sum(line.intersection(parts[int(index)]).length for index in tree.query(line,predicate='intersects'))

def analyze_path(path,start):
 coords=[c[:2] for c in path['points']['coordinates']];line=transform(TO_M,LineString(coords));distance=float(path['distance']);length=line.length
 ped_line=line.intersection(PED_M);ped=min(distance,ped_line.length);green=min(distance,indexed_intersection_length(line,GREEN_PARTS,GREEN_TREE))
 walking=[ped_line]
 for a,b,kind in path.get('details',{}).get('road_class',[]):
  if str(kind).lower() in ['footway','pedestrian','path','steps','corridor'] and b>a:walking.append(transform(TO_M,LineString(coords[a:b+1])))
 walking_m=min(distance,unary_union(walking).length);backtracking=assess_backtracking(line)
 segments=[];total=0
 for a,b in zip(coords,coords[1:]):
  da=hav(a,b)
  if da<1:continue
  key=tuple(sorted([(round(a[0],5),round(a[1],5)),(round(b[0],5),round(b[1],5))]));segments.append((key,da));total+=da
 seen=set();repeated=0
 for key,d in segments:
  if key in seen:repeated+=d
  seen.add(key)
 unique_length=unary_union(line).length;repeat_ratio=max(repeated/max(1,total),1-unique_length/max(1,length))
 closed=hav(coords[0],coords[-1])<25;compactness=0
 if closed:
  polygon=Polygon(list(line.coords));area=polygon.buffer(0).area;compactness=min(1,4*math.pi*area/max(1,length*length))
 stops=[]
 for idx in POI_TREE.query(line.buffer(65)):
  f,p=POI[int(idx)];offset=line.distance(p);along=line.project(p);prop=f['properties']
  if offset>60 or along<25 or along>length-25:continue
  stops.append({'id':prop['id'],'name':prop['name'],'kind':prop.get('kind','culture'),'category_label':prop.get('category_label',prop.get('category','Zanimljivost')),'description':prop.get('description','Lokacija evidentirana u javnom izvoru.'),'address':prop.get('address',''),'source_label':prop.get('source','Grad Zagreb'),'coordinates':f['geometry']['coordinates'],'along_m':round(along),'offset_m':round(offset),'source_url':prop['source_url'],'interest':prop.get('interest',2),'website':prop.get('website'),'source_refs':prop.get('source_refs',[]),'portal_backed':bool(prop.get('portal_backed')),'portal_culture':any(r.get('dataset_id')=='geoportal-kultura' for r in prop.get('source_refs',[]))})
 stops.sort(key=lambda p:-(p['interest']*10-p['offset_m']*.05));chosen=[]
 for p in stops:
  if all(hav(p['coordinates'],c['coordinates'])>55 for c in chosen):chosen.append(p)
  if len(chosen)>=8:break
 chosen.sort(key=lambda p:p['along_m']);snapped=path.get('snapped_waypoints',{}).get('coordinates',[start])[0]
 return {'distance_m':round(distance,1),'duration_min':round(path['time']/60000,1),'geometry':{'type':'LineString','coordinates':coords},'metrics':{'pedestrian_m':round(ped),'green_m':round(green),'walking_m':round(walking_m),'pedestrian_pct':round(100*ped/max(1,distance)),'green_pct':round(100*green/max(1,distance)),'walking_pct':round(100*walking_m/max(1,distance)),'repeat_ratio':round(repeat_ratio,3),**backtracking,'compactness':round(compactness,3),'sight_score':sum(p['interest'] for p in chosen),'portal_stops':sum(bool(p.get('portal_backed')) for p in chosen),'portal_culture_score':sum(p['interest'] for p in chosen if p.get('portal_culture')),'culture_score':sum(p['interest']*(2 if p.get('portal_culture') else 1) for p in chosen)},'stops':chosen,'instructions':[{k:i[k] for k in ['text','distance','time','sign'] if k in i} for i in path.get('instructions',[])],'snap_m':round(hav(start,snapped[:2]),1),'_line':line,'_segments':seen}

def same_route(a,b):
 s1=a['_segments'];s2=b['_segments']
 if len(s1&s2)/max(1,min(len(s1),len(s2)))>.82:return True
 short,long=(a,b) if a['_line'].length<=b['_line'].length else (b,a)
 if '_choice_corridor' not in long:long['_choice_corridor']=long['_line'].buffer(8)
 return short['_line'].intersection(long['_choice_corridor']).length/max(1,short['_line'].length)>.84

def distinct_routes(routes,limit=5):
 selected=[]
 for r in routes:
  if all(not same_route(r,u) for u in selected):selected.append(r)
  if len(selected)>=limit:break
 return selected

async def bounded_choice_paths(requests,seconds=6):
 tasks=[asyncio.create_task(r) for r in requests]
 if not tasks:return []
 done,pending=await asyncio.wait(tasks,timeout=seconds)
 for task in pending:task.cancel()
 if pending:await asyncio.gather(*pending,return_exceptions=True)
 return [task.result() for task in tasks if task in done and not task.cancelled() and task.exception() is None]

def choice_name(route,selected,used):
 counts={stop['id']:sum(any(p['id']==stop['id'] for p in r['stops']) for r in selected) for stop in route['stops']}
 stops=sorted(route['stops'],key=lambda p:(counts[p['id']],-p['interest'],p['offset_m']))
 for stop in stops:
  name=stop['name']
  if name not in used:
   used.add(name);return 'Usput: '+name
 line=route['_line'];x,y=line.coords[0];centre=line.centroid;dx=centre.x-x;dy=centre.y-y
 direction=('Sjever' if dy>0 else 'Jug') if abs(dy)>abs(dx)*1.7 else ('Istok' if dx>0 else 'Zapad') if abs(dx)>abs(dy)*1.7 else ('Sjevero' if dy>0 else 'Jugo')+('istok' if dx>0 else 'zapad')
 return direction+' · '+str(round(route['duration_min']))+' min'

def describe(route,mode,target=None):
 m=route['metrics'];phrases=[]
 if m.get('walking_m',m['pedestrian_m'])>120:phrases.append('uključuje pješačke dionice')
 if m['green_m']>120:phrases.append('vodi uz zelene površine')
 if not phrases:phrases.append('povezuje gradske ulice u jednostavan obilazak' if mode=='loop' else 'povezuje polazište i odredište izravnijim putem')
 text='Ruta '+(' i '.join(phrases))+'.'
 if route['stops']:text+=' Uz put je '+str(len(route['stops']))+' označenih lokacija.'
 if mode=='loop' and target and abs(route['duration_min']-target)>6:text+=' Traje približno '+str(round(route['duration_min']))+' minuta.'
 return text

def public_route(r,name,tag,mode,target=None):
 return {k:v for k,v in dict(r,id=hashlib.sha256(json.dumps(r['geometry'],sort_keys=True).encode()).hexdigest()[:14],name=name,tag=tag,description=describe(r,mode,target)).items() if not k.startswith('_')}

def quality(r,pref):
 m=r['metrics']
 if pref=='green':return m['green_pct']*.95+m['walking_pct']*.08
 if pref=='pedestrian':return m['walking_pct']*.95+m['pedestrian_pct']*.15+m['green_pct']*.06
 if pref=='culture':return m['sight_score']*1.6+m['portal_culture_score']*.9+len(r['stops'])*2+m['walking_pct']*.08
 return m['green_pct']*.30+m['walking_pct']*.25+min(20,m['sight_score'])*.25

def route_rank(r,req,base_distance=None):
 m=r['metrics']
 if req.mode=='loop':
  time_cost=abs(r['duration_min']-req.duration)*2+max(0,r['duration_min']-req.duration-1)*6
  shape_cost=m['repeat_ratio']*100+m['near_retrace_ratio']*60+max(0,.12-m['compactness'])*70
 else:
  time_cost=max(0,r['distance_m']/max(1,base_distance)-1)*70;shape_cost=m['repeat_ratio']*80
 return time_cost+shape_cost-quality(r,req.preference)*.8

def anchors_near(start,radius):
 origin=transform(TO_M,Point(start));candidates=[]
 for idx in POI_TREE.query(origin.buffer(radius)):
  f,p=POI[int(idx)];d=p.distance(origin);pr=f['properties']
  if 120<d<radius and pr.get('interest',2)>=2 and pr.get('kind')!='water':candidates.append((f,p,d))
 candidates.sort(key=lambda item:-(item[0]['properties'].get('interest',2)*70+(90 if any(r.get('dataset_id')=='geoportal-kultura' for r in item[0]['properties'].get('source_refs',[])) else 0)-abs(item[2]-radius*.65)*.08))
 result=[]
 for item in candidates:
  if all(item[1].distance(other[1])>100 for other in result):result.append(item)
  if len(result)>=18:break
 return result

def spatial_loop_pairs(start,target):
 # Explore different directions through actual OSM junctions; GH supplies all connecting geometry.
 if ROUTING_ANCHOR_TREE is None:return []
 origin=transform(TO_M,Point(start));pairs=[];seen=set()
 def anchor(radius,bearing):
  angle=math.radians(bearing);wanted=Point(origin.x+radius*math.sin(angle),origin.y+radius*math.cos(angle));ids=ROUTING_ANCHOR_TREE.query(wanted,predicate='dwithin',distance=max(100,radius*.3))
  suitable=[int(i) for i in ids if radius*.55<=origin.distance(ROUTING_ANCHOR_POINTS[int(i)])<=radius*1.4]
  if not suitable:return None
  return min(suitable,key=lambda i:wanted.distance(ROUTING_ANCHOR_POINTS[i]))
 for divisor,bearings in [(4.3,range(0,360,45)),(5.0,range(0,360,90))]:
  radius=target/divisor
  for bearing in bearings:
   a=anchor(radius,bearing);c=anchor(radius,bearing+90)
   if a is None or c is None or a==c:continue
   if ROUTING_ANCHOR_POINTS[a].distance(ROUTING_ANCHOR_POINTS[c])<150:continue
   key=tuple(sorted([a,c]))
   if key in seen:continue
   seen.add(key);pairs.append((ROUTING_ANCHORS[a]['coordinates'],ROUTING_ANCHORS[c]['coordinates']))
 return pairs

async def terminal_edge_access(start):
 """Conservatively certify an unavoidable start/end stem on a terminal GH edge."""
 try:
  async with httpx.AsyncClient(timeout=3) as client:
   response=await client.get(GH+'/spt',params={'profile':'foot','point':str(start[1])+','+str(start[0]),'distance_limit':220,'columns':'node_id,prev_node_id,edge_id,distance,longitude,latitude,street_name'})
   response.raise_for_status();rows=list(csv.DictReader(io.StringIO(response.text)))
  root=next(r for r in rows if r['prev_node_id']=='-1');children=[r for r in rows if r['prev_node_id']==root['node_id']]
  if len(children)!=2:return None
  for leaf in children:
   if any(r['prev_node_id']==leaf['node_id'] for r in rows):continue
   if not 0<float(leaf['distance'])<=180:continue
   xy=[float(leaf['longitude']),float(leaf['latitude'])]
   path=await gh_route([xy,start],'plain',{'details':['intersection','edge_id','road_class']})
   if not path:continue
   snaps=path.get('snapped_waypoints',{}).get('coordinates',[]);detail=path.get('details',{}).get('intersection',[])
   if len(snaps)!=2 or hav(xy,snaps[0][:2])>1 or not detail:continue
   if hav([float(root['longitude']),float(root['latitude'])],snaps[-1][:2])>1:continue
   if abs(float(path['distance'])-float(leaf['distance']))>3 or len(path.get('details',{}).get('edge_id',[]))!=1:continue
   # The GH graph exposes just one permitted departure from this adjacent end node.
   if sum(bool(v) for v in detail[0][2].get('entries',[]))!=1:continue
   exit_node=next(c for c in children if c is not leaf);distance=float(exit_node['distance'])
   if not 60<distance<=164:return None
   return {'kind':'terminal_edge','minimum_shared_m':distance,'street':exit_node.get('street_name',''),'source':'GraphHopper foot graph: root has two exits; one adjacent end node has one permitted departure','checked_deadend_entries':1}
 except (httpx.HTTPError,ValueError,KeyError,StopIteration):return None
 return None

async def build_pool(req):
 if req.mode=='destination':
  paths=await asyncio.gather(*[gh_route([req.start,req.end],pref) for pref in ['plain','balanced','green','pedestrian']])
  if not paths[0]:raise HTTPException(422,'Nismo pronašli pješački put između ovih točaka. Odaberi točku bliže javnom putu ili drugi ulaz.')
  base=analyze_path(paths[0],req.start);candidates=[base]+[analyze_path(p,req.start) for p in paths[1:] if p]
  corridor=base['_line'];targets=[]
  for idx in POI_TREE.query(corridor.buffer(180)):
   f,p=POI[int(idx)];along=corridor.project(p)
   if f['properties'].get('interest',2)>=3 and 100<along<corridor.length-100 and corridor.distance(p)<180:targets.append((f,p))
  targets.sort(key=lambda item:-item[0]['properties'].get('interest',2))
  paths=await asyncio.gather(*[gh_route([req.start,f['geometry']['coordinates'],req.end],'balanced') for f,p in targets[:3]])
  candidates.extend(analyze_path(p,req.start) for p in paths if p)
  candidates=[r for r in candidates if r['distance_m']<=base['distance_m']*1.35]
  base_distance=base['distance_m']
 else:
  target=req.duration*80;seeds=[7,19,41,91];scales=[1,.92,1.06,1]
  paths=await asyncio.gather(*[gh_route([req.start],pref,{'algorithm':'round_trip','round_trip.distance':target*scale,'round_trip.seed':seed}) for pref in ['balanced','green','pedestrian'] for seed,scale in zip(seeds,scales)])
  pairs=[];near=anchors_near(req.start,target*.43)
  for i,(fa,pa,da) in enumerate(near):
   for fb,pb,db in near[i+1:]:
    ab=pa.distance(pb);perimeter=da+ab+db
    if ab<180 or not target*.52<perimeter<target*.98:continue
    cosine=(da*da+db*db-ab*ab)/max(1,2*da*db)
    if cosine>.88:continue
    pairscore=abs(perimeter*1.18-target)-30*(fa['properties'].get('interest',2)+fb['properties'].get('interest',2))-90*sum(any(r.get('dataset_id')=='geoportal-kultura' for r in f['properties'].get('source_refs',[])) for f in [fa,fb]);pairs.append((pairscore,fa,fb))
  pairs.sort(key=lambda x:x[0]);selected_pairs=[];used=set()
  for score,fa,fb in pairs:
   ids={fa['properties']['id'],fb['properties']['id']}
   if len(ids&used)>0:continue
   selected_pairs.append((fa,fb));used|=ids
   if len(selected_pairs)>=4:break
  if selected_pairs:
   paths.extend(await asyncio.gather(*[gh_route([req.start,fa['geometry']['coordinates'],fb['geometry']['coordinates'],req.start],'balanced') for fa,fb in selected_pairs]))
  shape_candidates=[];access_evidence=None;access_limit=60
  def selected_shapes():
   eligible=[r for r in shape_candidates if loop_backtracking_ok(r['metrics'],access_limit)]
   return select_loop_candidates(eligible,req.duration)
  def collect_shape(path):
   if not path:return
   r=analyze_path(path,req.start);coords=r['geometry']['coordinates'];m=r['metrics']
   if hav(coords[0],coords[-1])>25 or m['repeat_ratio']>.06 or not loop_backtracking_ok(m,180) or m['compactness']<.045:return
   shape_candidates.append(r)
  for path in paths:collect_shape(path)
  candidates=selected_shapes()
  if len(candidates)<2 and any(r['metrics']['access_tail_m']>60 for r in shape_candidates):
   access_evidence=await terminal_edge_access(req.start)
   if access_evidence:access_limit=min(180,access_evidence['minimum_shared_m']+16);candidates=selected_shapes()
  if len(distinct_routes(candidates))<5:
   network_pairs=spatial_loop_pairs(req.start,target)
   if network_pairs:
    network_paths=await bounded_choice_paths([gh_route([req.start,a,c,req.start],'balanced') for a,c in network_pairs],seconds=6)
    for path in network_paths:collect_shape(path)
    candidates=selected_shapes()
  if not candidates:
   compact_pairs=spatial_loop_pairs(req.start,target*4.3/5.5);tours=[];tour_keys=set()
   for a,c in compact_pairs:
    for same,d in compact_pairs:
     if c!=same or d==a:continue
     key=tuple(tuple(x) for x in [a,c,d])
     if key not in tour_keys:tour_keys.add(key);tours.append([a,c,d])
   if tours:
    tour_paths=await asyncio.gather(*[gh_route([req.start,*tour,req.start],'balanced') for tour in tours[:8]])
    for path in tour_paths:collect_shape(path)
    candidates=selected_shapes()
  if len(distinct_routes(candidates))<3:
   extra_paths=await asyncio.gather(*[gh_route([req.start],'balanced',{'algorithm':'round_trip','round_trip.distance':target*scale,'round_trip.seed':seed}) for seed,scale in [(3,1),(29,.94),(57,1.05),(113,1),(233,.90),(761,1.08)]])
   for path in extra_paths:collect_shape(path)
   candidates=selected_shapes()
  if access_evidence:
   for r in candidates:
    if r['metrics']['access_tail_m']>60:r['access_evidence']=access_evidence
  if not candidates:raise HTTPException(422,'Za ovo polazište i vrijeme nismo pronašli kvalitetan krug bez nepotrebnog vraćanja. Odaberi drugo trajanje ili polazište.')
  base_distance=None
 unique=[]
 for r in candidates:
  if all(r['_segments']!=u['_segments'] for u in unique):unique.append(r)
 return {'candidates':unique,'base_distance':base_distance,'created':time.monotonic()}

async def candidate_pool(req):
 key=(req.mode,tuple(round(x,6) for x in req.start),tuple(round(x,6) for x in req.end) if req.end else None,req.duration if req.mode=='loop' else None)
 lock=POOL_LOCKS.setdefault(key,asyncio.Lock())
 async with lock:
  cached=POOL_CACHE.get(key)
  if cached and time.monotonic()-cached['created']<300:POOL_CACHE.move_to_end(key);return cached
  now=time.monotonic()
  while REQUEST_TIMES and now-REQUEST_TIMES[0]>60:REQUEST_TIMES.popleft()
  if len(REQUEST_TIMES)>=35:raise HTTPException(429,'Mnogo je izračuna u kratkom vremenu. Pričekaj trenutak i pokušaj ponovno.')
  REQUEST_TIMES.append(now)
  async with LIMIT:pool=await build_pool(req)
  POOL_CACHE[key]=pool
  while len(POOL_CACHE)>24:
   old,_=POOL_CACHE.popitem(last=False)
   if old!=key:POOL_LOCKS.pop(old,None)
  return pool

@app.post('/plan')
async def plan(req:Plan):
 if req.mode=='destination' and req.end is None:raise HTTPException(422,'Odaberi odredište pa pokušaj ponovno.')
 if req.end and hav(req.start,req.end)<50 and req.mode=='destination':raise HTTPException(422,'Polazište i odredište vrlo su blizu. Za obilazak odaberi kružnu šetnju.')
 engine_health=await health()
 if isinstance(engine_health,JSONResponse):raise HTTPException(503,'Servis za izračun putova trenutačno se priprema. Pokušaj ponovno za trenutak.')
 pool=await candidate_pool(req);candidates=pool['candidates'];base_distance=pool['base_distance']
 balanced=req.model_copy(update={'preference':'balanced'});baseline=min(candidates,key=lambda r:route_rank(r,balanced,base_distance))
 ordered=sorted(candidates,key=lambda r:route_rank(r,req,base_distance));metric={'green':'green_pct','pedestrian':'walking_pct','culture':'culture_score'}.get(req.preference)
 if metric:
  minimum=2 if req.preference=='culture' else 3
  better=[r for r in ordered if r['metrics'][metric]>=baseline['metrics'][metric]+minimum]
  first=better[0] if better else baseline
  ordered=[first]+[r for r in ordered if r is not first]
 selected=distinct_routes(ordered,5)
 routes=[];used_names=set()
 for i,r in enumerate(selected):
  name=choice_name(r,selected,used_names)
  public=public_route(r,name,'PREPORUKA' if i==0 else 'ALTERNATIVA',req.mode,req.duration)
  public['choice_summary']=str(r['metrics']['green_pct'])+'% uz zelenilo · '+str(r['metrics']['walking_pct'])+'% pješačkim putovima'
  routes.append(public)
 chosen=selected[0];changed=chosen['geometry']!=baseline['geometry'];metric={'green':'green_pct','pedestrian':'walking_pct','culture':'culture_score'}.get(req.preference)
 gain=round(chosen['metrics'].get(metric,0)-baseline['metrics'].get(metric,0),1) if metric else 0
 message='Rute su uspoređene prema tvom odabiru. Vrijeme prikazuje hodanje bez razgledavanja.'
 if req.preference!='balanced' and not changed:message='Najbolji pronađeni put već odgovara ovom prioritetu. Nismo pronašli bolju različitu rutu unutar zadanog vremena.' if req.mode=='loop' else 'Za ove točke najbolji pronađeni put već odgovara odabranom prioritetu.'
 return {'routes':routes,'message':message,'mode':req.mode,'preference':req.preference,'preference_effect':{'changed_from_balanced':changed,'metric':metric,'gain':gain},'candidate_count':len(candidates),'data_snapshot':META['snapshot'],'method':'Stvarna mreža putova, izmjerene dionice i relevantne lokacije uz odabranu rutu.','portal_sources':['pedestrian','culture','water']}

@app.post('/gpx')
def gpx(req:Gpx):
 for c in req.coordinates:
  if len(c)!=2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in c) or not(META['bounds'][0]-.02<c[0]<META['bounds'][2]+.02 and META['bounds'][1]-.02<c[1]<META['bounds'][3]+.02):raise HTTPException(422,'Podaci rute nisu valjani za izvoz.')
 ET.register_namespace('','http://www.topografix.com/GPX/1/1');root=ET.Element('{http://www.topografix.com/GPX/1/1}gpx',version='1.1',creator='Lagano — Zagreb')
 trk=ET.SubElement(root,'trk');ET.SubElement(trk,'name').text=req.name;ET.SubElement(trk,'desc').text='Pješačka ruta modelirana iz otvorenih podataka. Provjeri stanje i znakove na terenu. © OpenStreetMap contributors, ODbL; Grad Zagreb, Otvorena dozvola.'
 seg=ET.SubElement(trk,'trkseg')
 for lon,lat in req.coordinates:ET.SubElement(seg,'trkpt',lat=f'{lat:.7f}',lon=f'{lon:.7f}')
 return {'filename':'lagano-setnja.gpx','gpx':ET.tostring(root,encoding='unicode',xml_declaration=True)}
