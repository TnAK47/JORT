"""Règles locales partagées par l'ingestion et la réconciliation.

Les valeurs publiées restent intactes. La clé sert à comparer, jamais à
remplacer une preuve ou à certifier un identifiant administratif.
"""
import re
from extraction_structuree import normalize_name

RULE_VERSION = 'identites-2026-10-15-v2'
FISCAL_KINDS = ('identifiant_fiscal_publie', 'identifiant_unique_publie', 'mf', 'identifiant_unique')
RC_KINDS = ('registre_commerce_publie', 'rc')
OFFICIAL_KINDS = ('cin', *FISCAL_KINDS, *RC_KINDS)


def person_name(value):
    name = normalize_name(value)
    return re.sub(r'^(?:(?:m|mr|mrs|mme|mlle|monsieur|madame|mademoiselle)\s+)+', '', name).strip()


def identifier_family(kind):
    return 'fiscal' if kind in FISCAL_KINDS else 'rc' if kind in RC_KINDS else kind


def identifier_parts(kind, value):
    raw = re.sub(r'\s+', '', str(value or '').upper())
    compact = re.sub(r'[^A-Z0-9]', '', raw)
    family = identifier_family(kind)
    if family == 'cin':
        digits = re.sub(r'[.\-]', '', raw)
        return (digits, '') if re.fullmatch(r'\d{8}', digits) and int(digits) else None
    if family == 'fiscal':
        # Garder la lettre de contrôle pour vérifier les contradictions.
        # Seuls les séparateurs et les zéros de présentation sont neutralisés.
        match = re.fullmatch(r'(\d{5,8})(?:/?([A-Z]))?(?:/[A-Z0-9]{1,6}){0,4}', raw)
        if not match or not int(match[1]):
            return None
        return (str(int(match[1])), match[2] or '')
    if family == 'rc':
        # Un suffixe de texte extrait (ex. « EN ») n'est pas tronqué au hasard.
        if re.fullmatch(r'[A-Z]\d{5,14}', compact):
            compact = compact[1:]
        if re.fullmatch(r'[A-Z]{1,3}\d{5,14}|\d{5,14}', compact):
            return compact, ''
        return None
    return None


def canonical_identifier(kind, value):
    parts = identifier_parts(kind, value)
    # Un format inconnu reste consultable, mais n'est pas une clé de fusion.
    return parts[0] if parts else re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def conflict_reason(entities, observations):
    """Refuser les vrais conflits et les changements de nom non documentés."""
    if len({e['nature'] for e in entities}) != 1:
        return 'Natures incompatibles'
    values = {'cin': set(), 'fiscal': set(), 'rc': set()}
    fiscal_letters = set()
    for row in observations:
        if row.get('attribution', 'explicite') != 'explicite' or not row.get('active', True):
            continue
        parts = identifier_parts(row['kind'], row.get('raw_value') or row.get('normalized_value'))
        if not parts:
            continue
        family = identifier_family(row['kind'])
        if family in values:
            values[family].add(parts[0])
        if family == 'fiscal' and parts[1]:
            fiscal_letters.add(parts[1])
    if len(values['cin']) > 1:
        return 'CIN contradictoires'
    if len(values['fiscal']) > 1:
        return 'Matricules fiscaux contradictoires'
    if entities[0]['nature'] == 'personne_physique':
        # Le CIN tunisien est l'identifiant légal unique : il prime sur une
        # variation de transcription du nom (ordre, titre, accent ou coquille).
        # Sans CIN commun, on conserve le garde-fou sur les noms différents.
        if not values['cin'] and len({person_name(e['label']) for e in entities}) > 1:
            return 'Même CIN mais noms différents : attribution à vérifier'
    rc_numbers = {re.sub(r'^[A-Z]{1,3}', '', value) for value in values['rc']}
    if entities[0]['nature'] != 'personne_physique' and len(rc_numbers) > 1 and not values['fiscal']:
        return 'RC différents sans MF commun'
    return None
