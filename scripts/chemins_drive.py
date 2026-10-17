"""Résolution commune des chemins Google Drive utilisés par JORT."""
import os
from pathlib import Path


def google_drive_mon_drive():
    """Retourne « Mon Drive », par défaut dans la session Windows de l’utilisateur."""
    value = os.environ.get('JORT_GOOGLE_DRIVE_ROOT') or r'G:\Mon Drive'
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ValueError('JORT_GOOGLE_DRIVE_ROOT doit être un chemin absolu vers « Mon Drive ».')
    return root


def google_drive_jort():
    """Retourne le dossier JORT dans « Mon Drive »."""
    return google_drive_mon_drive() / 'JORT'


def resolve_drive_reference(reference):
    """Résout une référence relative `drive/année/fichier.pdf` dans le dossier JORT."""
    value = str(reference or '').replace('\\', '/')
    if not value.lower().startswith('drive/'):
        raise ValueError('La référence doit commencer par drive/.')
    relative = Path(value[6:])
    if relative.is_absolute() or any(part in ('', '.', '..') for part in relative.parts):
        raise ValueError('Référence Google Drive invalide.')
    base = google_drive_jort().resolve()
    target = (base / relative).resolve()
    target.relative_to(base)
    return target


def check_google_drive_access(require_jort_folder=True):
    """Vérifie l’accès au lecteur et au dossier JORT sans parcourir les PDF."""
    mon_drive = google_drive_mon_drive()
    with os.scandir(mon_drive):
        pass
    jort = google_drive_jort()
    if require_jort_folder:
        with os.scandir(jort):
            pass
    return jort
