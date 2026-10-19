"""Contrôle et correction différée de la cohérence de la page Collecte.

Par défaut, le script est strictement en lecture seule : il vérifie que chaque
bouton de l'administration est accepté par l'API locale et recense les textes
qui exposent encore SQLite alors que la consultation s'appuie sur PostgreSQL.

Avec --apply, il sauvegarde les deux fichiers d'interface, applique uniquement
les libellés validés ci-dessous, puis conserve un rapport dans
data/maintenance/corrections-collecte/. Il ne touche ni aux bases, ni aux PDF,
ni aux traitements de collecte ou d'indexation.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil


UI_FILES = (Path('dist/collecte.html'), Path('dist/collecte.js'))
DIRECT_ACTIONS = {'maintenance_merge_entities', 'maintenance_undo_merge'}
# Chaque remplacement décrit le comportement réel : PostgreSQL consulte la
# restitution ; les archives annuelles restent utilisées pour les preuves et
# les reprises, sans être présentées comme le moteur actif.
REPLACEMENTS = {
    'dist/collecte.html': (
        (
            'Lecture seule : vérifie annonces, sources, pages, liens, PDF sur G: et bases SQLite. Le contrôle global vérifie aussi les références des interventions.',
            'Lecture seule : vérifie annonces, sources, pages, liens, PDF sur G:, archives annuelles locales et cohérence de la base PostgreSQL de consultation. Le contrôle global vérifie aussi les références des interventions.',
        ),
        (
            'Corrige les anomalies reconnues sans relire les PDF : titres récupérables, synchronisation SQLite et mise à jour des interventions. Une copie de sécurité précède chaque modification.',
            'Corrige les anomalies reconnues sans relire les PDF : titres récupérables, synchronisation des archives annuelles, publication dans la base de consultation et mise à jour des interventions. Une copie de sécurité précède chaque modification.',
        ),
        (
            'Vérifie et compacte les bases SQLite annuelles pour accélérer les requêtes et récupérer de l’espace. Une copie de sécurité précède chaque base modifiée.',
            'Vérifie et compacte les archives annuelles locales pour réduire leur encombrement. La recherche utilise la base PostgreSQL de consultation. Une copie de sécurité précède chaque archive modifiée.',
        ),
        (
            'Détecte les personnes dont le nom a absorbé un mot de la phrase suivante, par exemple «\u00a0Nom. En\u00a0». Réextrait uniquement les annonces concernées, supprime l’occurrence erronée lorsqu’une occurrence corrigée est prouvée dans la même annonce, puis met à jour le regroupement. Les annonces et passages sources restent inchangés.',
            'Détecte les personnes dont le nom a absorbé un mot de la phrase suivante, par exemple «\u00a0Nom. En\u00a0». Réextrait uniquement les annonces concernées et corrige ce suffixe certain ; si une occurrence corrigée existe déjà dans la même annonce, le doublon est réuni. Les annonces et passages sources restent inchangés.',
        ),
        (
            'Crée une copie locale cohérente des bases JSON et SQLite. Les PDF sources ne sont pas dupliqués.',
            'Crée une copie locale cohérente des archives annuelles. Les PDF sources ne sont pas dupliqués.',
        ),
    ),
    'dist/collecte.js': (
        (
            'JSON ${bytes(item.json_bytes)} · SQLite ${bytes(item.sqlite_bytes)}',
            'Archives ${bytes(item.json_bytes)} · index local ${bytes(item.sqlite_bytes)}',
        ),
    ),
}


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return fallback


def atomic_write(path, content):
    temporary = path.with_name(path.name + '.coherence.tmp')
    temporary.write_text(content, encoding='utf-8', newline='\n')
    os.replace(temporary, path)


def allowed_actions(server_source):
    match = re.search(r"if action not in \((?P<actions>.*?)\) or limit not in", server_source, re.S)
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group('actions') if match else ''))


def queued_modes(server_source):
    match = re.search(r"if mode not in \((?P<modes>.*?)\): raise ValueError\('Mode de maintenance invalide'", server_source, re.S)
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group('modes') if match else ''))


def active_lanes(root):
    status = read_json(root / 'data/collecte-traitement.json', {})
    lanes = status.get('lanes', {}) if isinstance(status, dict) else {}
    return {name: lane.get('state') for name, lane in lanes.items()
            if isinstance(lane, dict) and lane.get('state') in {'running', 'pending', 'stopping'}}


def audit(root):
    html_path, js_path = (root / item for item in UI_FILES)
    server_path = root / 'scripts/serveur_local.py'
    html = html_path.read_text(encoding='utf-8')
    js = js_path.read_text(encoding='utf-8')
    server = server_path.read_text(encoding='utf-8')
    buttons = set(re.findall(r'data-action="([a-z0-9_]+)"', html))
    api_actions = allowed_actions(server)
    modes = queued_modes(server)
    missing_api = sorted(buttons - api_actions)
    maintenance_buttons = {value for value in buttons if value.startswith('maintenance_')}
    missing_mode = sorted(action for action in maintenance_buttons
                          if action.removeprefix('maintenance_') not in modes and action not in DIRECT_ACTIONS)
    legacy = []
    for relative, content in ((UI_FILES[0], html), (UI_FILES[1], js)):
        for number, line in enumerate(content.splitlines(), 1):
            if re.search(r'\bSQLite\b', line):
                legacy.append({'file': str(relative).replace('\\', '/'), 'line': number, 'text': line.strip()})
    replacements = []
    for relative, entries in REPLACEMENTS.items():
        content = (root / relative).read_text(encoding='utf-8')
        for before, after in entries:
            replacements.append({
                'file': relative,
                'present': before in content,
                'already_corrected': after in content,
                'before': before,
                'after': after,
            })
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'state': 'ready' if not missing_api and not missing_mode else 'blocked',
        'buttons': sorted(buttons),
        'api_actions_missing': missing_api,
        'maintenance_modes_missing': missing_mode,
        'legacy_sqlite_references': legacy,
        'planned_replacements': replacements,
        'active_lanes': active_lanes(root),
        'scope': 'Interface Collecte et vérification statique des routes API uniquement.',
    }


def apply(root, report):
    if report['state'] != 'ready':
        raise RuntimeError('Routes incohérentes : corrigez les erreurs avant de modifier l’interface.')
    if report['active_lanes']:
        raise RuntimeError('Traitement actif : attendez sa fin avant d’appliquer une correction d’interface.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = root / 'data/maintenance/corrections-collecte' / stamp
    backup.mkdir(parents=True, exist_ok=False)
    changed = []
    for relative, entries in REPLACEMENTS.items():
        path = root / relative
        original = path.read_text(encoding='utf-8')
        updated = original
        for before, after in entries:
            if before in updated:
                updated = updated.replace(before, after)
            elif after not in updated:
                raise RuntimeError(f'Libellé attendu introuvable : {relative}')
        if updated != original:
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            atomic_write(path, updated)
            changed.append(relative)
    report.update(state='applied', backup=str(backup.relative_to(root)).replace('\\', '/'), changed_files=changed,
                  applied_at=datetime.now(timezone.utc).isoformat())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Applique les seuls libellés validés après sauvegarde.')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    report = audit(root)
    if args.apply:
        report = apply(root, report)
        report_dir = root / 'data/maintenance/corrections-collecte'
        report_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(report_dir / 'dernier-rapport.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    try:
        print(payload)
    except UnicodeEncodeError:
        # Certaines consoles Windows utilisent encore CP-1252. Le rapport
        # reste identique ; seuls les caractères non pris en charge sont
        # échappés pour garantir une fin de commande propre.
        print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['state'] in {'ready', 'applied'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
