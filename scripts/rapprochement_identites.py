"""Clés de regroupement présumé et propositions de rapprochement sourcées."""

import re
import json
import unicodedata

STRATEGY_VERSION = '2'
PHONETIC_STRATEGY = 'phonetic-off-v1'
NAME_STRATEGY_VERSION = 'normalized-name-v1'


def occurrences_distinctes(occurrences):
    """Écarte seulement une répétition du même ID ou de la même preuve précise.

    Le journal, la page et l'annonce seuls ne suffisent jamais : plusieurs
    personnes peuvent être publiées dans le même passage général.
    """
    seen_ids, seen_evidence = set(), set()
    for row in occurrences:
        occurrence_id = row.get('id') or row.get('entity_id')
        if occurrence_id and str(occurrence_id) in seen_ids:
            continue
        if occurrence_id:
            seen_ids.add(str(occurrence_id))
        evidence = row.get('evidence')
        precise = None
        if isinstance(evidence, dict) and (evidence.get('start') is not None
                and evidence.get('end') is not None):
            precise = evidence
        elif row.get('start') is not None and row.get('end') is not None:
            precise = {'quote': row.get('quote'), 'start': row.get('start'), 'end': row.get('end')}
        key = None
        if row.get('announcement_id') and precise:
            key = (str(row['announcement_id']), row.get('type'),
                   normaliser_nom_personne(row.get('name')), normaliser_cin(row.get('cin')),
                   json.dumps(precise, sort_keys=True, ensure_ascii=False))
        if key and key in seen_evidence:
            continue
        if key:
            seen_evidence.add(key)
        yield row


def normaliser_nom_personne(value):
    """Clé de nom tolérant casse, accents, ponctuation et espaces.

    Cette clé sert à regrouper provisoirement les homonymes dans le graphe.
    Elle ne modifie ni les annonces d'origine ni leurs citations.
    """
    if value is None:
        return ''
    decomposed = unicodedata.normalize('NFD', str(value).casefold())
    plain = ''.join(char for char in decomposed if unicodedata.category(char) != 'Mn')
    return re.sub(r'\s+', ' ', ''.join(char if char.isalnum() else ' ' for char in plain)).strip()


def cle_prenom_nom(value):
    """Clé stricte prénom + nom, indépendante de l'ordre et des patronymes.

    Elle ne suffit jamais seule : l'appelant doit aussi exiger la même CIN.
    Les particules courantes sont ignorées ; les mots et leur orthographe
    restent exacts, sans rapprochement phonétique.
    """
    tokens = normaliser_nom_personne(value).split()
    particles = {'ben', 'bin', 'ibn', 'bint', 'al', 'el', 'ai', 'ep', 'epe', 'de', 'du', 'del', 'la', 'le'}
    tokens = [token for token in tokens if token not in particles]
    if len(tokens) < 2:
        return ''
    return '|'.join(sorted((tokens[0], tokens[-1])))


def normaliser_cin(value):
    """Normalise espaces et casse sans altérer les caractères de la CIN."""
    return ''.join(str(value).upper().split()) if value is not None else ''


def candidats_cin_exacte(occurrences):
    """Retourne des paires candidates; une CIN identique ne confirme rien."""
    by_cin = {}
    for occurrence in occurrences_distinctes(occurrences):
        if occurrence.get('type') not in (None, 'personne'):
            continue
        cin = normaliser_cin(occurrence.get('cin'))
        occurrence_id = occurrence.get('id') or occurrence.get('entity_id')
        if cin and occurrence_id:
            by_cin.setdefault(cin, []).append(str(occurrence_id))
    results = []
    for _cin, ids in sorted(by_cin.items()):
        unique_ids = sorted(set(ids))
        for index, left in enumerate(unique_ids):
            for right in unique_ids[index + 1:]:
                results.append({
                    'occurrence_a': left, 'occurrence_b': right,
                    'method': 'exact_cin', 'strategy_version': STRATEGY_VERSION,
                    'status': 'pending',
                    # Ne pas recopier la donnée personnelle dans la preuve.
                    'evidence': {'kind': 'same_cin', 'cin_present': True},
                })
    return results


def candidats_phonetiques(occurrences, *, enabled=False):
    """Point d'extension versionné, désactivé et sans calcul pour le moment."""
    del occurrences, enabled
    return []


def candidats_nom_identique(occurrences):
    """Regroupe en candidats provisoires les personnes au nom normalisé identique."""
    by_name = {}
    for occurrence in occurrences_distinctes(occurrences):
        if occurrence.get('type') not in (None, 'personne'):
            continue
        name_key = normaliser_nom_personne(occurrence.get('name'))
        occurrence_id = occurrence.get('id') or occurrence.get('entity_id')
        if name_key and occurrence_id:
            by_name.setdefault(name_key, []).append(str(occurrence_id))
    results = []
    for name_key, ids in sorted(by_name.items()):
        unique_ids = sorted(set(ids))
        for index, left in enumerate(unique_ids):
            for right in unique_ids[index + 1:]:
                results.append({
                    'occurrence_a': left, 'occurrence_b': right,
                    'method': 'normalized_name', 'strategy_version': NAME_STRATEGY_VERSION,
                    'status': 'needs_review', 'presumed_same_person': True,
                    'evidence': {'kind': 'same_normalized_name', 'name_key': name_key},
                })
    return results


def proposer_candidats(occurrences, *, phonetic_enabled=False):
    """Combine les candidats CIN, nom identique et stratégie phonétique inactive."""
    rows = list(occurrences)
    result = candidats_cin_exacte(rows)
    result.extend(candidats_nom_identique(rows))
    result.extend(candidats_phonetiques(rows, enabled=phonetic_enabled))
    return result
