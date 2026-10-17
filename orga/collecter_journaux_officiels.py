"""Collecte privée des lois et décrets (ar/fr), sans abonnement ni annonces légales.

Source opérationnelle : catalogue public de la Library of Congress.
Les années absentes de ce catalogue ne sont pas déclarées complètes.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager, closing

ROOT = Path(__file__).resolve().parent
CATALOGUE = 'https://www.loc.gov/collections/foreign-legal-gazettes/?fa=location:tunisia&fo=json'
VERSION = '1'


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def request(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.loc.gov', 'tile.loc.gov'):
        raise ValueError('URL hors de la source autorisée : ' + url)
    return urllib.request.Request(url, headers={'User-Agent': 'JORT-Private-Research/1.0'})


def fetch_json(url, cache, refresh=False):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding='utf-8'))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request(url), timeout=60) as response:
                data = json.load(response)
            atomic_json(path, data)
            time.sleep(0.5)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(min(int(exc.headers.get('Retry-After', '5')), 60))
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def entry_from_loc(item):
    title = item.get('title', '')
    # Liste positive ET exclusion : le catalogue contient aussi les annonces légales.
    if not title.startswith('Official Gazette of the Republic of Tunisia;'):
        return None
    if re.search(r'announcement|judicial|legal notice', title, re.I):
        return None
    languages = {'arabic': 'ar', 'french': 'fr'}
    langs = [languages[x] for x in item.get('language', []) if x in languages]
    if len(langs) != 1:
        return None
    date = item.get('date', '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return None
    dt.date.fromisoformat(date)
    number = re.search(r'(?:No\.|ʻAdad|ʿAdad|‘Adad|Adad)\s*(\d+)', title)
    if not number:
        return None
    pdfs = [r['pdf'] for r in item.get('resources', []) if r.get('pdf')]
    if len(pdfs) != 1:
        return None
    request(pdfs[0])
    item_url = item.get('url') or item['id']
    item_url = item_url.replace('http://www.loc.gov/', 'https://www.loc.gov/')
    uid = item_url.rstrip('/').rsplit('/', 1)[-1]
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', uid):
        raise ValueError('Identifiant de notice invalide')
    return dict(id=uid, title=title, date=date, year=int(date[:4]),
                language=langs[0], number=int(number[1]), source='loc',
                notice_url=item_url, pdf_url=pdfs[0], version=VERSION)


def inventory(state, refresh=False):
    url = CATALOGUE
    seen = set()
    entries = {}
    excluded = 0
    while url:
        if url in seen:
            raise ValueError('Pagination répétée : inventaire incomplet')
        seen.add(url)
        data = fetch_json(url, state / 'cache', refresh)
        if not isinstance(data.get('results'), list):
            raise ValueError('Réponse catalogue inattendue')
        for item in data['results']:
            entry = entry_from_loc(item)
            if entry:
                entries[entry['id']] = entry
            else:
                excluded += 1
        print(f'Catalogue page {len(seen)} : {len(entries)} journaux lois/décrets retenus', flush=True)
        url = data.get('pagination', {}).get('next')
        if url:
            url = url.replace('http://www.loc.gov/', 'https://www.loc.gov/')
    result = dict(source=CATALOGUE, created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  complete_catalogue=True, archives_exhaustive=False, pages=len(seen),
                  excluded=excluded, entries=list(entries.values()))
    atomic_json(state / 'inventaire.json', result)
    return result


def verify_pdf(path):
    from pypdf import PdfReader
    with path.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ValueError('La réponse reçue n’est pas un PDF')
        stream.seek(0)
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted or not len(reader.pages):
            raise ValueError('PDF chiffré ou sans pages')
        pages = len(reader.pages)
        # Vérifier les objets de toutes les pages, sans OCR ni extraction des annonces.
        for page in reader.pages:
            _ = page.mediabox
    return pages


def relative_path(entry):
    # L'identifiant de notice préserve les variantes d'une même édition.
    return Path(str(entry['year'])) / entry['language'] / (
        f"JORT-lois-decrets_{entry['date']}_n{entry['number']:03d}_{entry['language']}_{entry['id']}.pdf")


def write_iort_list(state, entries, report, first, last, languages):
    rows = []
    by_issue = collections.defaultdict(set)
    for entry in entries:
        by_issue[entry['date'], entry['number']].add(entry['language'])
    for (date, number), available in sorted(by_issue.items()):
        for language in languages:
            if language not in available:
                rows.append(dict(annee=int(date[:4]), langue=language, numero=number,
                                 date=date, motif='edition_absente_du_catalogue_LOC',
                                 certitude='parution_dans_cette_langue_a_verifier'))
    for year in range(first, last + 1):
        for language in languages:
            if not any(e['year'] == year and e['language'] == language for e in entries):
                rows.append(dict(annee=year, langue=language, numero='', date='',
                                 motif='annee_a_inventorier_sur_IORT', certitude='couverture_inconnue'))
    lookup = {e['id']: e for e in entries}
    for error in report['errors']:
        entry = lookup[error['id']]
        rows.append(dict(annee=entry['year'], langue=entry['language'], numero=entry['number'],
                         date=entry['date'], motif='telechargement_LOC_en_echec', certitude='notice_connue'))
    path = state / 'a-completer-sur-iort.csv'
    pending = path.with_suffix('.csv.tmp')
    with pending.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['annee', 'langue', 'numero', 'date', 'motif', 'certitude'])
        writer.writeheader()
        writer.writerows(rows)
    os.replace(pending, path)


@contextmanager
def exclusive_lock(path):
    # Verrou Windows libéré par l'OS même après crash ; le fichier vide reste présent.
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    try:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Une collecte utilise déjà ce répertoire') from exc
        yield
    finally:
        stream.close()


def download(entry, destination, state, db, pause):
    final = destination / relative_path(entry)
    row = db.execute('SELECT sha256,path FROM downloads WHERE id=?', (entry['id'],)).fetchone()
    if row and final.exists() and digest(final) == row[0]:
        return 'reutilise'
    if final.exists():
        # Ne jamais écraser un original non reconnu par notre état durable.
        pages = verify_pdf(final)
        sha = digest(final)
        if row and sha != row[0]:
            raise ValueError('Empreinte modifiée : fichier conservé, contrôle manuel requis')
    else:
        staging = state / 'temporaires'
        staging.mkdir(parents=True, exist_ok=True)
        temp = staging / (entry['id'] + '.part')
        try:
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request(entry['pdf_url']), timeout=90) as response:
                        with temp.open('wb') as stream:
                            shutil.copyfileobj(response, stream, length=1024 * 1024)
                            stream.flush()
                            os.fsync(stream.fileno())
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise
                    time.sleep(min(int(exc.headers.get('Retry-After', '5')), 60))
                except (urllib.error.URLError, TimeoutError, http.client.HTTPException):
                    if attempt == 2:
                        raise
                    time.sleep(2 ** (attempt + 1))
            pages = verify_pdf(temp)
            sha = digest(temp)
            final.parent.mkdir(parents=True, exist_ok=True)
            pending = final.with_suffix('.pdf.part')
            shutil.copyfile(temp, pending)
            if digest(pending) != sha:
                raise OSError('Copie destination non conforme')
            os.replace(pending, final)
            if digest(final) != sha:
                raise OSError('Vérification finale de la destination échouée')
        finally:
            if temp.exists():
                temp.unlink()
        time.sleep(pause)
    proof = dict(entry, sha256=sha, bytes=final.stat().st_size, pages=pages,
                 path=str(final), checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                 validation='PDF lisible et copie vérifiée ; identité selon notice LOC',
                 drive_sync='Synchronisation distante Google Drive non vérifiée')
    atomic_json(final.with_suffix('.source.json'), proof)
    with db:
        db.execute('INSERT OR REPLACE INTO downloads VALUES (?,?,?,?)',
                   (entry['id'], sha, str(final), json.dumps(proof, ensure_ascii=False)))
    return 'telecharge'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path(r'H:\Mon Drive\JORT\journaux-officiels'))
    parser.add_argument('--etat', type=Path, default=ROOT / 'data' / 'collecte-officielle')
    parser.add_argument('--debut', type=int, default=1956)
    parser.add_argument('--fin', type=int, default=dt.date.today().year)
    parser.add_argument('--langues', nargs='+', choices=['ar', 'fr'], default=['ar', 'fr'])
    parser.add_argument('--inventaire', action='store_true', help='Inventorier seulement')
    parser.add_argument('--actualiser', action='store_true', help='Relire les pages du catalogue')
    parser.add_argument('--notice', action='append', default=[], help='Identifiant LOC pour un pilote ciblé')
    parser.add_argument('--limite', type=int, default=0, help='Nombre de fichiers traités ; 0 = tous')
    parser.add_argument('--pause', type=float, default=1)
    args = parser.parse_args(argv)
    if args.debut > args.fin or args.limite < 0 or args.pause < 0:
        parser.error('Période, limite ou pause invalide')
    state = args.etat.resolve()
    try:
        state.relative_to(ROOT.resolve())
    except ValueError:
        parser.error('Le suivi et les temporaires doivent rester sous le dossier orga de JORT')
    destination = args.destination.resolve()
    if not args.destination.is_absolute():
        parser.error('La destination doit être un chemin absolu')
    with exclusive_lock(state / 'collecte.lock'):
        if args.notice:
            entries = []
            for uid in args.notice:
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', uid):
                    parser.error('Identifiant de notice invalide')
                url = f'https://www.loc.gov/item/{uid}/?fo=json'
                data = fetch_json(url, state / 'cache', args.actualiser)
                item = dict(data['item'])
                item['resources'] = data.get('resources', [])
                entry = entry_from_loc(item)
                if not entry:
                    raise ValueError('Notice hors périmètre ou métadonnées insuffisantes : ' + uid)
                entries.append(entry)
            corpus = dict(entries=entries, complete_catalogue=False, archives_exhaustive=False)
        else:
            corpus = inventory(state, args.actualiser)
        entries = sorted((e for e in corpus['entries'] if args.debut <= e['year'] <= args.fin
                          and e['language'] in args.langues), key=lambda e: (e['date'], e['language'], e['id']))
        report = dict(source='Library of Congress', destination=str(destination),
                      archives_exhaustive=False, catalogue_complete=corpus['complete_catalogue'],
                      selected=len(entries), states={}, errors=[], annual_counts={},
                      missing_year_languages=[], note='Absence du catalogue LOC ≠ absence de parution. IORT non interrogé.')
        counts = collections.Counter((e['year'], e['language']) for e in entries)
        for year in range(args.debut, args.fin + 1):
            for lang in args.langues:
                report['annual_counts'][f'{year}/{lang}'] = counts[year, lang]
                if not counts[year, lang]:
                    report['missing_year_languages'].append(f'{year}/{lang}')
        atomic_json(state / 'rapport.json', report)
        write_iort_list(state, entries, report, args.debut, args.fin, args.langues)
        if args.inventaire:
            print(f'{len(entries)} PDF inventoriés. Rapport : {state / "rapport.json"}')
            return 0
        import pypdf  # Échec explicite avant tout téléchargement si dépendance absente.
        if not Path(destination.anchor).is_dir():
            raise OSError('Lecteur destination absent ou inaccessible : ' + destination.anchor)
        destination.mkdir(parents=True, exist_ok=True)
        # Empêcher aussi deux états locaux distincts d'écrire vers la même destination.
        with exclusive_lock(destination / 'collecte.lock'), closing(sqlite3.connect(state / 'reprise.sqlite')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS downloads (id TEXT PRIMARY KEY, sha256 TEXT, path TEXT, proof TEXT)')
            processed = entries[:args.limite] if args.limite else entries
            report['planned'] = len(processed)
            report['status'] = 'running'
            try:
                for index, entry in enumerate(processed, 1):
                    try:
                        status = download(entry, destination, state, db, args.pause)
                        report['states'][status] = report['states'].get(status, 0) + 1
                        print(f"{index}/{len(processed)} {entry['date']} {entry['language']} n°{entry['number']} : {status}", flush=True)
                    except (ValueError, urllib.error.URLError) as exc:
                        report['errors'].append(dict(id=entry['id'], error=str(exc)))
                        print('Échec :', entry['id'], str(exc), flush=True)
                        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429):
                            raise RuntimeError('Accès refusé ou débit limité : collecte arrêtée') from exc
                    atomic_json(state / 'rapport.json', report)
                report['status'] = 'finished_with_errors' if report['errors'] else 'finished'
            except KeyboardInterrupt:
                report['status'] = 'interrupted'
                raise
            except Exception as exc:
                report['status'] = 'stopped'
                report['fatal_error'] = str(exc)
                raise
            finally:
                atomic_json(state / 'rapport.json', report)
                write_iort_list(state, entries, report, args.debut, args.fin, args.langues)
        return 1 if report['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Arrêt demandé. Les PDF validés et le point de reprise sont conservés.', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f'Collecte arrêtée : {exc}', file=sys.stderr)
        sys.exit(1)
