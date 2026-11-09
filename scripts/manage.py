#!/usr/bin/env python3
from pathlib import Path
import os,sys,signal,subprocess,time,urllib.request,json
B=Path(__file__).resolve().parents[1]
CONFIG=json.loads((B/'runtime/service-config.json').read_text()) if (B/'runtime/service-config.json').exists() else {}
BACKEND_PORT=int(CONFIG.get('backend_port',8316));ROUTING_PORT=int(CONFIG.get('routing_port',8314))
SERVICES={
 'graphhopper':["java","-Xmx1500m","-jar",str(B/'runtime/graphhopper-web-11.1.jar'),"server",str(B/'backend/graphhopper.yml')],
 'backend':[str(B/'.venv/bin/uvicorn'),"backend.app:app","--host","127.0.0.1","--port",str(BACKEND_PORT),"--no-access-log"],
 'wordpress':[str(B/'runtime/frankenphp'),"run","--config",str(B/'runtime/Caddyfile'),"--adapter","caddyfile"]}
def alive(name):
 try:
  pid=int((B/'private'/f'{name}.pid').read_text());args=Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0',b' ').decode()
  if str(B) not in args:return None
  return pid
 except (FileNotFoundError,ValueError,PermissionError):return None
def start(name):
 if alive(name):return
 env=os.environ.copy();env['LAGANO_GH_URL']=f'http://127.0.0.1:{ROUTING_PORT}'
 for key,d in [('XDG_DATA_HOME','xdg-data'),('XDG_CONFIG_HOME','xdg-config'),('XDG_CACHE_HOME','xdg-cache')]:env[key]=str(B/'runtime'/d)
 with open(B/'logs'/f'{name}.log','a') as log:p=subprocess.Popen(SERVICES[name],cwd=B,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 f=B/'private'/f'{name}.pid';f.write_text(str(p.pid));f.chmod(0o600)
def stop(name):
 pid=alive(name)
 if pid:
  os.kill(pid,signal.SIGTERM)
  for _ in range(60):
   if not alive(name):break
   time.sleep(.1)
  if alive(name):raise RuntimeError(f'{name}: graceful stop timed out; inspect process')
 (B/'private'/f'{name}.pid').unlink(missing_ok=True)
cmd=sys.argv[1] if len(sys.argv)>1 else 'status';names=sys.argv[2:] or list(SERVICES)
if any(n not in SERVICES for n in names):raise SystemExit('Unknown project service')
if cmd in ('stop','restart'):
 for n in reversed(names):stop(n)
if cmd in ('start','restart'):
 for n in names:start(n)
if cmd not in ('start','stop','restart','status'):raise SystemExit('Usage: manage.py start|stop|restart|status [service]')
if cmd in ('start','restart'):
 urls={'graphhopper':f'http://127.0.0.1:{ROUTING_PORT}/info','backend':f'http://127.0.0.1:{BACKEND_PORT}/health','wordpress':CONFIG.get('app_url','http://127.0.0.1:18319/')}
 for n in names:
  ready=False
  for _ in range(240):
   try:
    with urllib.request.urlopen(urls[n],timeout=2) as res:ready=res.status==200
   except Exception:pass
   if ready:break
   if not alive(n):raise RuntimeError(f'{n}: process exited; inspect logs')
   time.sleep(.3)
  if not ready:raise RuntimeError(f'{n}: readiness timeout; inspect logs')
for n in SERVICES:print(n,alive(n) or 'stopped')
