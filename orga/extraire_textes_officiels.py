"""Extrait les textes page par page des PDF officiels déjà validés."""
import argparse, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from pypdf import PdfReader
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from texte_pdf import extract_page

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=Path(r'H:\Mon Drive\JORT\journaux-officiels'))
    p.add_argument('--sortie', type=Path, default=Path(__file__).parent/'data'/'extraction-officielle')
    args=p.parse_args()
    pdfs=list(args.source.rglob('*.pdf'))
    if not pdfs: raise SystemExit('Aucun PDF trouvé: '+str(args.source))
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'extractor':'texte_pdf.py','files':[]}
    for pdf in pdfs:
        rel=pdf.relative_to(args.source)
        target=args.sortie/rel.parent/(pdf.stem+'.txt')
        meta=args.sortie/rel.parent/(pdf.stem+'.extraction.json')
        target.parent.mkdir(parents=True,exist_ok=True)
        reader=PdfReader(str(pdf),strict=False); pages=[]; warnings=[]
        for i in range(len(reader.pages)):
            text, warns=extract_page(reader,i,path=pdf)
            pages.append(f'\n\n===== PAGE {i+1} =====\n{text}')
            warnings.extend(dict(page=i+1,message=w) for w in warns)
        payload=''.join(pages)
        target.write_text(payload,encoding='utf-8')
        sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        record={'source_pdf':str(pdf),'relative_pdf':str(rel),'text_file':str(target),
                'sha256':sha,'pages':len(reader.pages),'characters':len(payload),
                'warnings':warnings,'status':'a_verifier' if warnings else 'extrait'}
        meta.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        manifest['files'].append(record)
        print(f"{rel}: {len(reader.pages)} pages, {len(payload)} caractères, {len(warnings)} avertissements",flush=True)
    manifest['summary']={'files':len(pdfs),'pages':sum(x['pages'] for x in manifest['files']),
                         'warnings':sum(len(x['warnings']) for x in manifest['files'])}
    args.sortie.mkdir(parents=True,exist_ok=True)
    (args.sortie/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': main()
