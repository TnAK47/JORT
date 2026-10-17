"""Alléger les journaux sans perdre les preuves, serveur arrêté."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import os
import socket
from datetime import datetime, timezone
from collecter_2024 import save_json

ROOT=Path(__file__).resolve().parents[1]


def digest(stream):
    h=hashlib.sha256()
    for chunk in iter(lambda:stream.read(1024*1024),b''):
        h.update(chunk)
    return h.hexdigest()


def archive(keep=8):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8765)) == 0:
            raise RuntimeError('Arrêter JORT avant d’alléger les journaux.')
    paths=sorted((ROOT/'data/logs/executions').glob('[0-9]*.jsonl'), key=lambda p:p.stat().st_mtime, reverse=True)
    saved=0
    count=0
    for source in paths[keep:]:
        target=source.with_name(source.name+'.gz')
        temporary=source.with_name(source.name+'.compression.tmp')
        first,last={},{}
        with source.open('rb') as stream, gzip.open(temporary,'wb',compresslevel=6) as out:
            for line in stream:
                out.write(line)
                try: event=json.loads(line)
                except (json.JSONDecodeError, UnicodeError): continue
                if not first: first=event
                last=event
        with source.open('rb') as a, gzip.open(temporary,'rb') as b:
            if digest(a) != digest(b):
                temporary.unlink(missing_ok=True)
                raise ValueError('Archive différente du journal original ; suppression refusée.')
        summary=dict(id=source.stem,started_at=first.get('time'),last_at=last.get('time'),lane=first.get('lane'),
                     action=first.get('action'),years=first.get('years',[]),events=None,archived=True)
        save_json(source.with_name(source.stem+'.resume.json'),summary)
        os.replace(temporary,target)
        # Vérifier la cible résolue avant toute suppression locale.
        if not source.resolve().is_relative_to((ROOT/'data/logs/executions').resolve()):
            raise ValueError('Journal hors du répertoire autorisé.')
        saved+=source.stat().st_size-target.stat().st_size
        source.unlink()
        count+=1
    for source in sorted((ROOT/'data').glob('[0-9][0-9][0-9][0-9]/logs/evenements*.jsonl')):
        if source.stat().st_size == 0:
            continue
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        target=source.with_name(f'{source.stem}-archive-{stamp}.jsonl.gz')
        temporary=source.with_name(source.name+'.compression.tmp')
        with source.open('rb') as stream,gzip.open(temporary,'wb',compresslevel=6) as out:
            for chunk in iter(lambda:stream.read(1024*1024),b''): out.write(chunk)
        with source.open('rb') as a,gzip.open(temporary,'rb') as b:
            if digest(a) != digest(b):
                raise ValueError('Archive d’événements incohérente ; original conservé.')
        os.replace(temporary,target)
        if not source.resolve().is_relative_to((ROOT/'data').resolve()):
            raise ValueError('Événements hors du projet.')
        saved+=source.stat().st_size-target.stat().st_size
        source.unlink()
        count+=1
    report_path=ROOT/'data/maintenance/rapport-allegement-journaux.json'
    previous=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    result=dict(archived=count,retained_recent=min(keep,len(paths)),bytes_saved=saved,
                cumulative_bytes_saved=saved+previous.get('cumulative_bytes_saved',previous.get('bytes_saved',0)),evidence_preserved=True)
    save_json(report_path,result)
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep',type=int,default=8)
    args=parser.parse_args()
    if args.keep < 1: parser.error('Conserver au moins un journal récent.')
    archive(args.keep)
