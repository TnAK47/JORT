"""Règles communes de reprise : relancer seulement une erreur temporaire."""
import errno
import sqlite3
import urllib.error


def erreur_temporaire(exc):
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    if isinstance(exc,OSError) and (exc.errno in (errno.ECONNRESET,errno.ECONNABORTED,errno.ECONNREFUSED,
                                                   errno.ETIMEDOUT,errno.ENETDOWN,errno.ENETUNREACH,errno.EHOSTUNREACH)
                                    or getattr(exc,'winerror',None) in (10053,10054,10060,10061,10065)):
        return True
    return isinstance(exc, sqlite3.OperationalError) and any(
        word in str(exc).casefold() for word in ('locked', 'busy', 'temporarily unavailable'))


def ressource_indisponible(exc):
    return (isinstance(exc, (MemoryError, OSError))
            and not isinstance(exc,(FileNotFoundError,IsADirectoryError))
            and not erreur_temporaire(exc))
