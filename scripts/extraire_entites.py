"""Extraction prudente d'organisations et de rôles explicitement cités."""
import hashlib
import re
import unicodedata
from datetime import date
from qualite_entites import libelle_documentaire, nettoyer_libelle


_LABEL = re.compile(
    r"(?im)^\s*(?:[-•]\s*)?(?:le\s+)?(?:nom\s+(?:ou\s+raison\s+sociale|de\s+(?:la\s+)?(?:soci[eé]t[eé](?:\s+[àa]\s+constituer)?|l['’]entreprise(?:\s+[àa]\s+constituer)?|l['’]association))|"
    r"raison\s+sociale|nom|soci[eé]t[eé]|d[eé]nomination)\s*:\s*",
    re.I,
)
_NEXT_FIELD = re.compile(r"\s+(?:type\s+(?:(?:de\s+)?(?:la\s+)?soci.{0,3}t.{0,3}|d'entreprise)|capital(?:\s+souscrit)?\b|montant\s+du\s+capital|si.{0,3}ge\s+social|adresse\b|objet(?:\s+de\s+l'entreprise|\s+social)?\b|objectifs\b|date\b|identifiant\b|dur.{0,3}e\b|nombre\s+d'actions|forme\s+juridique)\s*:?", re.I)
ENTITY_EXTRACTION_VERSION = 11
TITLE_EXTRACTION_VERSION = 3
_ROLE = r"(?:commissaire\s+aux\s+comptes(?:\s+(?:titulaire|suppl[eé]ant))?|pr[eé]sident(?:e)?\s+directeur(?:trice)?\s+g[eé]n[eé]ral(?:e)?|directeur(?:trice)?\s+g[eé]n[eé]ral(?:e)?\s+adjoint(?:e)?|membre\s+fondateur|membre(?:\s+du\s+conseil(?:\s+d['’]administration)?)?|fondateur|fondatrice|vice\s*[-–]?\s*pr[eé]sident(?:e)?|pr[eé]sident(?:e)?(?:\s+du\s+conseil(?:\s+d['’]administration)?)?|directeur(?:trice)?(?:\s+g[eé]n[eé]ral(?:e)?)?|secr[eé]taire(?:\s+g[eé]n[eé]rale?)?|tr[eé]sori[eè]r(?:e|e)?|g[eé]rant(?:e)?|liquidateur(?:trice)?|repr[eé]sentant(?:e)?\s+l[eé]gal(?:e)?|administrateur(?:trice)?(?:\s+d[eé]l[eé]gu[eé](?:e)?)?)"
_PERSON = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?:ben|bin|bou|abou|el|al|ould|ouled|bint)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4}$")

# Act headings may wrap across several PDF lines, or share the last line
# with the company name. Match complete legal phrases, never a bare name
# prefix: a company called "Nomination Services" must remain unchanged.
_HEADING_OFFICE = rf"(?:commissaire\s+aux\s+comptes|{_ROLE})"
_ACT_HEADING = re.compile(
    rf"(?im)^\s*(?:"
    rf"(?:[eé]lection|nomination|d[eé]signation|r[eé]vocation|d[eé]mission)\s+(?:du|des|de\s+(?:la|l['’]|un|une)|d['’]un(?:e)?)\s*{_HEADING_OFFICE}"
    rf"|renouvellement\s+(?:de|des)\s+mandats?\s+d['’]administrateurs?"
    rf"|reconduction\s+des\s+fonctions\s+d['’]un(?:e)?\s+{_HEADING_OFFICE}"
    rf")(?:\s+et\s+(?:nomination|d[eé]signation|reconduction\s+des\s+fonctions)\s+(?:du|des|de\s+(?:la|l['’]|un|une)|d['’]un(?:e)?)\s*{_HEADING_OFFICE})*\b",
    re.I,
)


def _norm(text):
    return " ".join(str(text or "").split())


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _organisation(text):
    # Prefer a clearly labeled name, stopping at the next known field even when OCR omitted its colon.
    for match in _LABEL.finditer(text):
        end_match = _NEXT_FIELD.search(text, match.end())
        candidate = text[match.end():end_match.start() if end_match else match.end()+180]
        if not end_match:
            kept = []
            for line in candidate.splitlines()[:3]:
                line = line.strip(" .-–—")
                if not line or (kept and re.match(r"^(?:une?\s+association|il\s+|les\s+|l'assembl[eé]e|nomination|r[eé]vocation|suivant|conform[eé]ment)\b", line, re.I)):
                    break
                kept.append(line)
            candidate = " ".join(kept)
        candidate = re.split(r"[;]", candidate, maxsplit=1)[0].strip(" .-–—\n")
        candidate = _norm(candidate)
        if 2 <= len(candidate) <= 150 and len(candidate.split()) <= 18:
            return candidate

    # Keep the original company heading. Removing only fragments of an act
    # heading used to leave "et d'un Directeur Général" in the company name.
    lines = [_norm(line).strip(" .-–—") for line in text.splitlines() if _norm(line).strip(" .-–—")]
    legal_form = re.compile(r"(?:^|[\s(])(?:s\s*\.?\s*u\s*\.?\s*a\s*\.?\s*r\s*\.?\s*l\s*\.?|s\s*\.?\s*a\s*\.?(?:\s*r\s*\.?\s*l\s*\.?)?|soci[eé]t[eé]\s+(?:anonyme|unipersonnelle\s+[àa]\s+responsabilit[eé]\s+limit[eé]e|[àa]\s+responsabilit[eé]\s+limit[eé]e))(?=$|[\s).,;:'’\"»])", re.I)
    ignored = re.compile(r"^(?:notice|r[eé]alisation|augmentation|constitution|avis|gestion\s+de\s+soci[eé]t[eé]s?|st[eé]s?\s+unipersonnelles\s+[àa]\s+responsabilit[eé]\s+limit[eé]e|dissolution|r[eé]duction|convocation|changement|rabaissement|nomination|renouvellement|d[eé]mission|r[eé]vocation|d[eé]cision|transfert|avis\s+de|partie\s+poursuivante|report\s+de|g[eé]n[eé]rale\s+ordinaire)\b", re.I)
    office_heading = re.compile(rf"^(?:(?:des?|du|et|d['’]un)\s+)*(?:{_ROLE}|g[eé]n[eé]ral|conseil\s+d['’]administration)(?:\s*[-–]\s*du\s+commissaires?\s+aux\s+comptes)?$", re.I)
    section_heading = re.compile(r"^soci[eé]t[eé]s?(?:\s+anonymes?)?$", re.I)
    field = re.compile(r"^(?:adresse|si[eè]ge|mf|identifiant|num[eé]ro|capital|objet|type|forme)\b", re.I)
    for i, line in enumerate(lines):
        form = legal_form.search(line)
        if not form or i > 16:
            continue
        prior = []
        for candidate in lines[max(0, i-5):i]:
            if candidate.startswith("(") and candidate.endswith(")"):
                continue
            if ignored.search(candidate) or office_heading.fullmatch(candidate) or section_heading.fullmatch(candidate) or field.search(candidate):
                prior = []
            else:
                prior.append(candidate)
        if form and form.start() > 0:
            same_line = re.sub(r"\([^)]*\)", " ", line[:form.start()]).strip(" .-–—\"'«»")
            same_line = _ACT_HEADING.sub('', same_line).strip(" .-–—\"'«»")
            if same_line and not field.search(same_line):
                prior.append(same_line)
        candidate = " ".join(prior[-5:]).strip(" .-–—\"'«»")
        single_caps_name = len(candidate.split()) == 1 and candidate.isupper() and candidate.isalpha()
        if 2 <= len(candidate) <= 150 and len(candidate.split()) <= 18 and candidate[:1].isupper() and (len(candidate.split()) >= 2 or bool(form and form.start() > 0) or single_caps_name):
            return candidate
    return None


def nom_entite_entete(announcement):
    """Récupère prudemment un nom en tête d'annonce suivi d'un identifiant.

    Ce motif permet notamment de reconnaître un syndic ou organisme dont le
    nom ne porte pas une forme juridique explicite. Sans identifiant proche,
    la première ligne peut être un titre éditorial : on ne la transforme pas
    en entité.
    """
    lines = [line.strip(' \t .–—') for line in str(announcement.get('text') or '').splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    name = lines[0].strip('«»\"\'')
    context = ' '.join(lines[1:5])
    if not re.search(r'\b(?:MF(?:\s*/\s*IU)?|matricule\s+fiscal|identifiant\s+(?:unique|fiscal)|RNE|registre\s+national\s+des\s+entreprises)\b', context, re.I):
        return None
    if not (3 <= len(name) <= 120 and len(name.split()) <= 14):
        return None
    if re.search(r'\d{4}|\b(?:annonces?\s+l[eé]gales?|soci[eé]t[eé]s?\s+anonymes?|journal\s+officiel|r[eé]alisation|avis)\b', name, re.I):
        return None
    return name

def _kind(announcement, text, name):
    reference = str(announcement.get("reference") or "").upper()
    if re.search(r"(?:SANB|SRLB|SRUB|SODB)\d*$", reference):
        return "societe"
    if re.search(r"APSF\d*$", reference) and re.search(r"(?:constitution|homologation)[^\n]{0,100}association|association[^\n]{0,100}(?:constitution|homologation)", text, re.I):
        return "association"
    lead = " ".join(text.splitlines()[:8])
    if re.search(r"(?:constitution|cr[eé]ation)\s+d['’ ]?(?:une?\s+)?soci[eé]t[eé]|soci[eé]t[eé]\s+(?:anonyme|[àa]\s+responsabilit[eé]\s+limit[eé]e)", lead, re.I):
        return "societe"
    if re.search(r"(?:constitution|cr[eé]ation)\s+d['’ ]?(?:une?\s+)?association", lead, re.I) and name and _fold(name).startswith("association "):
        return "association"
    return None


def _unique_identifier(text, name=None):
    """Lit un identifiant explicitement étiqueté près du nom de l'organisation."""
    if name:
        name_pattern = r"\s+".join(re.escape(part) for part in name.split())
        named = re.search(name_pattern, text, re.I)
        if not named:
            return None
        text = text[named.start():named.start() + 700]
    match = re.search(
        r"\b(?:identifiant\s+unique|I\s*\.?\s*U\s*\.?)\s*(?:n[°ºo.]\s*)?[:=\-]?\s*([A-Z0-9]{6,12})\b",
        text, re.I)
    return match.group(1).upper() if match else None


def _published_identifier(text, name=None):
    """Retient le type et la valeur explicitement publiés, sans inventer d'ID."""
    if name:
        match = re.search(r"\s+".join(re.escape(part) for part in name.split()), text, re.I)
        if not match:
            return {}
        text = text[match.start():match.start() + 700]
    for label, kind in (
        (r"identifiant\s+unique|I\s*\.?\s*U\s*\.?|RNE", 'identifiant_unique_publie'),
        (r"MF(?:\s*/\s*IU)?|matricule\s+fiscal|identifiant\s+fiscal", 'identifiant_fiscal_publie'),
        (r"registre\s+(?:national\s+)?du\s+commerce|R\s*\.?\s*C\s*\.?", 'registre_commerce_publie'),
    ):
        match = re.search(rf"\b(?:{label})\s*(?:n[°ºo.]\s*)?[:=\-]?\s*([A-Z0-9]{{6,12}}(?:\s*/\s*[A-Z0-9]{{1,4}})?)\b", text, re.I)
        if match:
            return dict(identifier=re.sub(r'\s+', '', match.group(1)).upper(), identifier_type=kind,
                        identifier_quote=_norm(match.group(0)))
    return {}


def famille_role(role):
    value = _fold(role)
    if 'commissaire' in value or 'auditeur' in value:
        return 'controle'
    if 'directeur' in value or 'gerant' in value:
        return 'direction'
    if 'administrateur' in value or 'president' in value or 'conseil' in value:
        return 'administration'
    return 'autre'


def _valid_person(name):
    if libelle_documentaire(name,'personne'):return False
    name = _norm(name).strip(" ,.;:-")
    return bool(_PERSON.fullmatch(name)) and not re.search(r"\d|\b(?:association|soci[eé]t[eé]|st[eé]|SARL|SUARL|identifiant|fiscal[e]?|monsieur|madame|fondateurs?|cin|titulaire|demeurant|l'ensemble|fonds|commerce)\b", name, re.I)


def _valid_commerce_person(name):
    name = _norm(name).strip(' ,.;:-')
    parts = name.split()
    excluded = r"\b(?:soci[eé]t[eé]|st[eé]|SARL|SUARL|identifiant|fiscal[e]?|CIN|titulaire|demeurant|l'ensemble|fonds|commerce|totalit[eé]|sis|situ[eé]|avenue|rue|place|local|opposition|acheteur|vendeur|journal|JORT)\b"
    return (2 <= len(parts) <= 9
            and all(re.fullmatch(r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+", part) for part in parts)
            and not re.search(excluded, name, re.I))


def _cin_near(text, start, end, other_people=(), *, scope=None):
    """Retourne une CIN explicite proche du nom ; aucune déduction par le nom seul."""
    left, right = max(0, start - 90), min(len(text), end + 90)
    if scope:
        left, right = max(left, scope[0]), min(right, scope[1])
    window = text[left:right]
    matches = list(re.finditer(
        r"(?:C\s*\.\s*I\s*\.\s*N\s*\.?|CIN|carte\s+d['’]?identit[eé]\s+nationale)"
        r"\s*(?:n[°ºo.]\s*)?[:n°º-]?\s*(\d(?:[ .-]?\d){7})",
        window, re.I))
    if not matches:
        return None
    person_start, person_end = start - left, end - left
    # Une CIN proche n'est qu'un candidat : ne l'attribuer que si le passage
    # ne contient pas une autre personne aussi proche de ce numéro.
    candidates = []
    for match in matches:
        distance = max(person_start - match.end(), match.start() - person_end, 0)
        bridge = window[min(person_end, match.end()):max(person_start, match.start())]
        if re.search(r"\b(?:a\s+(?:vendu|c[eé]d[eé])|soci[eé]t[eé]|st[eé]|identifiant|Monsieur|Madame|Mr|Mme)\b", bridge, re.I):
            continue
        if distance <= 24:
            candidates.append((distance, match))
    if len(candidates) != 1:
        return None
    distance, match = candidates[0]
    if distance > 24:
        return None
    for other_start, other_end in other_people:
        other_start = other_start - left
        other_end = other_end - left
        other_distance = max(other_start - match.end(), match.start() - other_end, 0)
        if other_distance <= distance:
            return None
    return re.sub(r"\D", "", match.group(1))


def _event_date_evidence(text, start, end):
    """Ne lit une date d'effet que lorsqu'un marqueur explicite la précède."""
    left = max(text.rfind(separator, 0, start) for separator in ('.', ';', '\n')) + 1
    right = min((position for separator in ('.', ';', '\n')
                 if (position := text.find(separator, end)) >= 0), default=len(text))
    context = text[max(left, start - 100):min(right, end + 200)]
    marker = re.search(r"(?:à\s+compter\s+du|prend\s+effet\s+le|effet\s+à\s+partir\s+du|à\s+partir\s+du)\s+(.{0,40})", context, re.I)
    if not marker:
        return None, None
    value = marker.group(1)
    numeric = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)", value)
    if numeric:
        day, month, year = map(int, numeric.groups())
        try:
            return date(year, month, day).isoformat(), _norm(context[marker.start():marker.start(1)+numeric.end()])
        except ValueError:
            return None, None
    months = {'janvier':1,'février':2,'fevrier':2,'mars':3,'avril':4,'mai':5,'juin':6,'juillet':7,'août':8,'aout':8,'septembre':9,'octobre':10,'novembre':11,'décembre':12,'decembre':12}
    named = re.search(r"(?<!\d)(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})(?!\d)", value, re.I)
    if named and named.group(2).casefold() in months:
        try:
            return date(int(named.group(3)), months[named.group(2).casefold()], int(named.group(1))).isoformat(), _norm(context[marker.start():marker.start(1)+named.end()])
        except ValueError:
            return None, None
    return None, None


def _event_date(text, start, end):
    return _event_date_evidence(text, start, end)[0]


def _commerce_party(text, start, end, before):
    """Repère une partie nommée près de l'acte de vente, sans inventer de partie."""
    segment_start = max(0, end - 900) if before else start
    segment_end = end
    segment = text[segment_start:segment_end]
    introducer = r"(?:\bà\s+(?:la\s+)?|\bau\s+profit\s+de\s+|\bau\s+)" if not before else r""
    companies = list(re.finditer(
        introducer + r"\b(?:soci[eé]t[eé]|ST[EÉ]\.?)\s+(?P<name>[^;:]{2,220})", segment, re.I))
    if companies:
        match = companies[-1] if before else companies[0]
        raw = match.group('name')
        # Le nom doit se terminer avant un champ de l'acte ou une forme juridique.
        stop = re.search(r"\s+(?:identifiant\b|matricule\s+fiscal|M\.?\s*F\.?\b|I\.?\s*U\.?\b|CIN\b|dont\b|ayant\b|repr[eé]sent[eé]e?\b|sise?\b|situ[eé]e?\b|adresse\b|si[eè]ge\b|immatricul[eé]e?\b|la\s+totalit[eé]\b|l['’]ensemble\b|un\s+fonds\b|le\s+fonds\b|S\s*\.?\s*U\s*\.?\s*A\s*\.?\s*R\s*\.?\s*L\b|S\s*\.?\s*A\s*\.?\s*R\s*\.?\s*L\b|S\s*\.?\s*A\b)|[,;]|\.\s+(?=[A-ZÀ-ÖØ-Þ])", raw, re.I)
        raw_name = raw[:stop.start()] if stop else raw
        # Une société et une personne sont deux vendeurs, pas une raison sociale.
        raw_name = re.split(r"\s+et\s+(?=(?:Monsieur|Madame|Mr\.?|Mme\.?)\s+|[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+\s+[A-ZÀ-ÖØ-Þ])", raw_name, maxsplit=1)[0]
        raw_name = raw_name.strip(' .-–—,«»"')
        name = _norm(raw_name)
        # Les formes descriptives seules ne constituent pas une raison sociale.
        if (2 <= len(name) <= 150 and len(name.split()) <= 18
                and not re.match(r"(?:anonyme|unipersonnelle|[àa]\s+responsabilit[eé]|entrepreneur\s+physique)\b", name, re.I)):
            offset = match.start('name') + raw.find(raw_name)
            return dict(name=name, type='societe', start=segment_start + offset,
                        end=segment_start + offset + len(raw_name), scope=(segment_start, segment_end))
        return None
    persons = list(re.finditer(
        introducer + r"(?i:Monsieur|Madame|Mr\.?|Mme\.?|M\s*\.?|Mlle\.?)\s+"
        r"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,8})",
        segment))
    if persons:
        match = persons[-1] if before else persons[0]
        raw_name = re.split(r"\.\s+(?=[A-ZÀ-ÖØ-Þ])", match.group('name'), maxsplit=1)[0]
        raw_name = re.split(r"\s+(?:C\s*\.?\s*I\s*\.?\s*N\s*\.?|carte\s+d['’]?identit[eé]\s+nationale|titulaire|demeurant|r[eé]sidant|identifiant)\b", raw_name, maxsplit=1, flags=re.I)[0].strip(' ,.;:-')
        name = _norm(raw_name)
        if _valid_person(name) or _valid_commerce_person(name):
            offset = match.start('name') + match.group('name').find(raw_name)
            return dict(name=name, type='personne', start=segment_start + offset, end=segment_start + offset + len(raw_name), scope=(segment_start, segment_end))
    bare_name = r"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,8})"
    if before:
        bare = re.search(bare_name + r"\s*$", segment)
    else:
        bare = re.search(introducer + bare_name, segment)
    if bare:
        raw_name = re.split(r"\.\s+(?=[A-ZÀ-ÖØ-Þ])", bare.group('name'), maxsplit=1)[0]
        raw_name = re.split(r"\s+(?:C\s*\.?\s*I\s*\.?\s*N\s*\.?|carte\s+d['’]?identit[eé]\s+nationale|titulaire|demeurant|r[eé]sidant|identifiant)\b", raw_name, maxsplit=1, flags=re.I)[0].strip(' ,.;:-')
        name = _norm(raw_name)
        if _valid_person(name) or _valid_commerce_person(name):
            offset = bare.start('name') + bare.group('name').find(raw_name)
            return dict(name=name, type='personne', start=segment_start + offset, end=segment_start + offset + len(raw_name), scope=(segment_start, segment_end))
    return None


def _governance_mentions(text):
    """Lit des fonctions explicites et des listes, bornées à leur clause."""
    civil = r"(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)"
    mentions = []
    for heading in re.finditer(r"\badministrateurs?\s+suivants\b", text, re.I):
        start = heading.end()
        section = text[start:start + 1800]
        colon = re.search(r'^[^:]{0,140}:', section)
        if colon:
            start += colon.end()
            section = section[colon.end():]
        boundary = re.search(r"(?:\bet\s+de\s+)?\b(?:nommer|d[eé]signer)\b|\.\s+(?:Il|En|Deux|Le|Les)\b|\n\s*\d{4}[A-Z0-9]+", section)
        if boundary:
            section = section[:boundary.start()]
        event = 'renewal' if re.search(r'renouvel|recondu', text[max(0, heading.start()-160):heading.start()], re.I) else 'role_publie'
        # Les JORT utilisent parfois une civilité collective : « Messieurs A,
        # B et C ». Le titre « administrateurs suivants » apporte alors le
        # rôle à chacun, sans créer de lien depuis une simple cooccurrence.
        plural = re.search(r"\b(?:Messieurs|Mesdames)\s+(?P<names>[^.;:]{2,900})", section, re.I | re.S)
        if plural:
            listed = plural.group('names')
            stop = re.search(r"\b(?:et\s+)?(?:la|le)\s+soci[eé]t[eé]\b", listed, re.I)
            if stop:
                listed = listed[:stop.start()]
            cursor = 0
            for raw in re.split(r"\s*(?:,|\bet\b)\s*", listed, flags=re.I):
                name = _norm(re.sub(rf"^(?:{civil})\s+", "", raw, flags=re.I)).strip(' .;')
                if not _valid_person(name):
                    continue
                position = listed.casefold().find(raw.casefold(), cursor)
                if position < 0:
                    continue
                cursor = position + len(raw)
                name_offset = raw.casefold().find(name.casefold())
                name_start = start + plural.start('names') + position + max(0, name_offset)
                name_end = name_start + len(name)
                mentions.append(dict(name=name, type='personne', role='administrateur', start=name_start,
                                     end=name_end, event_type=event,
                                     quote=_norm(text[heading.start():name_end])))
        for item in re.finditer(r"(?:^|,|\bet\b)\s*(?P<item>.*?)(?=,|\bet\b|$)", section, re.I | re.S):
            raw = item.group('item').strip(' \n\t.;-•')
            member = re.match(rf"(?:(?:la|le)\s+)?(?P<kind>soci[eé]t[eé]|{civil})\s+(?P<name>.+)", raw, re.I | re.S)
            if not member:
                continue
            name = _norm(member.group('name')).strip(' .;')
            kind = 'societe' if _fold(member.group('kind')) == 'societe' else 'personne'
            if (kind == 'personne' and not _valid_person(name)) or not (2 <= len(name) <= 150):
                continue
            offset = item.group('item').find(raw) + member.start('name')
            name_start = start + item.start('item') + offset
            name_end = name_start + len(member.group('name').rstrip(' \n\t.;'))
            mentions.append(dict(name=name, type=kind, role='administrateur', start=name_start,
                                 end=name_end, event_type=event, quote=_norm(text[heading.start():name_end])))
    # Le rôle peut être publié avant une liste : « nomination de deux nouveaux
    # administrateurs, en l'occurrence Mr X et Mr Y ». La liste reste bornée à
    # cette décision et chaque nom doit conserver sa propre preuve.
    collective = re.compile(
        r"\b(?P<action>nomination|d[eé]signation|renouvellement)\s+(?:de\s+)?"
        r"(?:(?:\d+|un|une|deux|trois|quatre|cinq|plusieurs)\s+)?(?:nouveaux?\s+|nouvelles?\s+)?"
        r"(?P<role>administrateurs?|commissaires?\s+aux\s+comptes)\b(?P<tail>.{0,700})", re.I | re.S)
    named = re.compile(
        rf"\b{civil}\s+(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){{1,4}})")
    for action in collective.finditer(text):
        tail = action.group('tail')
        boundary = re.search(r"(?:[.;]|\n\s*(?:\d+[-)]|[A-Z0-9]{4,}))", tail)
        if boundary:
            tail = tail[:boundary.start()]
        role = _fold(action.group('role'))
        role = 'administrateur' if role.startswith('administrateur') else 'commissaire aux comptes'
        event = 'renewal' if _fold(action.group('action')).startswith('renouvel') else 'appointment'
        for person in named.finditer(tail):
            name = _norm(person.group('name'))
            if not _valid_person(name):
                continue
            name_start = action.start('tail') + person.start('name')
            name_end = action.start('tail') + person.end('name')
            mentions.append(dict(name=name, type='personne', role=role, start=name_start,
                                 end=name_end, event_type=event,
                                 quote=_norm(text[action.start():name_end])))
    # In prose the role can precede the name: "le mandat du Directeur Général Mr X".
    role_first = re.compile(
        rf"(?:(?:le\s+)?mandat\s+(?:du|de\s+la|de)|(?:les\s+)?fonctions\s+(?:du|de\s+la|de))\s+"
        rf"(?P<role>{_ROLE})\s+{civil}\s+(?P<name>[^,.;:]{{2,110}}?)"
        r"(?=\s+(?:pour|et\s+(?:le|la|de)|qui|durant|en\s+tant|à\s+compter)\b|[,.;:]|$)", re.I)
    for match in role_first.finditer(text):
        name = _norm(match.group('name'))
        if not _valid_person(name):
            continue
        context = text[max(0, match.start()-150):match.start()]
        actions = list(re.finditer(r'renouvel\w*|recondu\w*|nomm\w*|nomination|d[eé]mission|r[eé]vocation', context, re.I))
        action = _fold(actions[-1].group()) if actions else ''
        event = ('renewal' if action.startswith(('renouvel', 'recondu')) else
                 'appointment' if action.startswith(('nomm', 'nomination')) else
                 'resignation' if action == 'demission' else 'revocation' if action == 'revocation' else 'role_publie')
        quote_start = max(0, match.start()-150) + actions[-1].start() if actions else match.start()
        mentions.append(dict(name=name, type='personne', role=_norm(match.group('role')).casefold(),
                             start=match.start('name'), end=match.end('name'), event_type=event,
                             quote=_norm(text[quote_start:match.end()])))
    return mentions


def _relations(text, aid, org_id, entities):
    """Return links only where role and plausible personal name share a short phrase."""
    found = []
    patterns = [
        # Forme fréquente des annonces de constitution : « Gérance :
        # Monsieur X est nommé gérant ».
        (re.compile(r"g[eé]rance\s*:\s*(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)?\s*(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4})\s+(?:est|a\s+[eé]t[eé]|sera)\s+(?:nomm[eé]|d[eé]sign[eé]|[eé]lu(?:e)?)\s+(?:nouveau\s+|nouvelle\s+)?(?P<role>g[eé]rant(?:e)?)", re.I), "appointment"),
        # Les noms sans civilité sont admis uniquement si l'action et le rôle
        # figurent dans la même phrase : « Fathi Ghrissi est nommé gérant ».
        (re.compile(r"(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)?\s*(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4})\s+(?:est|a\s+[eé]t[eé]|sera)\s+(?:nomm[eé]|d[eé]sign[eé]|[eé]lu(?:e)?)\s+(?:nouveau\s+|nouvelle\s+)?(?P<role>" + _ROLE + r")", re.I), "appointment"),
        (re.compile(rf"(?P<role>{_ROLE})\s+(?:de\s+l'association\s*:?)?\s*:\s*(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){{1,3}})", re.I), "role_publie"),
        (re.compile(rf"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){{1,3}})\s*:\s*(?P<role>{_ROLE})(?:\s+de\s+l'association)?", re.I), "role_publie"),
        (re.compile(rf"(?:nomination|nommer|d[eé]signation|d[eé]signer|[eé]lection|[eé]lire)\s+(?:de\s+)?(?:Monsieur|Madame|Mr\.?|Mme\.?|M\.?|Mlle\.?)?\s*(?P<name>[^,.;:]{{2,110}}?)\s+(?:en\s+tant\s+que|en\s+qualit.{{0,2}}\s+d['’]?|au\s+poste\s+de|aux\s+fonctions\s+de|comme)\s*(?:nouvel(?:le)?\s+|nouveau\s+)?(?P<role>{_ROLE})", re.I), "appointment"),
        (re.compile(rf"(?:reconduction(?:\s+des\s+fonctions)?|renouvellement)\s+(?:(?:du|de|des)\s+)?(?:mandats?\s+(?:du|de|des)\s+)?(?:Monsieur|Madame|Mr\.?|Mme\.?|M\.?|Mlle\.?)?\s*(?P<name>[^,.;:]{{2,110}}?)\s+(?:en\s+tant\s+que|en\s+(?:sa\s+)?qualit.{{0,2}}\s+d['’]?|au\s+poste\s+de|aux\s+fonctions\s+de|comme)\s*(?P<role>{_ROLE})", re.I), "renewal"),
        (re.compile(rf"d[eé]mission\s+(?:de\s+)?(?:Monsieur|Madame|Mr\.?|Mme\.?|M\.?|Mlle\.?)?\s*(?P<name>[^,.;:]{{2,110}}?)\s+de\s+ses\s+fonctions\s+d['’]?\s*(?P<role>{_ROLE})", re.I), "resignation"),
        (re.compile(rf"(?:r[eé]vocation|destitution)\s+(?:de\s+)?(?:Monsieur|Madame|Mr\.?|Mme\.?|M\.?|Mlle\.?)?\s*(?P<name>[^,.;:]{{2,110}}?)\s+de\s+(?:son\s+poste|ses\s+fonctions)\s+d['’]?\s*(?P<role>{_ROLE})", re.I), "revocation"),
    ]
    # Founder lists are explicit role evidence; split only the local list before its terminator.
    for m in re.finditer(r"(?:les\s+)?fondateurs\s*:\s*(.{2,500})", text, re.I):
        phrase = m.group(1).split(".", 1)[0]
        cursor = 0
        for item in re.split(r",|\bet\b", phrase, flags=re.I):
            item_start = phrase.casefold().find(item.casefold(), cursor)
            if item_start < 0:
                continue
            cursor = item_start + len(item)
            person = re.sub(r"\s+resident\b.*$", "", item, flags=re.I).strip(" /-\n")
            if _valid_person(person):
                person_offset = item.find(person)
                if person_offset < 0:
                    continue
                person_start = item_start + person_offset
                found.append((person, "membre fondateur", m.start(1) + person_start, m.start(1) + person_start + len(person), "nomination"))
    evidence_quotes = {}
    for rx, event_type in patterns:
        for m in rx.finditer(text):
            raw_name = re.split(r'\s+CIN\b', m.group('name'), maxsplit=1, flags=re.I)[0].rstrip()
            name, role = _norm(raw_name), _norm(m.group("role")).casefold()
            if _valid_person(name):
                role = re.sub(r"\s+de\s+l'association$", "", role).strip()
                found.append((name, role, m.start("name"), m.start('name')+len(raw_name), event_type))
                evidence_quotes[(m.start('name'), m.start('name')+len(raw_name), role)] = _norm(m.group(0))

    # Les tableaux du JORT aplatis par le PDF gardent souvent cette forme :
    # « Monsieur X Lui-même Vice-Président 2023-2025 ». Le rôle est dans une
    # colonne distincte, sans deux-points.
    table_row = re.compile(
        r"(?:Monsieur|Madame|Mr\.?|Mme\.?|M\s*\.?|Mlle\.?)\s+"
        r"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+(?:(?i:ben|bin|bou|abou|el|al|ould|ouled|bint|bt)\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4})"
        r"\s*(?:\(\d+\))?\s+(?:(?:lui|elle)\s*[-–]?\s*m[eê]me\s+)?"
        r"(?P<role>vice\s*[-–]?\s*pr[eé]sident(?:e)?|pr[eé]sident(?:e)?|administrateur(?:trice)?|membre)\b",
        re.I)
    for match in table_row.finditer(text):
        name, role = _norm(match.group('name')), _norm(match.group('role')).casefold()
        if _valid_person(name):
            found.append((name, role, match.start('name'), match.end('name'), 'role_publie'))

    # Les listes d’administrateurs donnent parfois le rôle dans le titre,
    # puis uniquement les noms dans les puces.
    for heading in re.finditer(r"\badministrateurs?\s+suivants\b", text, re.I):
        section = text[heading.end():heading.end() + 1400]
        bullets = re.compile(
            r"(?:^|\n)\s*[-•]\s*(?:Monsieur|Madame|Mr\.?|Mme\.?|M\s*\.?|Mlle\.?)\s+"
            r"(?P<name>[^.;:]{2,100}?)(?=\s*[.;])", re.I)
        for match in bullets.finditer(section):
            name = _norm(match.group('name')).strip(' ,')
            if _valid_person(name):
                start = heading.end() + match.start('name')
                found.append((name, 'administrateur', start, heading.end() + match.end('name'), 'role_publie'))

    rich_mentions = _governance_mentions(text)
    mention_types = {}
    for mention in rich_mentions:
        found.append((mention['name'], mention['role'], mention['start'], mention['end'], mention['event_type']))
        mention_types[(mention['start'], mention['end'])] = mention['type']
        evidence_quotes[(mention['start'], mention['end'], mention['role'])] = mention['quote']
    relations = []
    seen = set()
    people_spans = [(start, end) for _, _, start, end, _ in found]
    for name, role, start, end, event_type in found:
        kind = mention_types.get((start, end), 'personne')
        key = (kind, _fold(name), _fold(role), event_type)
        if key in seen:
            continue
        seen.add(key)
        entity = next((row for row in entities if row.get('announcement_id') == aid
                       and row.get('type') == kind and _fold(row.get('name', '')) == _fold(name)), None)
        fresh_entity = entity is None
        eid = entity['id'] if entity else f"{aid}:{'personne' if kind == 'personne' else 'societe-liee'}:{len(entities)}"
        if entity is None:
            entity = dict(id=eid, name=name, type=kind, announcement_id=aid, is_primary=False)
        other_people = [span for span in people_spans if span != (start, end)]
        cin = _cin_near(text, start, end, other_people) if kind == 'personne' else None
        if cin:
            entity['cin'] = cin
            entity['identity_match_status'] = 'a_verifier'
        if fresh_entity:
            entities.append(entity)
        event_date, event_date_quote = _event_date_evidence(text, start, end)
        relations.append(dict(id=f"{aid}:relation:{len(relations)}", **{"from": eid, "to": org_id},
                              role=role, type="role_organisation", event_type=event_type,
                              event_date=event_date, event_date_quote=event_date_quote, announcement_id=aid,
                              quote=evidence_quotes.get((start, end, role), _norm(text[start:end])),
                              role_family=famille_role(role), status="extrait_automatiquement"))

    return relations


def _capital_subscriptions(text, aid, org_id, entities, existing_relations=()):
    """Relie les bénéficiaires explicitement nommés d'une augmentation de capital.

    Une annonce qui réserve une souscription ne prouve pas que le versement a
    ensuite été réalisé : le rôle reste donc formulé comme une désignation.
    """
    pattern = re.compile(
        r"\baugmentation\s+de\s+capital\b.{0,250}?\b(?:est|sera)\s+"
        r"(?:int[eé]gralement\s+)?r[eé]serv[eé]e\s+(?:[àa]|au\s+profit\s+de)\s+"
        r"(?P<list>[^.;]{2,900})",
        re.I | re.S)
    reserved = pattern.search(text)
    if not reserved:
        return []
    subscriber_list = reserved.group('list')
    quote = _norm(text[reserved.start():reserved.end()])
    subscribers = []
    person_pattern = re.compile(
        r"\b(?:Monsieur|Madame|Mr\.?|Mme\.?|Mlle\.?|M\.?)\s+(?P<name>[^,.;]+)", re.I)
    for match in person_pattern.finditer(subscriber_list):
        raw_name = re.split(r"\s+et\s+(?=(?:aux?|à\s+la)\s+soci[eé]t[eé]s?)", match.group('name'), maxsplit=1, flags=re.I)[0]
        name = _norm(raw_name).strip(' «»"')
        if _valid_person(name):
            subscribers.append((name, 'personne'))
    companies = re.search(r"\baux?\s+soci[eé]t[eé]s?\s+(?P<list>.+)", subscriber_list, re.I | re.S)
    if companies:
        for match in re.finditer(r"«([^»]{2,150})»|\"([^\"]{2,150})\"", companies.group('list')):
            name = _norm(match.group(1) or match.group(2)).strip()
            if name:
                subscribers.append((name, 'societe'))
    relations=[]
    seen=set()
    known_ids={str(relation.get('id')) for relation in existing_relations}
    existing_entities=[entity for entity in entities if str(entity.get('announcement_id'))==aid]
    for name, kind in subscribers:
        folded=_fold(name)
        if not folded or (kind,folded) in seen:
            continue
        seen.add((kind,folded))
        entity=next((row for row in existing_entities
                     if row.get('type')==kind and _fold(row.get('name'))==folded),None)
        digest=hashlib.sha1(folded.encode('utf-8')).hexdigest()[:12]
        if entity is None:
            entity=dict(id=f'{aid}:capital-souscripteur:{kind}:{digest}',name=name,type=kind,announcement_id=aid)
            entities.append(entity)
            existing_entities.append(entity)
        relation_id=f'{aid}:relation:souscription-capital:{digest}'
        if relation_id in known_ids:
            continue
        relations.append(dict(id=relation_id, **{'from':entity['id'],'to':org_id},
            role='bénéficiaire désigné de la souscription',type='souscription_capital',
            event_type='augmentation_capital',announcement_id=aid,quote=quote,
            status='extrait_automatiquement'))
    return relations


def reparer_souscriptions_capital(announcement, entities, relations):
    """Récupère les souscripteurs explicitement désignés sans relire le PDF."""
    aid=str(announcement.get('id') or '')
    text=str(announcement.get('text') or '')
    if not re.search(r'augmentation\s+de\s+capital',text,re.I):
        return {'entities':[],'relations':[]}
    relevant=[entity for entity in entities if str(entity.get('announcement_id'))==aid]
    organizations=[entity for entity in relevant
                   if entity.get('type') in ('societe','association','organisation')]
    if len(organizations)!=1:
        return {'entities':[],'relations':[]}
    copied=[dict(entity) for entity in relevant]
    before={str(entity.get('id')) for entity in copied}
    found=_capital_subscriptions(text,aid,organizations[0]['id'],copied,relations)
    new_entities=[entity for entity in copied if str(entity.get('id')) not in before]
    return {'entities':new_entities,'relations':found}


def _vente_fonds(text, aid, entities):
    """Extrait vendeur et acheteur uniquement si les deux parties sont explicites."""
    if not re.search(r"fonds\s+de\s+commerce", text, re.I) or re.search(r"donation|renonciation|location|g[eé]rance\s+libre", text[:500], re.I):
        return []
    sale = re.search(r"\b(?:(?:a|ont)\s+(?:vendu|c[eé]d[eé])|vend(?:u|ue)\s+et\s+c[eé]d[eé])\b", text, re.I)
    if not sale:
        return []
    seller = _commerce_party(text, 0, sale.start(), before=True)
    buyer = _commerce_party(text, sale.end(), min(len(text), sale.end() + 520), before=False)
    if not seller or not buyer or seller['name'].casefold() == buyer['name'].casefold():
        return []
    sellers = [seller]
    if seller['type'] == 'societe':
        extra = re.search(r"\s+et\s+(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4})\s*$", text[:sale.start()])
        if extra and _valid_commerce_person(extra.group('name')):
            sellers.append(dict(name=_norm(extra.group('name')),type='personne',start=extra.start('name'),end=extra.end('name'),scope=(extra.start('name'),sale.start())))
    quote_end = min(len(text), buyer['end'] + 120)
    fund_id = f"{aid}:fonds-commerce"
    fund_name, fund_evidence = _nom_fonds_vendu(text, sale.start())
    fund = dict(id=fund_id, name=fund_name, type="fonds_commerce", announcement_id=aid,
                is_primary=True, title_confidence='extrait' if fund_evidence else 'generique')
    if fund_evidence:
        fund['title_quote'] = fund_evidence
    entities.append(fund)
    quote = _norm(text[min(seller['start'], sale.start()):quote_end])
    event_date, event_date_quote = _event_date_evidence(text, sale.start(), sale.end())
    relations = []
    for index, (role, party) in enumerate([('vendeur', p) for p in sellers] + [('acheteur', buyer)]):
        suffix = f':{index+1}' if role == 'vendeur' and index else ''
        party_id = f"{aid}:partie-vente:{role}{suffix}"
        entity = dict(id=party_id, name=party['name'], type=party['type'], announcement_id=aid)
        if party['type'] == 'personne':
            other_party = seller if party is buyer else buyer
            cin = _cin_near(text, party['start'], party['end'], [(other_party['start'], other_party['end'])], scope=party['scope'])
            if cin:
                entity['cin'] = cin
                entity['identity_match_status'] = 'a_verifier'
                # Conserver le champ publié qui justifie cette attribution.
                scope_text = text[party['scope'][0]:party['scope'][1]]
                for evidence in re.finditer(
                        r"(?:C\s*\.\s*I\s*\.\s*N\s*\.?|CIN|carte\s+d['’]?identit[eé]\s+nationale)"
                        r"\s*(?:n[°ºo.]\s*)?[:n°º-]?\s*(\d(?:[ .-]?\d){7})", scope_text, re.I):
                    if re.sub(r'\D', '', evidence.group(1)) == cin:
                        entity['cin_quote'] = _norm(evidence.group(0))
                        break
        else:
            own_text = text[party['end']:party['scope'][1]]
            identifier = re.search(
                r"\b(?P<label>identifiant\s+(?:fiscal[e]?|unique)|matricule\s+fiscal|M\.?\s*F\.?|registre\s+(?:national\s+)?du\s+commerce|R\.?\s*C\.?)\s*(?:n[°ºo.]\s*)?[:=\-]?\s*(?P<value>[A-Z0-9]{6,12}(?:\s*/\s*[A-Z0-9]+){0,4})\b",
                own_text, re.I)
            if identifier:
                entity['identifier'] = re.sub(r'\s+', '', identifier.group('value')).upper()
                label = identifier.group('label').casefold()
                entity['identifier_type'] = ('registre_commerce_publie' if 'commerce' in label or re.fullmatch(r'r\.?\s*c\.?', label)
                                             else 'identifiant_unique_publie' if 'unique' in label
                                             else 'identifiant_fiscal_publie')
                entity['identifier_quote'] = _norm(identifier.group(0))
        entities.append(entity)
        relation_from, relation_to = (party_id, fund_id) if role == 'vendeur' else (fund_id, party_id)
        relations.append(dict(id=f"{aid}:relation:vente:{role}{suffix}", **{"from": relation_from, "to": relation_to},
            role=role, type="vente_fonds_commerce", event_type="vente",
            event_date=event_date, event_date_quote=event_date_quote, announcement_id=aid, quote=quote,
            status="extrait_automatiquement"))
    return relations


def _nom_fonds_vendu(text, sale_start):
    """Construit un titre distinctif seulement à partir des mentions publiées."""
    excerpt = _norm(text[sale_start:] if sale_start else text)
    # L'en-tête « Vente d'un fonds de commerce » ne décrit pas le bien.
    matches = list(re.finditer(r"fonds?\s+de\s+commerce\b", excerpt, re.I))
    match = next((m for m in matches if not re.match(r"[\s«»\"()]*\s*(?:suivant|par\b|g[eé]rance\s+libre|aux\s+termes|en\s+vertu|les\s+oppositions)\b", excerpt[m.end():], re.I)), None)
    if not match:
        return 'Fonds de commerce', ''
    description = excerpt[match.end():]
    description = re.split(r"(?:\.\s+(?:Les|Le|Toute|Toutes|Tout|Tous|Ce|Cet|Cette)\b|\b(?:les\s+oppositions|appartenant\s+[àa]|propri[eé]t[eé]\s+de|immatricul[eé]|identifiant\s+fiscal|MF\s*/\s*IU|[12][09]\d{2}[A-Z]\d{4,})\b|[àa]\s+(?:monsieur|madame|Mr\.?|Mme\.?|la\s+soci[eé]t[eé])\s+)", description, maxsplit=1, flags=re.I)[0].strip(' ,:-.')
    description = re.sub(r"^(?:connu\s+sous\s+le\s+nom\s+de|d[eé]nomm[eé]|exploit[eé]\s+sous\s+le\s+nom\s+de|[àa]\s+l['’]enseigne)\s*", '', description, flags=re.I)
    description = re.sub(r"^(?:de\s+|d['’])", '', description, flags=re.I)
    description = re.split(r"\b(?:pour\s+un\s+prix|moyennant|au\s+prix|avec\s+(?:tous|ses)|durant|pour\s+une\s+(?:dur[eé]e|p[eé]riode)|comprenant|ainsi\s+que|enregistr[eé])\b", description, maxsplit=1, flags=re.I)[0].strip(' ,:-.')
    evidence = _norm('fonds de commerce ' + description)
    description = re.sub(r"^(?:qui\s+consiste\s+en\s+(?:un(?:e)?\s+)?|[àa]\s+usage\s+(?:de\s+)?)", '', description, flags=re.I)
    # Lorsque l'activité suit l'adresse, remettre l'activité en tête du titre.
    activity = re.search(r"\s+destin[eé]\s+[àa]\s+(?:exercer\s+)?l['’]activit[eé]\s+(?:de\s+)?(.+)", description, re.I)
    if activity:
        description = activity.group(1).strip(' .') + ' — ' + description[:activity.start()].strip(' .')
    description = re.sub(r"\btype\s*\d+\b", '', description, flags=re.I)
    description = re.sub(r"\s+(?:sis|situ[eé])\s+(?:au|aux|[àa]|[àa]\s+la)\s+", ' — ', description, flags=re.I)
    description = re.sub(r"^(?:sis|situ[eé])\s+(?:au|aux|[àa])\s+", '', description, flags=re.I)
    description = _norm(description)
    if len(description) < 3:
        return 'Fonds de commerce', ''
    if len(description) > 150:
        description = description[:150].rsplit(' ', 1)[0].rstrip(' ,:-') + '…'
    return description[:1].upper() + description[1:], evidence


def _nom_organisation_complementaire(announcement):
    """Cherche une désignation explicite ; aucun nom n'est inventé."""
    text = str(announcement.get('text') or '')
    flat = _norm(text)
    commercial = re.search(r"\bnom\s+(?:de\s+commerce|commercial)\s*:\s*(.{3,180})", flat, re.I)
    if commercial:
        value = re.split(r"\s+(?:adresse|registre|matricule|identifiant|capital)\b", commercial.group(1), maxsplit=1, flags=re.I)[0].strip(' .;')
        if _titre_entite_valide(value): return value, commercial.group(0)
    gda = re.search(r"groupement\s+de\s+d[eé]veloppement\s+agricole.{0,500}?\b(?:nomination|d[eé]nomination)\s*:?\s*[\"«]([^\"»]{2,100})[\"»]", flat, re.I)
    if gda: return 'Groupement de développement agricole '+gda.group(1).strip(), gda.group(0)
    label = re.search(r"\b(?:d[eé]nomination(?:\s+(?:sociale(?:\s+et\s+commerciale)?|commerciale))?|raison\s+sociale|nom\s+(?:ou\s+raison\s+sociale|de\s+(?:la\s+)?(?:soci[eé]t[eé]|l['’]association)))\s*[:：]\s*(.{2,350})", flat, re.I)
    if label:
        name = _NEXT_FIELD.split(label.group(1), maxsplit=1)[0]
        name = re.split(r"\s+(?:cat[eé]gorie|secteur|activit[eé]|suivant|en\s+vertu|forme)\b|;", name, maxsplit=1, flags=re.I)[0]
        name = name.strip(' .,:;«»"')
        if 2 < len(name) < 150 and _titre_entite_valide(name):
            return name, _norm(label.group(0)[:len(name)+40])
    lines = [line.strip(' .–—') for line in text.splitlines() if line.strip()]
    inline_form = re.compile(r"\s+(?:S\.?U\.?A\.?R\.?L\.?|S\.?A\.?R\.?L\.?|S\.?A\.?)\s+(?:au\s+capital|capital|si[eè]ge)", re.I)
    for line in lines[:35]:
        found = inline_form.search(line)
        if found:
            name = line[:found.start()].strip(' .–—')
            if 2 < len(name) < 150 and _titre_entite_valide(name) and not re.match(r'constitution|nomination|augmentation|suivant|en\s+vertu', name, re.I):
                return name, line
    # Une forme juridique ou un capital bornent la raison sociale qui précède.
    form = re.compile(r"^(?:soci[eé]t[eé]\s+(?:anonyme|[àa]\s+responsabilit[eé]|unipersonnelle|mutuelle)|S\.?A\.?R\.?L\.?\b|S\.?U\.?A\.?R\.?L\.?\b|S\.?A\.?\s+au\s+capital|au\s+capital)", re.I)
    heading = re.compile(r"^(?:soci[eé]t[eé]s?\s+(?:anonymes?|[àa]\s+responsabilit[eé]|unipersonnelles)|autres\s+soci[eé]t[eé]s|notice|avis|constitution|approbation|augmentation|r[eé]duction|renouvellement|nomination|d[eé]mission|r[eé]vocation|modification|dissolution|assembl[eé]e|convocation|suivant|en\s+vertu|par\s+acte|si[eè]ge|capital|MF\b|identifiant|g[eé]rant|commissaire|dirigeants|statuts|(?:(?:du|des|et)\s+)?(?:mandat|commissaire|dirigeant|conseil)|gestion\s+|[\d.,]+\s+(?:TND|DT|dinars)\b)", re.I)
    for i, line in enumerate(lines[:35]):
        if not form.search(line):
            continue
        names = []
        for prev in reversed(lines[max(0, i-4):i]):
            if re.match(r'^(?:changement|transfert)\s+(?:du|de)\s+si[eè]ge',prev,re.I):
                break
            if heading.search(prev) or re.search(r"\b(?:comptes|mandats?|conform[eé]ment|articles?|commerciales|[12][09]\d{2})\b|^(?:Agr[eé]ment|Mise\s+[àa]|jour\s+de\s+statuts|sociales$|Et\s+(?:augmentation|modification|nomination|renouvellement))", prev, re.I):
                break
            names.insert(0, prev)
        name = _norm(' '.join(names)).strip('«»" .')
        if 2 < len(name) <= 150 and len(name.split()) <= 18 and _titre_entite_valide(name):
            return name, _norm(' '.join(names))
    # « Société AITICO / Au capital… », « Association … / Objet… ».
    prefix = re.compile(r"^(?:soci[eé]t[eé]|st[eé]\.?|association|syndic|groupement)\s+", re.I)
    for i, line in enumerate(lines[:30]):
        if not prefix.search(line) or heading.search(line):
            continue
        parts = [line]
        for follow in lines[i+1:i+4]:
            if heading.search(follow) or _NEXT_FIELD.match(' ' + follow) or form.search(follow):
                break
            if re.match(r"^[([]?\s*(?:M[./]?F\b|MF\s*/\s*IU|R[./]?C\b|Cf\.|objet|adresse|dur[eé]e|type|forme|il\s+|le\s+|les\s+|conform[eé]ment)\b", follow, re.I):
                break
            parts.append(follow)
        name = _norm(' '.join(parts)).strip('«»" .')
        if 3 < len(name) < 150 and len(name.split()) <= 18 and _titre_entite_valide(name):
            return name, _norm(' '.join(parts))
    narrative = re.compile(r"\b(?:soci[eé]t[eé]|ST[EÉ]\.?)\s+(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’&.-]*(?:\s+(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’&.-]*|de|du|des|et|el)){0,12})")
    for match in narrative.finditer(flat):
        name = match.group('name').strip(' .')
        if 2 <= len(name) <= 120 and _titre_entite_valide(name) and not re.match(r'(?:Anonyme|Unipersonnelle|A\s+Responsabilit[eé]|S\.?A\.?R\.?L|S\.?U\.?A\.?R\.?L|Civile|Communautaire)\b',name,re.I):
            return name, match.group(0)
    return None, None


def _nettoyer_nom_titre(name):
    name=nettoyer_libelle(name,'organisation')
    name = re.sub(r"^(?:dep[oô]t\s+(?:des?\s+)?[eé]tats\s+financiers|augmentation\s+(?:du\s+)?capital\s+(?:de\s+la\s+)?)\s*", '', name, flags=re.I)
    name = re.sub(r'^[\d., ]+\s+(?:TND|DT|dinars)\s+', '', name, flags=re.I)
    name = re.split(r'[.;]\s+\d+\s*[).:-]', name, maxsplit=1)[0]
    name = re.split(r"\s+(?:\(\s*)?(?:MF\s*/\s*IU|M[./]?F(?=\s*[:：])|identifiant\s+(?:unique|fiscal)|matricule\s+fiscal)\b",name,maxsplit=1,flags=re.I)[0]
    return _norm(name.translate(str.maketrans('', '', '«»"'))).strip(' .,:;')


def _titre_entite_valide(name):
    value=_norm(name)
    if libelle_documentaire(value,'organisation'):return False
    if not value or re.match(r"^(?:Entit[eé]\s+non\s+identifi[eé]e|gestion\s+de\b|autres\s+soci[eé]t[eé]s\b|renouvellement\s+(?:de|des)\s+(?:mandats|pouvoirs)|r[eé]alisation\s+de\s+l['’]augmentation|additif\s+[àa]\s+la\s+notice|avis\s+de\s+cr[eé]ation|d[eé]mission\s+(?:du|pr[eé]sident)|approbation\s+(?:du|des)|homologation\s+de|assembl[eé]e\s+g[eé]n[eé]rale|convocation\b|sociales\b|Et\s+(?:augmentation|modification)|Agr[eé]ment\s+de)",value,re.I): return False
    if _ACT_HEADING.match(value): return False
    if re.match(r'^(?:changement|transfert)\s+(?:du|de)\s+si[eè]ge',value,re.I): return False
    if re.fullmatch(r'(?:Soci[eé]t[eé]s?\s+Anonymes?|Gestion|Fonds?\s+de\s+commerce|\d{4}[A-Z0-9-]+)',value,re.I): return False
    return not bool(re.search(r'comme\s+suit\s*:|zone\s+d.activit[eé]\s*:',value,re.I))


def completer_titre_annonce(announcement, entities):
    """Titre, catégorie et preuve identiques en analyse et en réparation."""
    aid = str(announcement['id'])
    text = str(announcement.get('text') or '')
    current = [e for e in entities if str(e.get('announcement_id')) == aid]
    fund = next((e for e in current if e.get('type') == 'fonds_commerce'), None)
    commerce = bool(re.search(r'fonds?\s+de\s+commerce', text, re.I) and (
        re.search(r'(?:FCCC|FCLB)\d*$', str(announcement.get('reference') or ''), re.I)
        or re.search(r'(?:vente|cession|donation|location|g[eé]rance)[^\n]{0,60}fonds\s+de\s+commerce', text[:400], re.I)))
    if commerce:
        sale = re.search(r'\b(?:a|ont)\s+(?:vendu|c[eé]d[eé])\b', text, re.I)
        name, quote = _nom_fonds_vendu(text, sale.end() if sale else 0)
        if not fund:
            fund = dict(id=f'{aid}:fonds-commerce', type='fonds_commerce', announcement_id=aid, is_primary=True)
            entities.append(fund)
        fund.update(name=name, title_quote=quote, title_confidence='extrait' if quote else 'incertain')
        announcement['category'] = 'fonds_commerce'
        primary = fund
    else:
        primary = next((e for e in current if e.get('type') in ('societe','association','organisation') and e.get('is_primary')), None)
        primary = primary or next((e for e in current if e.get('type') in ('societe','association','organisation')), None)
        if primary and primary.get('status') not in ('valide','human_confirmed') and not _titre_entite_valide(primary.get('name')):
            repaired, quote = _nom_organisation_complementaire(announcement)
            if repaired:
                primary.update(name=_nettoyer_nom_titre(repaired),title_quote=quote,title_confidence='extrait')
            else:
                primary['title_confidence']='incertain'
        if not primary:
            name, quote = _nom_organisation_complementaire(announcement)
            name = name or _organisation(text) or nom_entite_entete(announcement)
            if name and _titre_entite_valide(name):
                name = _nettoyer_nom_titre(name)
                kind = _kind(announcement, text, name) or ('association' if re.match(r'association\b', name, re.I) else 'organisation')
                primary = dict(id=f'{aid}:organisation-titre', name=name, type=kind, announcement_id=aid,
                               is_primary=True, title_quote=quote or name, title_confidence='extrait')
                primary.update(_published_identifier(text, name))
                entities.append(primary)
                announcement['category'] = kind
    if primary:
        cleaned=_nettoyer_nom_titre(primary['name'])
        if cleaned and primary.get('status') not in ('valide','human_confirmed'): primary['name']=cleaned
        announcement.update(title=primary['name'], title_quote=primary.get('title_quote') or primary['name'],
                            title_confidence=primary.get('title_confidence','extrait'),
                            title_review_required=primary.get('title_confidence') in ('incertain','generique'))
    else:
        # Un intitulé documentaire n'est pas une entité dans le graphe.
        lines = [_norm(line) for line in text.splitlines() if line.strip()]
        useful = next((line for line in lines[:12] if 8 <= len(line) <= 140 and not re.search(r"annonces\s+l[eé]gales|traduction|journal\s+officiel|sommaire|responsabilit[eé]|^\d{4}[A-Z0-9]+$", line, re.I)), None)
        announcement.update(title=useful or 'Annonce — désignation à préciser', title_quote=useful or '',
                            title_confidence='incertain', title_review_required=True)
    announcement['title_extraction_version'] = TITLE_EXTRACTION_VERSION
    return primary


def reparer_parties_vente(announcement, entities, relations):
    """Répare les parties déjà liées, sans changer leurs IDs ni leurs sources.

    Retourne les listes complètes copiées et un bilan. Une vente non isolée
    ou plusieurs occurrences pour un même rôle restent à examiner manuellement.
    Le programme appelant conserve les sauvegardes et trace les différences.
    """
    aid = str(announcement.get('id') or '')
    fresh_entities = []
    fresh_relations = _vente_fonds(str(announcement.get('text') or ''), aid, fresh_entities)
    result = {'entities': [dict(row) for row in entities],
              'relations': [dict(row) for row in relations], 'changed': False, 'warnings': []}
    old_relations = [row for row in relations if str(row.get('announcement_id')) == aid
                     and row.get('type') == 'vente_fonds_commerce']
    if not old_relations:
        return result
    if len(fresh_relations) != 2:
        result['warnings'].append('Parties de vente non isolées avec certitude ; occurrences conservées.')
        return result
    replacements = {}
    entity_by_id = {row.get('id'): row for row in entities}
    for role in ('vendeur', 'acheteur'):
        matches = [row for row in old_relations if row.get('role') == role]
        if len(matches) != 1:
            result['warnings'].append('Plusieurs relations ou rôle manquant ; vente à examiner.')
            return result
        # Les extractions historiques orientent les deux parties vers le fonds.
        # Identifier la partie par son occurrence, jamais par le sens du lien.
        endpoints = [matches[0].get('from'), matches[0].get('to')]
        party_ids = [eid for eid in endpoints if str(eid).endswith(f':partie-vente:{role}')]
        if len(party_ids) != 1:
            party_ids = [eid for eid in endpoints if eid in entity_by_id
                         and entity_by_id[eid].get('type') != 'fonds_commerce'
                         and not str(eid).endswith(':fonds-commerce')]
        if len(party_ids) != 1:
            result['warnings'].append('Partie de vente ambiguë ; occurrence conservée.')
            continue
        old_id = party_ids[0]
        old = entity_by_id.get(old_id)
        if not old or str(old.get('announcement_id')) != aid:
            result['warnings'].append('Occurrence de vente absente ou source incohérente.')
            return result
        fresh = next(row for row in fresh_entities if row['id'] == f'{aid}:partie-vente:{role}')
        if (old.get('type') == 'personne' and fresh.get('type') == 'societe'
                and not re.search(r"\b(?:soci[eé]t[eé]|ST[EÉ]|identifiant\s+fiscal[e]?)\b",
                                  str(old.get('name') or ''), re.I)):
            result['warnings'].append(f'{role} : personne sans marqueur de société devenue organisation ; représentant possible, occurrence conservée.')
            continue
        replacement = dict(old)
        for field in ('cin', 'cin_quote', 'identity_match_status', 'identifier', 'identifier_type', 'identifier_quote'):
            replacement.pop(field, None)
        replacement.update({key: value for key, value in fresh.items() if key not in ('id', 'announcement_id')})
        if (old.get('type') == replacement.get('type') == 'personne'
                and old.get('cin') and old.get('cin') == replacement.get('cin')):
            # Ne pas toucher une attribution inchangée pour enrichir seulement
            # sa preuve : la maintenance cible les corrections réelles.
            if 'cin_quote' in old:
                replacement['cin_quote'] = old['cin_quote']
            else:
                replacement.pop('cin_quote', None)
        if old.get('type') == replacement.get('type') == 'personne' and old.get('cin'):
            if not replacement.get('cin') or (old['cin'] != replacement.get('cin') and not fresh.get('cin_quote')):
                for field in ('cin', 'cin_quote', 'identity_match_status'):
                    if field in old:
                        replacement[field] = old[field]
                    else:
                        replacement.pop(field, None)
                result['warnings'].append(f'{role} : ancienne CIN conservée faute de preuve explicite suffisante pour la remplacer.')
        if (old.get('type') == replacement.get('type') == 'personne'
                and old.get('cin') == replacement.get('cin')
                and 'identity_match_status' in old):
            replacement['identity_match_status'] = old['identity_match_status']
        replacements[old_id] = replacement
    result['entities'] = [replacements.get(row.get('id'), dict(row)) for row in entities]
    result['changed'] = result['entities'] != list(entities)
    return result


def enrichir(announcements):
    """Conserve les personnes par occurrence ; une CIN reste une candidate à vérifier."""
    entities, relations = [], []
    counts = {"annonces": len(announcements), "organisations": 0, "personnes": 0, "relations": 0, "a_classer": 0}
    for index, announcement in enumerate(announcements):
        aid = str(announcement.get("id") or f"annonce:{index}")
        announcement["status"] = "extrait_automatiquement"
        text = str(announcement.get("text") or "")
        sale_relations = _vente_fonds(text, aid, entities)
        relations.extend(sale_relations)
        name = _organisation(text) or nom_entite_entete(announcement)
        # Le code de référence peut établir la catégorie même si la raison
        # sociale n'a pas été reconnue dans le texte extrait.
        kind = _kind(announcement, text, name) or ('organisation' if name else None)
        if not kind:
            announcement["category"] = "a_classer"
            announcement["warning"] = "Catégorie non déterminée automatiquement ; résultat conservé sans demander de validation humaine systématique."
            counts["a_classer"] += 1
            if re.search(r"fonds\s+de\s+commerce", text, re.I) and re.search(r"\b(?:a\s+(?:vendu|c[eé]d[eé])|vend(?:u|ue)\s+et\s+c[eé]d[eé])\b", text, re.I) and not sale_relations:
                announcement['warning'] += ' Vente de fonds repérée, mais parties non isolées avec assez de certitude ; aucun lien vendeur/acheteur créé.'
                counts.setdefault('ventes_fonds_a_verifier', 0)
                counts['ventes_fonds_a_verifier'] += 1
            continue
        if not name:
            announcement["category"] = kind
            announcement["warning"] = "Catégorie déduite du code de référence ; organisation non reconnue dans le texte. Aucun lien personne-organisation n'est créé sans nom identifiable."
            if re.search(r"fonds\s+de\s+commerce", text, re.I) and re.search(r"\b(?:a\s+(?:vendu|c[eé]d[eé])|vend(?:u|ue)\s+et\s+c[eé]d[eé])\b", text, re.I) and not sale_relations:
                announcement['warning'] += ' Vente de fonds repérée, mais parties non isolées avec assez de certitude ; aucun lien vendeur/acheteur créé.'
                counts.setdefault('ventes_fonds_a_verifier', 0)
                counts['ventes_fonds_a_verifier'] += 1
            counts.setdefault("sans_organisation_reconnue", 0)
            counts["sans_organisation_reconnue"] += 1
            continue
        announcement["category"] = kind
        announcement["status"] = "extrait_automatiquement"
        announcement["warning"] = "Catégorie et entités extraites automatiquement ; sources et interprétation à vérifier selon le besoin."
        oid = f"{aid}:organisation"
        organization = dict(id=oid, name=name, type=kind, announcement_id=aid, is_primary=True,
                            entity_extraction_version=ENTITY_EXTRACTION_VERSION)
        organization.update(_published_identifier(text, name))
        entities.append(organization)
        counts["organisations"] += 1
        extracted = _relations(text, aid, oid, entities)
        capital_relations = _capital_subscriptions(text, aid, oid, entities, extracted)
        if (re.search(r"fonds\s+de\s+commerce", text, re.I)
                and re.search(r"\b(?:a\s+(?:vendu|c[eé]d[eé])|vend(?:u|ue)\s+et\s+c[eé]d[eé])\b", text, re.I)
                and not sale_relations):
            announcement['warning'] += ' Vente de fonds repérée, mais parties non isolées avec assez de certitude ; aucun lien vendeur/acheteur créé.'
            counts.setdefault('ventes_fonds_a_verifier', 0)
            counts['ventes_fonds_a_verifier'] += 1
        relations.extend(extracted)
        relations.extend(capital_relations)
        counts["personnes"] += len(extracted)
    counts["relations"] = len(relations)
    counts["personnes"] = sum(entity['type'] == 'personne' for entity in entities)
    counts["organisations"] = sum(entity['type'] in ('societe', 'association') for entity in entities)
    for entity in entities:
        entity.setdefault('entity_extraction_version', ENTITY_EXTRACTION_VERSION)
    for announcement in announcements:
        completer_titre_annonce(announcement, entities)
    from extraction_structuree import complement
    entities, relations = complement(announcements, entities, relations)
    counts['organisations'] = sum(e['type'] in ('societe','association','organisation') for e in entities)
    counts['fonds_commerce'] = sum(e['type'] == 'fonds_commerce' for e in entities)
    counts['a_classer'] = sum(a.get('category') == 'a_classer' for a in announcements)
    counts['relations'] = len(relations)
    counts['personnes'] = sum(e['type'] == 'personne' for e in entities)
    return {"entities": entities, "relations": relations, "stats": counts}
