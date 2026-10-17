"""Plan par défaut. --apply installe le correctif préparé, serveur JORT arrêté.

--indexes ajoute les index prévus, --vacuum nettoie les versions mortes.
--rollback restaure le code sauvegardé (les index ajoutés restent disponibles).
Aucun téléchargement, fusion, changement mémoire global ou appel à un modèle.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import select
import subprocess
import sys
import tempfile
import textwrap

from postgresql_local import connect

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'scripts/consultation_postgresql.py'
TEMPLATE=ROOT/'scripts/optimisation_performance/recherche_entites.py'
DIRECTORY=ROOT/'data/maintenance/performance'
MANIFEST=DIRECTORY/'installation.json'
EXPECTED='51710f17a6d68dfd22eefc4b04a1fea5312ff48eb31c9e77f76e7f29cb0b2a14'
INDEXES={
    'jort_entity_search':('jort_entities','USING gin (normalized_name gin_trgm_ops)'),
    'jort_names_search_trgm':('jort_names','USING gin (normalized_name gin_trgm_ops)'),
    'jort_notice_visible_recent':('jort_announcements','(publication_date DESC NULLS LAST,id DESC) WHERE search_visible'),
    'jort_entity_active_label':('jort_entities',"(label,id) WHERE status='active' AND displayable"),
}
TABLES=('jort_entities','jort_names','jort_mentions','jort_entity_metrics','jort_announcements')


def now():return datetime.now(timezone.utc).isoformat()
def digest(content):return hashlib.sha256(content).hexdigest()


def method(source, class_name=None):
    tree=ast.parse(source)
    body=next(n.body for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name) if class_name else tree.body
    node=next(n for n in body if isinstance(n,ast.FunctionDef) and n.name=='nodes')
    lines=source.splitlines()
    return node,'\n'.join(lines[node.lineno-1:node.end_lineno])


def prepared_content(original):
    source=original.decode('utf-8');node,old=method(source,'Store')
    _,replacement=method(TEMPLATE.read_text(encoding='utf-8'))
    replacement=textwrap.indent(replacement,'    ')
    if old==replacement:return original
    if digest(old.encode())!=EXPECTED:
        raise RuntimeError('Store.nodes a changé depuis la préparation : réexaminer le correctif avant installation.')
    lines=source.splitlines(keepends=True)
    newline='\r\n' if '\r\n' in source else '\n'
    result=''.join(lines[:node.lineno-1])+replacement.replace('\n',newline)+newline+''.join(lines[node.end_lineno:])
    compile(result,str(TARGET),'exec')
    return result.encode('utf-8')


def atomic_write(path,content):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def save(report):atomic_write(MANIFEST,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))


@contextmanager
def exclusive():
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    with (DIRECTORY/'installation.lock').open('a+b') as stream:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def require_stopped():
    # La pile réseau de certains postes Windows intercepte localhost et peut
    # laisser un connect non bloquant en attente bien que le port soit libre.
    # Le système connaît directement ses sockets en écoute : c'est le contrôle
    # de référence dans ce cas, sans appeler l'API ni réveiller l'application.
    if os.name=='nt':
        check="$listener=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if($listener){exit 2}; exit 0"
        try:
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',check],capture_output=True,text=True,timeout=5)
            if result.returncode==0:return
            if result.returncode==2:
                raise RuntimeError('Arrêter le site et ses batchs avec arreter-jort.ps1 avant application. Garder PostgreSQL seul actif.')
        except RuntimeError:raise
        except Exception:
            # Repli ci-dessous si PowerShell / Get-NetTCPConnection est absent.
            pass
    with socket.socket() as probe:
        # Sous Windows, un socket avec délai retourne d'abord WSAEWOULDBLOCK
        # (10035) pendant que la connexion locale se résout. Attendre son
        # résultat réel évite de refuser une maintenance alors que le port est
        # libre.
        probe.setblocking(False)
        code=probe.connect_ex(('127.0.0.1',8765))
        if code in (0,):
            raise RuntimeError('Arrêter le site et ses batchs avec arreter-jort.ps1 avant application. Garder PostgreSQL seul actif.')
        if code in (10035,115):
            _,writable,_=select.select([], [probe], [], 1)
            if not writable:
                raise RuntimeError('Impossible de confirmer l’arrêt du site : le contrôle du port 8765 expire.')
            code=probe.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
    if code==0:
        raise RuntimeError('Arrêter le site et ses batchs avec arreter-jort.ps1 avant application. Garder PostgreSQL seul actif.')
    if code not in (10061,111):
        raise RuntimeError(f'Impossible de confirmer l’arrêt du site (code réseau {code}). Une restriction réseau ne prouve pas son arrêt.')


def apply(indexes=False,vacuum=False):
    require_stopped()
    with exclusive():
        original=TARGET.read_bytes();updated=prepared_content(original)
        report=json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
        if report and report.get('before_sha256') and digest(original) not in (report['before_sha256'],report.get('after_sha256')):
            raise RuntimeError('Le code a changé depuis la sauvegarde : intervention nécessaire.')
        if not report:
            backup=DIRECTORY/'consultation_postgresql.avant-performance.py'
            if backup.exists():raise RuntimeError('Sauvegarde sans manifeste : vérifier avant de continuer.')
            atomic_write(backup,original)
            report=dict(started_at=now(),backup=str(backup),before_sha256=digest(original),after_sha256=digest(updated),steps=[])
        report.update(state='running',updated_at=now());save(report)
        try:
            with connect(ROOT,role='owner') as db:
                db.autocommit=True
                db.execute("SET application_name='JO-JORT-PERFORMANCE'")
                db.execute("SET lock_timeout='3s'")
                db.execute("SET statement_timeout='15min'")
                if not db.execute('SELECT pg_try_advisory_lock(876555432) AS ok').fetchone()['ok']:
                    raise RuntimeError('Une autre optimisation est en cours.')
                # Refuser une maintenance concurrente, même sans site HTTP.
                others=db.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state<>'idle' AND backend_type='client backend'").fetchone()['n']
                if others:raise RuntimeError('Une autre opération SQL est active ; réessayer après sa fin.')
                for name,(table,definition) in INDEXES.items():
                    existing=db.execute('''SELECT i.indisvalid,t.relname AS table_name FROM pg_index i
                        JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
                        JOIN pg_namespace ns ON ns.oid=c.relnamespace WHERE ns.nspname='public' AND c.relname=%s''',(name,)).fetchone()
                    if existing:
                        if not existing['indisvalid'] or existing['table_name']!=table:
                            raise RuntimeError('Index invalide ou inattendu, examen requis : '+name)
                        continue
                    required=name in ('jort_entity_search','jort_names_search_trgm')
                    if not indexes:
                        if required:raise RuntimeError('Index de recherche absent : '+name+' ; relancer avec --indexes.')
                        continue
                    print('Création de '+name,flush=True)
                    db.execute(f'CREATE INDEX CONCURRENTLY {name} ON public.{table} {definition}')
                    report['steps'].append('index:'+name);save(report)
                for table in TABLES:
                    operation='VACUUM (ANALYZE)' if vacuum else 'ANALYZE'
                    print(operation+' '+table,flush=True)
                    db.execute(f'{operation} public.{table}')
                report['steps'].append('vacuum_analyze' if vacuum else 'analyze');save(report)
            # Le code est remplacé seulement après le succès des opérations SQL.
            if TARGET.read_bytes()!=original:raise RuntimeError('Code modifié pendant la préparation ; installation refusée.')
            atomic_write(TARGET,updated)
            report.update(state='applied',updated_at=now());save(report)
            print('Correctif installé. Redémarrer JORT avec son lanceur habituel.')
        except Exception as exc:
            report.update(state='failed',error=str(exc),updated_at=now());save(report)
            raise


def rollback():
    require_stopped()
    with exclusive():
        report=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if digest(TARGET.read_bytes())!=report['after_sha256']:
            raise RuntimeError('Le code a changé après installation : retour automatique refusé pour préserver les modifications.')
        backup=Path(report['backup']).resolve()
        if backup.parent!=DIRECTORY.resolve():raise RuntimeError('Chemin de sauvegarde inattendu.')
        content=backup.read_bytes()
        if digest(content)!=report['before_sha256']:raise RuntimeError('Sauvegarde altérée.')
        atomic_write(TARGET,content)
        report.update(state='rolled_back',updated_at=now());save(report)
        print('Code précédent restauré. Les index créés restent disponibles.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--apply',action='store_true');modes.add_argument('--rollback',action='store_true')
    parser.add_argument('--indexes',action='store_true');parser.add_argument('--vacuum',action='store_true')
    args=parser.parse_args()
    if (args.indexes or args.vacuum) and not args.apply:parser.error('--indexes et --vacuum nécessitent --apply.')
    if args.apply:apply(args.indexes,args.vacuum)
    elif args.rollback:rollback()
    else:
        prepared_content(TARGET.read_bytes())
        print('PLAN SEULEMENT — aucune modification de la base ou du code actif.')
        print('1. Sauvegarde du module ; contrôle serveur arrêté et accès PostgreSQL.')
        print('2. Index optionnels, statistiques ANALYZE ; VACUUM ordinaire optionnel.')
        print('3. Installation de la recherche par candidats UNION/INTERSECT.')
        print('4. Redémarrage manuel et nouvelle observation pour mesurer le gain.')
        print('Application : python scripts/ameliorer_performance_jort.py --apply --indexes --vacuum')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:print('JO-JORT : '+str(exc),file=sys.stderr);raise SystemExit(1)
