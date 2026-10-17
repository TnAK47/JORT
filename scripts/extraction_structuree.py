"""Compléments locaux : mentions, événements et objets, sans modifier les sources."""
import hashlib
import re
from datetime import date


def flat(value):
    return ' '.join(str(value or '').split())


def normalize_name(value):
    import unicodedata
    value=''.join(c for c in unicodedata.normalize('NFD',str(value).casefold()) if not unicodedata.combining(c))
    return re.sub(r'[^\w]+',' ',value).strip()


def organization_label(value):
    value=flat(value).strip(' «»".,;')
    value=re.sub(r'^.*?\b(?:Messieurs|Mesdames)(?:\s+et\s+Messieurs)?\s+les\s+(?:actionnaires|associ[eé]s)\s+(?:de\s+la\s+)?(?:soci[eé]t[eé]\s+)?','',value,flags=re.I)
    # « Société », « Sté » et « Ste » introduisent fréquemment une raison
    # sociale sans en faire partie. Les enlever ici évite de créer une seconde
    # fiche pour une même organisation, tout en laissant intactes les formes
    # juridiques comme « Société anonyme ».
    value=re.sub(r'^(?:la\s+)?(?:soci[eé]t[eé]|st[eé]\.?)\s+(?!anonyme\b|[àa]\s+responsabilit[eé]|unipersonnelle\b|communautaire\b)','',value,flags=re.I)
    return value.strip(' «»".,;')


def publication_type(text, sale=False):
    lead=flat(text[:1200])
    if re.search(r'\b(?:proc[eè]s[- ]?verbal|a\s+[eé]t[eé]\s+d[eé]cid[eé]|ont\s+d[eé]cid[eé])\b',lead,re.I):
        if sale:return 'cession_fonds'
        if re.search(r'augmentation|r[eé]duction\s+(?:du\s+)?capital',lead,re.I):return 'capital'
        return 'gouvernance' if re.search(r'nomination|renouvel|d[eé]mission|r[eé]vocation',lead,re.I) else 'decision'
    if re.search(r'^.{0,700}?\bconvocation\b|\b(?:actionnaires|associ[eé]s).{0,180}\b(?:invit[eé]s|convoqu[eé]s)\b',lead,re.I):return 'convocation'
    if sale:return 'cession_fonds'
    for kind,pattern in (
        ('constitution',r'constitution|cr[eé]ation\s+d[’\']'),
        ('dissolution',r'dissolution|liquidation'),
        ('rectificatif',r'rectificatif|erratum'),
        ('location_gerance',r'location[- ]g[eé]rance|g[eé]rance\s+libre'),
        ('capital',r'augmentation|r[eé]duction\s+(?:du\s+)?capital'),
        ('gouvernance',r'nomination|renouvel|d[eé]mission|r[eé]vocation'),
        ('procedure_judiciaire',r'vente\s+aux\s+ench[eè]res|adjudication|saisie'),
        ('comptes',r'[eé]tats\s+financiers|approbation\s+des\s+comptes'),
    ):
        if re.search(pattern,lead,re.I):return kind
    return 'publication_non_reconnue'


def dates_published(text):
    result={}
    pattern=re.compile(r'(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{4})')
    for kind,marker in [('acte',r'acte\b'),('assemblee',r'assembl[eé]e\b'),('conseil',r'conseil\s+d[’\']administration'),('enregistrement',r'enregistr[eé]')]:
        for m in re.finditer(marker,text,re.I):
            found=pattern.search(text,m.end(),min(len(text),m.end()+110))
            if not found:continue
            d,mo,y=map(int,re.split('[-/]',found['date']))
            try:value=date(y,mo,d).isoformat()
            except ValueError:continue
            result[kind]={'value':value,'quote':flat(text[m.start():found.end()])}
            break
    return result


def complement(announcements,entities,relations):
    # Imported lazily: extraire_entites calls this after its own extraction.
    from extraire_entites import ENTITY_EXTRACTION_VERSION,_ROLE,_valid_person,_cin_near,_event_date_evidence
    for a in announcements:
        aid=str(a['id']);text=str(a.get('text') or '');local=[e for e in entities if str(e['announcement_id'])==aid]
        for e in local:
            if e['type'] in ('societe','association','organisation'):
                e.setdefault('name_as_published',e['name']);e['canonical_name_candidate']=organization_label(e['name'])
        primary=next((e for e in local if e.get('is_primary') and e['type']!='fonds_commerce'),None)
        a['publication_type']=publication_type(text,any(e['type']=='fonds_commerce' for e in local))
        a['published_dates']=dates_published(text)
        a['search_visible']=a['publication_type']!='convocation'
        def party(name,kind='personne'):
            name=flat(name).strip(' «»".,;')
            if kind!='personne':name=organization_label(name)
            existing=next((e for e in local if e['type']==kind and normalize_name(e['name'])==normalize_name(name)),None)
            if existing:return existing
            eid=aid+':mention:'+hashlib.sha256((kind+'|'+normalize_name(name)).encode()).hexdigest()[:20]
            e=dict(id=eid,name=name,type=kind,announcement_id=aid,is_primary=False,entity_extraction_version=ENTITY_EXTRACTION_VERSION)
            local.append(e);entities.append(e);return e
        def link(e,to,role,event,quote,start,end):
            if any(r['announcement_id']==aid and r['from']==e['id'] and r['to']==to['id'] and r['role']==role and r.get('event_type')==event for r in relations):return
            when,evidence=_event_date_evidence(text,start,end)
            rid=aid+':fait:'+hashlib.sha256((e['id']+'|'+to['id']+'|'+role+'|'+event).encode()).hexdigest()[:20]
            family=('capital' if ('associ' in normalize_name(role) or 'fondateur' in normalize_name(role))
                    else 'direction' if 'directeur' in role or 'gerant' in role else 'administration')
            relations.append(dict(id=rid,**{'from':e['id'],'to':to['id']},role=role,type='role_organisation',event_type=event,event_date=when,event_date_quote=evidence,
                announcement_id=aid,quote=flat(quote),status='extrait_automatiquement',role_family=family))
        # Split within clauses. Representatives are subjects of their own fact,
        # never owners/sellers inferred from their signing a document.
        rx=re.compile(rf'(?P<action>nomination\s+de|nommer|d[eé]signation\s+de|renouvellement\s+(?:des\s+fonctions\s+)?de)\s+(?:Monsieur|Madame|Mr\.?|Mme\.?|M\.?)\s+(?P<name>[^.;:]{{2,100}}?)\s+(?:(?:repr[eé]sentant(?:e)?\s+de\s+(?:la\s+)?soci[eé]t[eé]\s+(?P<represented>[^.;:]{{2,130}}?))\s+)?(?:en\s+qualit[eé]\s+(?:de\s+|d[’\']\s*)|en\s+tant\s+que|comme)\s*(?P<role>{_ROLE})',re.I|re.S)
        if primary and a['publication_type']!='convocation':
            for m in rx.finditer(text):
                name=flat(m['name'])
                if not _valid_person(name):continue
                e=party(name);cin=_cin_near(text,m.start('name'),m.end('name'))
                if cin:e.update(cin=cin,identity_match_status='a_verifier')
                event='renewal' if m['action'].lower().startswith('renouvel') else 'appointment'
                role=flat(m['role']).lower();link(e,primary,role,event,m[0],m.start(),m.end())
                if m['represented']:
                    represented=party(m['represented'],'societe')
                    link(e,represented,'représentant','role_publie',m[0],m.start(),m.end())
        # Keep named persons even when the action/role is not recognized.
        mention=re.compile(r'\b(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?)\s+(?P<name>[A-ZÀ-ÖØ-Þ][\w’\'.-]+(?:\s+(?:(?:ben|bin|el|al|de)\s+)?[A-ZÀ-ÖØ-Þ][\w’\'.-]+){1,4})')
        for m in mention.finditer(text):
            name=flat(m['name'])
            if _valid_person(name):party(name)
        # Une liste d'associés fondateurs est une information explicite de
        # constitution : chaque personne relève bien d'un lien avec la société.
        # Le parseur s'arrête aux champs juridiques suivants et n'infère jamais
        # un lien à partir d'une simple cooccurrence dans l'annonce.
        if primary and a['publication_type']!='convocation':
            heading=re.compile(r'\b(?:associ[eé]s?\s*[- ]\s*fondateurs?|membres?\s*[- ]\s*fondateurs?|fondateurs?)\s*(?:sont\s*)?(?::|=|\b)',re.I)
            civil=re.compile(r"\b(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)\s+(?P<name>[A-ZÀ-ÖØ-Þ][\w’'.-]+(?:\s+(?:(?:ben|bin|bou|abou|el|al|ould|ouled|bint)\s+)?[A-ZÀ-ÖØ-Þ][\w’'.-]+){1,4})(?=\s*(?:[,;.])|\s+\bet\b|$)",re.I)
            stop=re.compile(r'(?:\n\s*\n|\b(?:capital|si[eè]ge\s+social|objet\s+social|dur[eé]e|apports?|forme\s+juridique)\s*[:：])',re.I)
            for marked in heading.finditer(text):
                tail=text[marked.end():marked.end()+1500]
                boundary=stop.search(tail)
                section=tail[:boundary.start() if boundary else len(tail)]
                people=[]
                for candidate in civil.finditer(section):
                    name=flat(candidate['name']).strip(' «»".,;')
                    if _valid_person(name):
                        people.append((name,marked.end()+candidate.start('name'),marked.end()+candidate.end('name')))
                # Certains textes donnent la liste sans civilité. Cela reste
                # acceptable uniquement sous l'intitulé explicite trouvé ici.
                if not people:
                    for raw in re.split(r'\s*(?:,|;|\bet\b)\s*',section):
                        name=re.sub(r'^(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)\s+','',flat(raw),flags=re.I).strip(' «»".,;')
                        if not _valid_person(name):
                            continue
                        position=text.find(raw,marked.end(),marked.end()+len(section))
                        if position>=0:
                            offset=position+max(0,raw.lower().find(name.lower()))
                            people.append((name,offset,offset+len(name)))
                for name,start,end in dict.fromkeys(people):
                    founder=party(name)
                    # L'extracteur général reconnaît déjà certaines listes de
                    # fondateurs. Ne pas publier deux fois la même preuve quand
                    # il a réussi ; ce complément couvre uniquement les listes
                    # qui lui échappaient.
                    if any(r['announcement_id']==aid and r['from']==founder['id'] and r['to']==primary['id']
                           and 'fondateur' in normalize_name(r.get('role','')) for r in relations):
                        continue
                    cin=_cin_near(text,start,end)
                    if cin:founder.update(cin=cin,identity_match_status='a_verifier')
                    link(founder,primary,'associé fondateur','constitution',text[marked.start():end],marked.start(),end)
        relevant=[r for r in relations if str(r['announcement_id'])==aid]
        if a['publication_type']=='convocation':
            for r in relevant:r['assertion_status']='propose'
        missing_role=bool(re.search(r'\bnomination\s+de\s+(?:Mr\.?|Monsieur|Madame|Mme\.?)',text,re.I)) and not any(r.get('event_type') in ('appointment','nomination') for r in relevant)
        a['extraction_quality']='partielle' if missing_role or (a.get('title_review_required') and len(text.strip())>80) else 'exploitable'
        a['quality_reasons']=['nomination_sans_role_reconnu'] if missing_role else ['designation_a_preciser'] if a.get('title_review_required') else []
        for e in local:
            quoted=e.get('name_as_published') or e['name']
            if e['type']=='personne':quoted=e['name']
            spans=re.search(r'\s+'.join(re.escape(w) for w in quoted.split()),text,re.I)
            if spans:
                e['evidence']={'start':spans.start(),'end':spans.end(),'quote':text[spans.start():spans.end()]}
                if e['type']=='personne' and e.get('cin'):
                    window=text[max(0,spans.start()-90):spans.end()+90]
                    for cin in re.finditer(r'\bC\.?\s*I\.?\s*N\.?\s*[:n°\s-]*(\d(?:[ .-]?\d){7})\b',window,re.I):
                        if re.sub(r'\D','',cin[1])==e['cin']:e['cin_quote']=cin[0];break
                if e['type'] in ('societe','association','organisation'):
                    stop=min(len(text),spans.end()+450)
                    for other in local:
                        if other is e:continue
                        other_name=other.get('name_as_published') or other['name']
                        found=re.search(r'\s+'.join(re.escape(w) for w in other_name.split()),text[spans.end():stop],re.I)
                        if found:stop=min(stop,spans.end()+found.start())
                    e['identifiers']=[]
                    for ident in re.finditer(r'\b(?P<label>MF(?:\s*/\s*IU)?|matricule\s+fiscal|identifiant\s+(?:fiscal|unique)|RNE|registre\s+(?:national\s+)?du\s+commerce|RC)\s*(?:n[°ºo.])?\s*[:=-]?\s*(?P<value>[A-Z0-9]{6,12}(?:\s*/\s*[A-Z0-9]{1,6}){0,4})\b',text[spans.end():stop],re.I):
                        label=ident['label'].lower()
                        kind='registre_commerce_publie' if label=='rc' or 'commerce' in label else 'identifiant_unique_publie' if 'unique' in label or label=='rne' else 'identifiant_fiscal_publie'
                        e['identifiers'].append(dict(kind=kind,value=re.sub(r'\s+','',ident['value']).upper(),quote=ident[0],attribution='explicite'))
                    if e['identifiers']:
                        first=e['identifiers'][0];e.update(identifier=first['value'],identifier_type=first['kind'],identifier_quote=first['quote'])
        if primary and a.get('title'):
            a['title']=organization_label(a['title']) if a.get('category')!='fonds_commerce' else a['title']
    return entities,relations
