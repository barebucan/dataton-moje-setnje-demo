from shapely.geometry import LineString
from shapely.strtree import STRtree
import math

def _edge_extent(mask,lengths):
 total=0.;last_match=0.;gap=0.
 for matched,distance in zip(mask,lengths):
  total+=distance
  if matched:last_match=total;gap=0.
  else:
   gap+=distance
   if gap>8.01:break
 return last_match

def _longest_run(mask,lengths):
 longest=run=gap=0.
 for matched,distance in zip(mask,lengths):
  if matched:
   run+=gap+distance;gap=0.;longest=max(longest,run)
  elif run:
   gap+=distance
   if gap>8.01:run=gap=0.
 return longest

def assess_backtracking(line,step=8.,tolerance=10.):
 """Measure reversed shared corridors; metres are sampled estimates, not proof of mandatory access."""
 length=line.length
 if length<1:return dict(near_retrace_ratio=0.,access_tail_m=0.,internal_retrace_m=0.,internal_near_retrace_m=0.,max_internal_return_m=0.)
 arcs=[];segments=[];midpoints=[];vectors=[];lengths=[]
 for k in range(math.ceil(length/step)):
  a=k*step;b=min(length,a+step);p=line.interpolate(a);q=line.interpolate(b);dx=q.x-p.x;dy=q.y-p.y;mag=math.hypot(dx,dy)
  segments.append(LineString([(p.x,p.y),(q.x,q.y)]));midpoints.append(line.interpolate((a+b)/2));vectors.append((dx/max(.001,mag),dy/max(.001,mag)));lengths.append(b-a);arcs.append((a+b)/2)
 tree=STRtree(segments);near=[False]*len(segments);exact=near.copy();symmetric=near.copy()
 for i,p in enumerate(midpoints):
  for value in tree.query(p,predicate='dwithin',distance=tolerance):
   j=int(value)
   if abs(arcs[i]-arcs[j])<32:continue
   if vectors[i][0]*vectors[j][0]+vectors[i][1]*vectors[j][1]>-.85:continue
   distance=p.distance(segments[j])
   if distance>tolerance:continue
   near[i]=True
   if distance<=2.5:exact[i]=True
   if abs(arcs[i]+arcs[j]-length)<=24:symmetric[i]=True
 prefix=_edge_extent(symmetric,lengths);suffix=_edge_extent(list(reversed(symmetric)),list(reversed(lengths)))
 # A tail is classified only when both ends share a spatially reversed corridor.
 if min(prefix,suffix)<16 or abs(prefix-suffix)>24:prefix=suffix=0.
 access=max(prefix,suffix)
 internal=[matched and arcs[i]>=prefix and arcs[i]<=length-suffix for i,matched in enumerate(near)]
 internal_exact=[matched and arcs[i]>=prefix and arcs[i]<=length-suffix for i,matched in enumerate(exact)]
 return dict(near_retrace_ratio=round(sum(d for d,v in zip(lengths,near) if v)/length,3),access_tail_m=round(access,1),internal_retrace_m=round(sum(d for d,v in zip(lengths,internal_exact) if v),1),internal_near_retrace_m=round(sum(d for d,v in zip(lengths,internal) if v),1),max_internal_return_m=round(_longest_run(internal,lengths),1))

def loop_backtracking_ok(metrics,allowed_access=60):
 return metrics['near_retrace_ratio']<=.10 and metrics['access_tail_m']<=allowed_access and metrics['internal_retrace_m']<=16 and metrics['internal_near_retrace_m']<=48 and metrics['max_internal_return_m']<=24


def select_loop_candidates(shape_candidates,target_minutes):
 """Do not justify extra shared access with a time target; compare cleaner shapes before time filtering."""
 if not shape_candidates:return []
 least_access=min(r['metrics']['access_tail_m'] for r in shape_candidates)
 return [r for r in shape_candidates if r['metrics']['access_tail_m']<=least_access+8 and target_minutes*.75<=r['duration_min']<=target_minutes+max(2,target_minutes*.10)]
