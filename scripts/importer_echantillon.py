"""Import local uniquement : PDF sources, texte paginé, annonces candidates."""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from pypdf import PdfReader
try:
    from .texte_pdf import extract_page
    from .collecter_2024 import atomic_write_bytes
except ImportError:
    from texte_pdf import extract_page
    from collecter_2024 import atomic_write_bytes

ROOT = Path(__file__).resolve().parents[1]
VERSION = 4
MOIS = dict(zip(['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'], range(1,13)))

def importer(path, year=None, expected_date=None, expected_number=None):
    with path.open('rb') as source_stream:
        header=source_stream.read(8)
        source_stream.seek(0,os.SEEK_END)
        size=source_stream.tell()
        source_stream.seek(max(0,size-1024))
        footer=source_stream.read()
    if not header.startswith(b'%PDF-') or b'%%EOF' not in footer:
        raise ValueError(f'PDF non finalisé ou invalide : {path.name}')
    checksum=hashlib.sha256()
    with path.open('rb') as source_stream:
        for chunk in iter(lambda:source_stream.read(1024*1024),b''):
            checksum.update(chunk)
    digest=checksum.hexdigest()
    cache_key = f"{digest}-{year or 'auto'}-{expected_number or 'auto'}-{expected_date or 'auto'}"
    cache = ROOT / 'data' / 'imports' / f'{cache_key}.json'
    if cache.exists():
        try:
            result = json.loads(cache.read_text(encoding='utf-8'))
            if result.get('version') == VERSION:
                archived = ROOT / 'dist' / result['source']['pdf']
                if archived.exists():
                    archive_hash=hashlib.sha256()
                    with archived.open('rb') as archive_stream:
                        for chunk in iter(lambda:archive_stream.read(1024*1024),b''):
                            archive_hash.update(chunk)
                    if archive_hash.hexdigest()==digest:
                        return result
        except (OSError,json.JSONDecodeError,KeyError,TypeError,AttributeError):
            preserved=cache.with_name(cache.name+f'.corrompu-{time.time_ns()}')
            os.replace(cache,preserved)
    reader = PdfReader(path)
    texts, ocr_warnings = [], []
    for index in range(len(reader.pages)):
        page_text, page_warnings = extract_page(reader, index, path)
        texts.append(page_text)
        ocr_warnings.extend(page_warnings)
    if not texts:
        raise ValueError(f'PDF sans page : {path.name}')
    first = texts[0]
    warnings = list(dict.fromkeys(ocr_warnings))
    date_match = re.search(r'(\d{1,2})\s+('+'|'.join(MOIS)+r')\s+(1956|19\d{2}|20\d{2})\s+N[°º]\s*(\d+)', first, re.I)
    if date_match:
        day, month, parsed_year, number = date_match.groups()
        parsed_year = int(parsed_year)
        date = f'{parsed_year}-{MOIS[month.lower()]:02d}-{int(day):02d}'
    else:
        filename_match = re.search(r'(?<!\d)(195[6-9]|19[6-9]\d|20\d\d)(?:[-_](\d{1,4}))?', path.name)
        parsed_year = int(year) if year is not None else (int(expected_date[:4]) if expected_date else (int(filename_match[1]) if filename_match else None))
        number = expected_number if expected_number is not None else (int(filename_match[2]) if filename_match and filename_match[2] else None)
        date = expected_date
        warnings.append('Date, numéro ou texte OCR absents de la première page ; métadonnées reprises de l’inventaire si disponibles.')
    parsed_year = parsed_year or (int(expected_date[:4]) if expected_date else None)
    if parsed_year is None or not 1956 <= parsed_year <= __import__('datetime').datetime.now().year:
        raise ValueError(f'Année française 1956-année actuelle non reconnue : {path.name}')
    if year is not None and int(year) != parsed_year:
        raise ValueError(f'Année PDF {parsed_year} différente de l’année demandée {year}')
    if expected_date and date and expected_date != date:
        raise ValueError(f'Date PDF {date} différente de l’inventaire {expected_date}')
    if expected_number is not None and number is not None and int(expected_number) != int(number):
        raise ValueError(f'Numéro PDF {number} différent de l’inventaire {expected_number}')
    number = int(number) if number is not None else 0
    reference = re.compile(r'(?m)^\s*((?:195[6-9]|19[6-9]\d|20\d{2})[A-Z0-9]{6,})[ \t]*$')
    source_id = f'jal-fr-{parsed_year}-{number:03d}-{digest[:8]}'
    datepart = date or 'date-inconnue'
    filename = f'jal-{parsed_year}-{number:03d}-{datepart}-fr-{digest[:8]}.pdf'
    destination = ROOT / 'dist' / 'sources' / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor,temp_name=tempfile.mkstemp(prefix=f'.{destination.stem}-',suffix='.tmp',dir=destination.parent)
    try:
        with os.fdopen(descriptor,'wb') as destination_stream, path.open('rb') as source_stream:
            shutil.copyfileobj(source_stream,destination_stream,1024*1024)
            destination_stream.flush();os.fsync(destination_stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temp_name,destination)
                break
            except PermissionError:
                if attempt==4:raise
                time.sleep(.1*(attempt+1))
    finally:
        if os.path.exists(temp_name):os.unlink(temp_name)
    source = dict(id=source_id, number=int(number), date=date, pdf='sources/'+filename,
                  pages=len(texts), sha256=digest, original_filename=path.name,
                  language='fr', importer_version=VERSION, origin='Fichier local fourni ; URL directe non capturée')
    pages = []
    combined = ''
    spans = []
    for index, text in enumerate(texts, 1):
        printed = re.search(r'\bPage\s+(\d+)', text)
        pages.append(dict(page=index, printed_page=int(printed[1]) if printed else None, text=text))
        page_has_reference=bool(reference.search(text))
        cover = (re.search(r'journal officiel', text, re.I)
                 and re.search(r'\bsommaire\b', text, re.I)
                 and not re.search(r'raison\s+sociale|capital\s+social|fondateurs?|g[eé]rant|a\s+(?:vendu|c[eé]d[eé])', text, re.I))
        if index == 1 and not page_has_reference and cover:
            continue
        # Conserver l'original paginé ; ôter uniquement l'en-tête récurrent du découpage.
        body = '\n'.join(line for line in text.splitlines() if not ('Journal Officiel de la République Tunisienne' in line and re.search(r'Page\s+\d+', line)))
        start = len(combined)
        combined += body + '\n'
        spans.append((start, len(combined), index))
    announcements = []
    start = 0
    def append(end, reference):
        nonlocal start
        segment = combined[start:end]
        text = segment.strip(' \n\r_')
        content_start = start + len(segment) - len(segment.lstrip(' \n\r_'))
        if text:
            pnums = [p for a,b,p in spans if a < end and b > content_start]
            key = reference or 'sans-reference'
            announcements.append(dict(id=f'{source_id}:{key}:{len(announcements)+1}', reference=reference,
                source_id=source_id, source_date=date, text=text, pages=pnums, category='a_classer', status='a_verifier',
                warning='Découpage automatique aux références finales ; limites et catégorie à vérifier.'))
        start = end
    for match in reference.finditer(combined):
        append(match.end(), match[1])
    append(len(combined), None)
    low_quality_pages=[]
    for page in pages:
        text=page['text'] or ''
        replacement_count=text.count('\ufffd')
        page['text_chars']=len(text.strip())
        page['text_quality']='faible_a_verifier' if page['text_chars']<60 or replacement_count else 'texte_extrait'
        if page['text_quality']=='faible_a_verifier':
            low_quality_pages.append(page['page'])
    if any(not (page['text'] or '').strip() for page in pages):
        warnings.append('OCR ou texte absent sur une ou plusieurs pages ; contenu non récupéré.')
    if low_quality_pages:
        warnings.append('Texte court ou caractères de remplacement sur les pages '+', '.join(map(str,low_quality_pages))+' ; vérifier le rendu PDF et l’OCR.')
    if any(a['reference'] is None and len(a['text']) >= 300 and re.search(r'association|soci[eé]t[eé]', a['text'], re.I) for a in announcements):
        warnings.append('Fragments sans référence conservés ; découpage des annonces à vérifier.')
    warnings.extend(['Texte natif extrait ; ordre des colonnes et découpage non garantis.',
                     'Traduction française uniquement ; couverture annuelle non vérifiée.'])
    result = dict(version=VERSION, source=source, pages=pages, announcements=announcements, warnings=warnings)
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(cache,json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', nargs='+', type=Path)
    args = parser.parse_args()
    if len(args.pdf) > 3:
        parser.error('Ce prototype est limité à trois fichiers par exécution.')
    imports = {}
    for path in args.pdf:
        item = importer(path)
        imports[item['source']['sha256']] = item
    data = dict(sources=[v['source'] for v in imports.values()],
                announcements=[a for v in imports.values() for a in v['announcements']], entities=[], relations=[])
    # Relevés limités aux annonces inspectées ; personnes distinctes par annonce.
    samples = [
      ('2024T01234SODB1', 'TABARKA APIARIES', 'societe', [
       ('Karim Barouali','membre fondateur'),('Hadiya Khadhraoui','membre fondateur'),
       ('Sarra Barouali','membre fondateur'),('Badhis Yahiaoui','membre fondateur')],
       'Fondateurs :'),
      ('2024X01711APSF1', 'Association Eduque-moi pour les orphelins', 'association', [
       ('Ilhem Azazi','présidente'),('Amna Ennassar','secrétaire générale'),('Latifa Essbouy','trésorière')],
       'Commission :'),
      ('2024403067APSF1', 'Fawanis El Ghad', 'association', [
       ('Ammar Faouzia','président'),('Zitouni Fatma','secrétaire générale'),
       ('Kadre Lotfi','trésorier'),('Chouikh Roua','aide secrétaire générale')],
       "Les membres de l'association :")]
    for reference, name, category, people, quote_start in samples:
        matches = [a for a in data['announcements'] if a['reference'] == reference]
        for announcement in matches:
            aid = announcement['id']
            normalized = ' '.join(announcement['text'].split())
            if quote_start not in normalized or any(person not in normalized for person,role in people):
                raise ValueError(f'Relevé incompatible avec le texte source : {reference}')
            quote = normalized[normalized.index(quote_start):].removesuffix(reference).strip()
            announcement['category'] = category
            oid = aid+':organisation'
            data['entities'].append(dict(id=oid, name=name, type=category, announcement_id=aid))
            for i,(person,role) in enumerate(people):
                pid = aid+f':personne:{i}'
                data['entities'].append(dict(id=pid, name=person, type='personne', announcement_id=aid))
                data['relations'].append(dict(id=aid+f':relation:{i}', **{'from':pid,'to':oid}, role=role,
                    announcement_id=aid, quote=quote, status='releve_a_verifier'))
    output = ROOT / 'outputs' / 'echantillon'
    output.mkdir(parents=True,exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    atomic_write_bytes(output/'data.json',encoded)
    atomic_write_bytes(output/'data.js',b'window.JORT_DATA = '+encoded+b';\n')
    print(json.dumps(dict(sources=len(data['sources']), annonces_candidates=len(data['announcements']),
                         organisations=sum(e['type'] != 'personne' for e in data['entities']),
                         relations=len(data['relations'])), ensure_ascii=False))

if __name__ == '__main__':
    main()
