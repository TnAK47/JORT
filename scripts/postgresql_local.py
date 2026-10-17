"""Serveur PostgreSQL appartenant uniquement à JORT, dans le dossier du projet."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'data/config/base.json'
SECRET=ROOT/'data/secrets/postgresql.json'
CLUSTER=ROOT/'data/postgresql'
BIN=ROOT/'tools/pgsql/bin'
PORT=55432


def enabled(root=ROOT):
    p=Path(root)/'data/config/base.json'
    if not p.exists():return False
    config=json.loads(p.read_text(encoding='utf-8'))
    return config.get('engine')=='postgresql' and config.get('state')=='active'


def secret(root=ROOT):
    return json.loads((Path(root)/'data/secrets/postgresql.json').read_text(encoding='utf-8'))


def connect(root=ROOT,role='writer',database=None):
    import psycopg
    from psycopg.rows import dict_row
    config=secret(root)
    return psycopg.connect(host='127.0.0.1',port=config['port'],dbname=database or 'jort',
        user=config['roles'][role]['user'],password=config['roles'][role]['password'],
        connect_timeout=5,application_name='JO-JORT-'+role.upper(),row_factory=dict_row,
        # Une requête HTTP reçoit sa connexion pour la durée de son travail
        # seulement. Ces garde-fous libèrent aussi une connexion abandonnée.
        options='-c idle_session_timeout=60000 -c idle_in_transaction_session_timeout=30000 -c statement_timeout=120000 -c lock_timeout=5000')


def run(*args,check=True):
    background=args[0]=='pg_ctl.exe' and 'start' in args
    output={'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL} if background else {'capture_output':True}
    result=subprocess.run([str(BIN/args[0]),*map(str,args[1:])],**output,text=True,encoding='utf-8',errors='replace',
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if check and result.returncode:raise RuntimeError((result.stderr or result.stdout or 'Démarrage PostgreSQL échoué ; voir data/logs/postgresql.log.')[-1500:])
    return result


def start():
    if not (CLUSTER/'PG_VERSION').exists():raise RuntimeError('Base PostgreSQL non initialisée : exécuter postgresql_local.py init.')
    if run('pg_ctl.exe','-D',CLUSTER,'status',check=False).returncode==0:return
    run('pg_ctl.exe','-D',CLUSTER,'-l',ROOT/'data/logs/postgresql.log','-w','-t','60','start')


def stop():
    if not (CLUSTER/'PG_VERSION').exists():return
    if run('pg_ctl.exe','-D',CLUSTER,'status',check=False).returncode==0:
        run('pg_ctl.exe','-D',CLUSTER,'-m','fast','-w','-t','60','stop')


def initialize():
    from psycopg import sql
    from collecter_2024 import save_json
    (ROOT/'data/secrets').mkdir(parents=True,exist_ok=True)
    (ROOT/'data/config').mkdir(parents=True,exist_ok=True)
    (ROOT/'data/logs').mkdir(parents=True,exist_ok=True)
    if not SECRET.exists():
        credentials={'port':PORT,'roles':{role:{'user':'jort_'+role,'password':secrets.token_urlsafe(36)} for role in ('owner','writer','reader')}}
        save_json(SECRET,credentials)
        if os.name=='nt':
            import ctypes
            # The user running the installation owns the secret; no password appears in a command line.
            account=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','')
            result=subprocess.run(['icacls',str(SECRET),'/inheritance:r','/grant:r',account+':F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise RuntimeError('Protection du fichier de connexion échouée.')
    credentials=secret()
    if not (CLUSTER/'PG_VERSION').exists():
        pw=ROOT/'data/secrets/initialisation.tmp'
        try:
            pw.write_text(credentials['roles']['owner']['password'],encoding='utf-8')
            run('initdb.exe','-D',CLUSTER,'-U','jort_owner','--pwfile',pw,'--auth=scram-sha-256','--encoding=UTF8','--locale=C')
        finally:pw.unlink(missing_ok=True)
        with (CLUSTER/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\nshared_buffers = '128MB'\nwork_mem = '8MB'\nmax_connections = 50\nidle_session_timeout = '60s'\nidle_in_transaction_session_timeout = '30s'\nstatement_timeout = '120s'\nlock_timeout = '5s'\nlog_line_prefix = 'JO-PG %m [%p] %a '\n")
    start()
    with connect(role='owner',database='postgres') as db:
        db.autocommit=True
        for role in ('writer','reader'):
            info=credentials['roles'][role]
            if not db.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(info['user'],)).fetchone():
                db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(info['user']),sql.Literal(info['password'])))
        if not db.execute("SELECT 1 FROM pg_database WHERE datname='jort'").fetchone():db.execute('CREATE DATABASE jort OWNER jort_owner')
        db.execute('REVOKE ALL ON DATABASE jort FROM PUBLIC')
        db.execute('GRANT CONNECT ON DATABASE jort TO jort_writer,jort_reader')
        db.execute('ALTER ROLE jort_reader SET default_transaction_read_only=on')
    with connect(role='owner') as db:
        db.execute((ROOT/'scripts/schema_postgresql.sql').read_text(encoding='utf-8'),prepare=False)
        db.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        db.execute('GRANT USAGE ON SCHEMA public TO jort_reader,jort_writer')
        db.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO jort_reader')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO jort_writer')
        db.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO jort_writer')
    if not CONFIG.exists():save_json(CONFIG,{'engine':'postgresql','state':'preparation','schema_version':1})
    return {'state':'ready','port':PORT,'cluster':'data/postgresql','engine':'postgresql'}


def health(root=ROOT):
    with connect(root,role='reader') as db:
        return dict(state='ok',engine='postgresql',version=db.execute('SELECT version() AS version').fetchone()['version'],
            writable=False,active=enabled(root))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','start','stop','status'])
    args=parser.parse_args()
    if args.action=='init':print(json.dumps(initialize()))
    elif args.action=='start':start();print(json.dumps(health()))
    elif args.action=='stop':stop();print('{"state":"stopped"}')
    else:print(json.dumps(health()))
