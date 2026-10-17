#!/usr/bin/env python3
r"""Observateur local de performance JORT, lecture seule.

Usage : .venv\Scripts\python.exe scripts\observer_performance_jort.py
Arrêt : Ctrl+C. Aucun appel modèle, aucune écriture PostgreSQL.
"""
from __future__ import annotations
import argparse, json, os, subprocess, time, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from postgresql_local import connect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'logs' / 'observations-performance' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())+'.jsonl')

def now(): return datetime.now(timezone.utc).isoformat()

def pg_snapshot(full=True):
    # Voir aussi les requêtes des autres rôles sans leur attribuer de nouveaux
    # privilèges. Cette connexion est expressément limitée à la lecture seule.
    with connect(ROOT, role='owner') as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL application_name='JO-JORT-OBSERVATION'")
        db.execute("SET LOCAL statement_timeout='3s'")
        active = db.execute("""select pid,application_name,left(coalesce(query,''),1200) query, state,
            extract(epoch from (clock_timestamp()-query_start))::float seconds,
            wait_event_type, wait_event
            from pg_stat_activity where datname=current_database()
            and pid<>pg_backend_pid() order by query_start nulls last LIMIT 50""").fetchall()
        dbstat = db.execute("select stats_reset,numbackends,xact_commit,xact_rollback,blks_read,blks_hit,temp_files,temp_bytes from pg_stat_database where datname=current_database()").fetchone()
        indexes = db.execute("""select schemaname,relname,indexrelname,idx_scan,idx_tup_read,
            idx_tup_fetch from pg_stat_user_indexes order by idx_scan nulls first,relname,indexrelname""").fetchall() if full else []
        settings = db.execute("select name,setting,unit from pg_settings where name in ('shared_buffers','work_mem','max_connections','effective_cache_size')").fetchall() if full else []
    return {'active': [dict(x) for x in active], 'database': dict(dbstat),
            'indexes': [dict(x) for x in indexes], 'settings': [dict(x) for x in settings]}

def api_probe(path, query):
    url='http://127.0.0.1:8765'+path+'?'+urllib.parse.urlencode(query)
    started=time.perf_counter()
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            body=response.read(8*1024*1024+1)
            if len(body)>8*1024*1024:raise ValueError('Réponse trop volumineuse pour cette sonde.')
            payload=json.loads(body)
            if path=='/api/health' and payload.get('service')!='jort-local':raise ValueError('Le port ne correspond pas à JORT.')
            return {'url':url,'status':response.status,'ms':round((time.perf_counter()-started)*1000,1),'bytes_received':len(body)}
    except Exception as exc:
        return {'url':url,'error':f'{type(exc).__name__}: {exc}','ms':round((time.perf_counter()-started)*1000,1)}

def memory():
    try:
        p=subprocess.run(['powershell','-NoProfile','-Command',"Get-Process python,postgres -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,WorkingSet64,CPU | ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:raise RuntimeError('Lecture des processus refusée.')
        return p.stdout.strip()
    except Exception as exc: return str(exc)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--interval',type=float,default=5); ap.add_argument('--probe',action='store_true')
    ap.add_argument('--minutes',type=float,default=20)
    args=ap.parse_args()
    if args.interval<2 or not 0<args.minutes<=120:ap.error('Intervalle >= 2 secondes, durée de 0 à 120 minutes.')
    OUT.parent.mkdir(parents=True,exist_ok=True)
    def log(event):
        with OUT.open('a',encoding='utf-8') as stream:stream.write(json.dumps(dict(at=now(),pid=os.getpid(),**event),ensure_ascii=False,default=str)+'\n')
    probes=[('/api/restitution/entities',{'q':'cofat','limit':20}),('/api/restitution/announcements',{'q':'cofat','page':0})]
    try:
        snapshot=pg_snapshot();health=api_probe('/api/health',{})
        if health.get('error'):raise RuntimeError(health['error'])
        log(dict(event='start',postgres=snapshot,health=health))
        print(f'Accès PostgreSQL et API vérifiés. Observation active : {OUT}',flush=True)
        end=time.monotonic()+args.minutes*60;full_at=probe_at=time.monotonic();index=0
        while time.monotonic()<end:
            stamp=time.monotonic();full=stamp>=full_at
            event=dict(event='sample',postgres=pg_snapshot(full))
            if full:event['memory']=memory();full_at=stamp+60
            if args.probe and stamp>=probe_at:
                path,q=probes[index%len(probes)];index+=1;probe_at=stamp+60
                event['api']=[api_probe(path,q)]
                if event['api'][0].get('error'):
                    args.probe=False
                    print('Sondes API désactivées après échec pour éviter de surcharger le serveur.',flush=True)
            log(event)
            if OUT.stat().st_size>=10*1024*1024:break
            time.sleep(min(args.interval,max(0,end-time.monotonic())))
        log(dict(event='stop',reason='durée ou taille maximale atteinte'));return 0
    except KeyboardInterrupt:log(dict(event='stop',reason='arrêt utilisateur'));return 0
    except Exception as exc:
        log(dict(event='failed',error=f'{type(exc).__name__}: {exc}'))
        print('Observation arrêtée : '+str(exc)+'\nRelancer depuis la session Windows autorisée à accéder à JORT.',flush=True)
        return 1
    finally:print('Journal conservé : '+str(OUT),flush=True)

if __name__=='__main__': raise SystemExit(main())
