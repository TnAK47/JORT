"""Préparer la consultation au lancement, uniquement si sa vue est absente ou périmée."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from restitution_index import Store, VERSION, add_terms, qualify

ROOT=Path(__file__).resolve().parents[1]


def prepare():
    from postgresql_local import enabled,health
    if enabled(ROOT):
        report=health(ROOT)
        with Store(ROOT).connect() as db:
            row=db.execute("SELECT value FROM jort_meta WHERE key='bootstrap'").fetchone()
            if not row:raise RuntimeError('Publication PostgreSQL absente : terminer la migration.')
        return dict(state='ready',engine='postgresql',rebuilt=False,revision=row['value']['revision'])
    marker_path=ROOT/'dist/publication.json'
    marker=json.loads(marker_path.read_text(encoding='utf-8')) if marker_path.exists() else {}
    try:
        with closing(Store(ROOT).connect()) as db:
            metadata=json.loads(db.execute("SELECT payload FROM meta WHERE key='bootstrap'").fetchone()[0])
            db.execute('SELECT id FROM nodes LIMIT 1').fetchone()
            db.execute('SELECT id FROM announcements LIMIT 1').fetchone()
        if marker.get('state')=='ready' and metadata.get('revision')==marker.get('revision') and metadata.get('version')==VERSION:
            with closing(sqlite3.connect(Store(ROOT).path)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='announcement_terms_search'").fetchone():
                    add_terms(db); db.commit()
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='node_quality'").fetchone():
                    qualify(db); db.commit()
            return dict(state='ready',rebuilt=False,revision=metadata['revision'])
    except (OSError,ValueError,TypeError,sqlite3.Error):pass
    if not any((ROOT/'data').glob('[0-9][0-9][0-9][0-9]/base.json')):
        raise RuntimeError('Aucune base annuelle disponible pour préparer la consultation.')
    from agregation_base import reconstruire
    data=reconstruire(ROOT)
    return dict(state='ready',rebuilt=True,revision=data['revision'])


if __name__=='__main__':print(json.dumps(prepare()))
