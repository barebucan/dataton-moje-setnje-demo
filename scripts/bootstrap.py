#!/usr/bin/env python3
"""Install a private, loopback demonstration from the source bundle. Never overwrites a running installation."""
from pathlib import Path
import argparse,json,hashlib,urllib.request,shutil,subprocess,os,secrets,tarfile,zipfile
B=Path(__file__).resolve().parents[1];P=argparse.ArgumentParser();P.add_argument('--cache-dir',type=Path);P.add_argument('--reuse-venv',type=Path);P.add_argument('--smoke',action='store_true');P.add_argument('--port',type=int,default=18319);P.add_argument('--backend-port',type=int,default=18316);P.add_argument('--routing-port',type=int,default=18314);P.add_argument('--admin-port',type=int,default=18315);a=P.parse_args()
if (B/'runtime/wordpress/wp-config.php').exists():raise SystemExit('An installation already exists. Use manage.py, not bootstrap.py.')
for n in [a.port,a.backend_port,a.routing_port,a.admin_port]:
 if not 1024<=n<=65535:raise SystemExit('Use unprivileged ports')
for folder in ['runtime','private','logs','downloads']:(B/folder).mkdir(exist_ok=True)
(B/'private').chmod(0o700);(B/'private/database').mkdir(exist_ok=True)
manifest=json.loads((B/'data/provenance/runtime-manifest.json').read_text())
for r in manifest:
 name=r['name'];dest=B/'downloads'/name;bundled=B/'vendor'/name
 candidates=[bundled]+([a.cache_dir/name] if a.cache_dir else [])
 for source in candidates:
  if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()==r['sha256']:
   shutil.copyfile(source,dest);break
 else:
  urllib.request.urlretrieve(r.get('pinned_url',r['url']),dest)
 if hashlib.sha256(dest.read_bytes()).hexdigest()!=r['sha256']:raise SystemExit('Checksum mismatch: '+name+'; do not silently accept a changed latest URL')
for name in ['graphhopper-web-11.1.jar','frankenphp','wp-cli.phar']:shutil.copyfile(B/'downloads'/name,B/'runtime'/name)
(B/'runtime/frankenphp').chmod(0o755)
with tarfile.open(B/'downloads/wordpress.tar.gz') as archive:archive.extractall(B/'runtime',filter='data')
wp=B/'runtime/wordpress'
with zipfile.ZipFile(B/'downloads/sqlite.zip') as archive:
 for info in archive.infolist():
  target=(wp/'wp-content/plugins'/info.filename).resolve()
  if not target.is_relative_to(wp/'wp-content/plugins'):raise SystemExit('Unsafe plugin archive')
 archive.extractall(wp/'wp-content/plugins')
shutil.copyfile(wp/'wp-content/plugins/sqlite-database-integration/db.copy',wp/'wp-content/db.php')
for kind,name in [('plugins','lagano-walks'),('themes','lagano')]:shutil.copytree(B/'wordpress'/name,wp/'wp-content'/kind/name,dirs_exist_ok=True)
if a.reuse_venv:
 if not (a.reuse_venv/'bin/uvicorn').exists():raise SystemExit('Missing reusable Python environment')
 (B/'.venv').symlink_to(a.reuse_venv.resolve(),target_is_directory=True)
else:
 subprocess.run(['python3','-m','venv',str(B/'.venv')],check=True)
 subprocess.run([str(B/'.venv/bin/pip'),'install','-r',str(B/'backend/requirements.lock')],check=True)
pbf=B/'data/raw/zagreb-city.osm.pbf'
if a.smoke:
 # Build a small independent central extract, rather than duplicating the complete city graph for QA.
 code="""from pathlib import Path
import osmium
b=Path(__file__).resolve().parents[1];source=b/'data/raw/zagreb-city.osm.pbf'
with osmium.BackReferenceWriter(b/'data/raw/restore-centre.osm.pbf',ref_src=source,remove_tags=False) as writer:
 for obj in osmium.FileProcessor(source).with_locations().with_filter(osmium.filter.EntityFilter(osmium.osm.WAY)):
  if obj.tags.get('highway') and any(n.location.valid() and 15.963<n.lon<15.991 and 45.801<n.lat<45.824 for n in obj.nodes):writer.add_way(obj)
"""
 (B/'scripts/restore_extract.py').write_text(code);subprocess.run([str(B/'.venv/bin/python'),str(B/'scripts/restore_extract.py')],check=True);pbf=B/'data/raw/restore-centre.osm.pbf'
config=(B/'backend/graphhopper-city.yml').read_text();lines=[]
for line in config.splitlines():
 if line.strip().startswith('datareader.file:'):line='  datareader.file: '+str(pbf)
 if line.strip().startswith('graph.location:'):line='  graph.location: '+str(B/'data/graph-cache')
 if line.strip()=='port: 8314':line='      port: '+str(a.routing_port)
 if line.strip()=='port: 8315':line='      port: '+str(a.admin_port)
 lines.append(line)
(B/'backend/graphhopper.yml').write_text('\n'.join(lines)+'\n')
url=f'http://127.0.0.1:{a.port}';backend=f'http://127.0.0.1:{a.backend_port}'
(B/'runtime/service-config.json').write_text(json.dumps(dict(app_url=url,backend_port=a.backend_port,routing_port=a.routing_port)))
password=secrets.token_urlsafe(24);constants={'DB_NAME':'lagano','DB_USER':'','DB_PASSWORD':'','DB_HOST':'localhost','DB_CHARSET':'utf8','DB_COLLATE':'','DB_ENGINE':'sqlite','DB_PATH':str(B/'private/database/lagano.sqlite'),'WP_HOME':url,'WP_SITEURL':url,'WP_ENVIRONMENT_TYPE':'local','DISALLOW_FILE_EDIT':True,'WP_AUTO_UPDATE_CORE':False,'AUTOMATIC_UPDATER_DISABLED':True,'WP_DEBUG':False,'DISABLE_WP_CRON':True,'LAGANO_BACKEND':backend,'LAGANO_ROOT':str(B)}
for key in ['AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY','AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT']:constants[key]=secrets.token_urlsafe(48)
php='<?php\n'+''.join('define('+json.dumps(k)+','+json.dumps(v)+');\n' for k,v in constants.items())+"$table_prefix='wp_';\nif (!defined('ABSPATH')) define('ABSPATH',__DIR__.'/');\nrequire_once ABSPATH.'wp-settings.php';\n";p=wp/'wp-config.php';p.write_text(php);p.chmod(0o600)
base=[str(B/'runtime/frankenphp'),'php-cli',str(B/'runtime/wp-cli.phar'),'--path='+str(wp)]
def cli(args):
 result=subprocess.run(base+args,capture_output=True,text=True)
 if result.returncode:raise SystemExit('WP setup failed: '+result.stderr[-1200:])
cli(['core','install','--url='+url,'--title=Lagano','--admin_user=lagano-local','--admin_password='+password,'--admin_email=demo@example.invalid','--skip-email'])
cli(['option','update','permalink_structure','/%postname%/']);cli(['eval','flush_rewrite_rules(false);']);cli(['plugin','activate','lagano-walks']);cli(['theme','activate','lagano']);cli(['option','update','timezone_string','Europe/Zagreb'])
p=B/'private/admin.json';p.write_text(json.dumps(dict(user='lagano-local',password=password,scope='fresh local demonstration only')));p.chmod(0o600)
caddy = """{
 admin off
 auto_https off
 persist_config off
 frankenphp {
  num_threads 6
 }
}
http://127.0.0.1:PORT {
 bind 127.0.0.1
 root * ROOT
 encode gzip zstd
 @private path /wp-config.php /wp-content/db.php /wp-content/database/* /.git/* /xmlrpc.php
 respond @private 404
 header X-Robots-Tag "noindex, nofollow"
 header X-Content-Type-Options "nosniff"
 handle /voice* {
  reverse_proxy 127.0.0.1:VOICE_BIND
 }
 php_server
}
"""
caddy=caddy.replace('PORT',str(a.port)).replace('ROOT',json.dumps(str(wp))).replace('VOICE_BIND',str(a.backend_port))
(B/'runtime/Caddyfile').write_text(caddy)
subprocess.run(['python3',str(B/'scripts/manage.py'),'start'],check=True)
print('Fresh loopback installation ready:',url,'; generated credentials in private/admin.json. No original database or TLS key was used.')
