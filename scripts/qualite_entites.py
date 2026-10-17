"""Écarter les libellés documentaires évidents, sans effacer leurs preuves."""
import re
import unicodedata


def norm(value):
    text=''.join(c for c in unicodedata.normalize('NFD',str(value or '')) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^\w]+',' ',text).strip()


def nettoyer_libelle(name,kind):
    if kind=='personne':return name
    name=re.sub(r"^(?:g[eé]n[eé]rale\s+(?:extraordinaire|ordinaire)|partie\s+poursuivante|poursuivant)\s+(?:(?:de\s+la|de|du|d['’])\s*)?",'',str(name or ''),flags=re.I)
    return name.strip(' .,:;')


def libelle_documentaire(name,kind):
    if kind=='fonds_commerce':return False
    value=norm(name)
    if value in ('journal officiel','registre national','registre de commerce','registre national des entreprises','gestion de','gestion de societes','autres societes','societes anonymes','societes communautaires','vente aux encheres publiques','vente a l enchere publique','l augmentation du capital d une','augmentation du capital','constitution d une societe'):
        return True
    return bool(re.match(r'^(?:tel|telephone|fax)\s+\d',value))
