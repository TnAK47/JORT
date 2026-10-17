"""Remise en cohérence avant collecte, serveur arrêté, sans relire les PDF."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import socket
import sqlite3
import traceback

from collecter_2024 import save_json
from maintenance_base import maintenance, _backup, _sync_preserving_links

ROOT=Path(__file__).resolve().parents[1]


def fingerprint(root):
    paths=list((root/'scripts').glob('*.py'))
    paths.extend((root/'data').glob('[0-9][0-9][0-9][0-9]/base.json'))
    paths.extend((root/'data').glob('[0-9][0-9][0-9][0-9]/*.sqlite*'))
    paths.extend((root/'data').glob('inventaire-*-fr.json'))
    paths.extend(p for p in (root/'data/referentiel-entites.sqlite',root/'data/identity-merges.json') if p.exists())
    stamps=[(str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(paths)]
    return hashlib.sha256(json.dumps(stamps).encode('utf-8')).hexdigest()


def reconcile_paths(server,years):
    """Réconcilier les références exactes connues, sans lire ni télécharger les PDF."""
    changes=[]
    for selected_year in years:
        server.select_year(selected_year)
        base=server.year_status_snapshot(selected_year)
        manifest=server.manifest()
        rows={row['number']:row for row in manifest['journals']}
        sources={source['number']:source for source in base.get('sources',[])}
        for source in sources.values():
            row=rows.setdefault(source['number'],dict(number=source['number']))
            row.setdefault('date',source.get('date'))
            row.setdefault('sha256',source.get('sha256'))
            if not row.get('path') and source.get('canonical_pdf'):
                row['path']=source['canonical_pdf']
        manifest['journals']=list(rows.values())
        source_updates={}
        for item in server.files_status(base=base,m=manifest):
            row=rows[item['number']]
            if item.get('status')=='present':
                row.update(path=item['path'],status='telecharge')
                source=sources.get(item['number'])
                if (source and row.get('date')==source.get('date')
                        and row.get('sha256')==source.get('sha256')
                        and source.get('canonical_pdf')!=item['path']):
                    source_updates[item['number']]=item['path']
            elif item.get('status')=='missing':
                row['status']='inventorie'
        server.save_json(server.manifest_path(),manifest)
        if source_updates:
            path=server.dataset()
            db=path.parent/f'jort-{selected_year}-fr.sqlite'
            run_id=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-chemins-pdf'
            backup=_backup(server.ROOT,[path,db],run_id)
            annual=server.read_json(path,{})
            for source in annual['sources']:
                if source['number'] in source_updates:
                    source['canonical_pdf']=source_updates[source['number']]
            annual['revision']=server.dataset_revision(annual)
            server.save_json(path,annual)
            _sync_preserving_links(db,annual,set(source_updates))
            changes.append(dict(year=selected_year,references=source_updates,backup=backup))
    return changes


def prepare(root=ROOT):
    root=Path(root).resolve()
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1',8765))==0:
            raise RuntimeError('Arrêtez le serveur avant la préparation ; les écritures doivent rester coordonnées.')
    path=root/'data/maintenance/preparation-collecte.json'
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous.get('state')=='ready' and previous.get('fingerprint')==fingerprint(root):
        print('Base déjà contrôlée et inchangée ; préparation réutilisée.',flush=True)
        return previous
    report=dict(state='running',started_at=datetime.now(timezone.utc).isoformat(),steps=[],pdf_reread=False)
    save_json(path,report)
    try:
        years=sorted(int(p.parent.name) for p in (root/'data').glob('[0-9][0-9][0-9][0-9]/base.json'))
        if years:
            for mode in ('backup','deduplicate_announcements','repair'):
                print(f'Préparation globale : {mode}',flush=True)
                result=maintenance(root,mode=mode,years=years,full=True,publish=False,
                                   progress=lambda p:print(p.get('message','Contrôle'),flush=True))
                report['steps'].append({k:result.get(k) for k in ('id','mode','state','can_publish','severity_counts','issue_counts','backups')})
                save_json(path,report)
                if result.get('state')!='done' or result.get('severity_counts',{}).get('error',0):
                    raise RuntimeError(f'Préparation {mode} interrompue ; consulter le rapport {result.get("id")}.')
                if mode in ('repair','deduplicate_announcements') and not result.get('can_publish'):
                    raise RuntimeError('Références incohérentes : la collecte reste arrêtée.')

            import serveur_local as server
            report['source_paths_reconciled']=reconcile_paths(server,years)
            # Une seule reconstruction globale : IDs durables, fusions présumées
            # et liens sourcés ; les occurrences annuelles restent conservées.
            print('Regroupement global des entités et publication des liens.',flush=True)
            server.publish_boundary()
            result=maintenance(root,mode='optimize',years=years,full=True,publish=False)
            report['steps'].append({k:result.get(k) for k in ('id','mode','state','sizes','backups','severity_counts')})
            if result.get('state')!='done':
                raise RuntimeError('Optimisation incomplète ; consulter le rapport administrateur.')
            registry=root/'data/referentiel-entites.sqlite'
            if registry.is_file():
                with closing(sqlite3.connect(registry,timeout=30)) as db:
                    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                        raise RuntimeError('Le référentiel des entités est incohérent.')
                    db.execute('PRAGMA optimize')
                    db.execute('VACUUM')
                    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                        raise RuntimeError('Contrôle du référentiel après optimisation en échec.')
                    report['identity_summary']=dict(unique_entities=db.execute('SELECT COUNT(DISTINCT canonical_id) FROM entity_occurrences WHERE active=1').fetchone()[0],
                                                    preserved_occurrences=db.execute('SELECT COUNT(*) FROM entity_occurrences WHERE active=1').fetchone()[0])
            report.update(years=years,
                          queue=server.job.get('queue'),publication=server.job.get('publication'))
        report.update(state='ready',finished_at=datetime.now(timezone.utc).isoformat(),fingerprint=fingerprint(root))
    except Exception as exc:
        report.update(state='failed',error=str(exc),traceback=traceback.format_exc())
        save_json(path,report)
        raise
    save_json(path,report)
    print('Base cohérente ; sauvegardes et rapport sous data/maintenance/.',flush=True)
    return report


if __name__=='__main__':
    prepare()
