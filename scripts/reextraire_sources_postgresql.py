"""Réextraction ciblée de journaux déjà importés, avec point de reprise."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from collecter_2024 import save_json
from migration_postgresql import publish, source_rows
from base_postgresql import Resolver, ingest_document
from postgresql_local import ROOT


def reextract(source_ids, *, root=ROOT, apply=False):
    """Relit seulement les journaux demandés et conserve leurs annonces source."""
    root = Path(root)
    requested = {str(value).strip() for value in source_ids if str(value).strip()}
    if not requested:
        raise ValueError('Au moins un identifiant de journal est requis.')
    report = dict(
        state='dry_run' if not apply else 'running',
        started_at=datetime.now(timezone.utc).isoformat(),
        requested=sorted(requested),
        documents=0,
        announcements=0,
        errors=[],
    )
    output = root / 'data/maintenance/reextraction-sources-postgresql.json'
    save_json(output, report)
    found = set()
    resolver = Resolver(root)
    try:
        for source, records, signature in source_rows(root):
            source_id = str(source['id'])
            if source_id not in requested:
                continue
            found.add(source_id)
            if not apply:
                report['documents'] += 1
                report['announcements'] += len(records)
                continue
            try:
                result = ingest_document(root, source, records, resolver, signature)
                report['documents'] += 1
                report['announcements'] += result['announcements']
            except Exception as exc:
                resolver.clear()
                report['errors'].append(dict(source_id=source_id, error=str(exc)))
            report['updated_at'] = datetime.now(timezone.utc).isoformat()
            save_json(output, report)
    finally:
        resolver.close()
    report['missing_sources'] = sorted(requested - found)
    if apply and report['documents']:
        report['publication_revision'] = publish(root)['revision']
    report['state'] = 'attention' if report['errors'] or report['missing_sources'] else ('done' if apply else 'dry_run')
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save_json(output, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Réextrait des journaux connus sans téléchargement.')
    parser.add_argument('--source-ids', nargs='+', required=True, help='Identifiants de journaux, par exemple jal-fr-2017-092-b3a49060.')
    parser.add_argument('--apply', action='store_true', help='Écrit les entités et liens recalculés dans PostgreSQL.')
    args = parser.parse_args()
    print(json.dumps(reextract(args.source_ids, apply=args.apply), ensure_ascii=True))
