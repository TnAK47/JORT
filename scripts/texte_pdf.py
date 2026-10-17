"""Extraction de texte PDF avec OCR local facultatif pour les pages scannées."""
import io
import shutil
import subprocess


OCR_REQUIRED = 'OCR requis : installer Tesseract et langues fra/ara.'


def _tesseract_path():
    return shutil.which('tesseract') or (r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        if __import__('os').path.isfile(r'C:\Program Files\Tesseract-OCR\tesseract.exe') else None)


def extract_page(reader, pageindex, path=None):
    """Retourne (texte, avertissements); `reader` est un PdfReader pypdf."""
    text = reader.pages[pageindex].extract_text() or ''
    if text.strip():
        return text, []
    tesseract = _tesseract_path()
    if not tesseract:
        return '', [OCR_REQUIRED]
    try:
        import pypdfium2
    except ImportError:
        return '', [OCR_REQUIRED + ' Le moteur de rendu pypdfium2 est absent.']
    try:
        if path is None:
            stream = getattr(reader, 'stream', None)
            if stream is None:
                return '', [OCR_REQUIRED + ' Le PDF source ne peut pas être rendu.']
            stream.seek(0)
            document = pypdfium2.PdfDocument(stream.read())
        else:
            document = pypdfium2.PdfDocument(str(path))
        bitmap = document[pageindex].render(scale=2.0)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        proc = subprocess.run([tesseract, 'stdin', 'stdout', '-l', 'fra+ara'],
                              input=buffer.getvalue(), capture_output=True, check=False, timeout=90)
        if proc.returncode:
            return '', [OCR_REQUIRED + ' Tesseract ne trouve pas les langues fra/ara.']
        return proc.stdout.decode('utf-8', errors='replace'), ['Texte OCR local ; lecture à vérifier.']
    except Exception as exc:
        return '', [f'{OCR_REQUIRED} Échec OCR local : {exc}']
    finally:
        try:
            document.close()
        except (UnboundLocalError, AttributeError):
            pass
