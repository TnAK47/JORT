"""Petit contrôle réseau des sources publiques, sans collecte massive."""
import urllib.request

URLS = [
    'https://www.pist.tn/jort/2020/2020F/Jo0452020.pdf',
    'http://www.pist.tn/jort/2020/2020F/Jo0452020.pdf',
    'http://www.cnudst.rnrt.tn/jortsrc/1956/1956f/jo00856.pdf',
    'https://idaraty.tn/fr/publications/jort-1956-092-e0ae',
    'https://www.loc.gov/collections/foreign-legal-gazettes/?fa=location:tunisia&fo=json',
]

if __name__ == '__main__':
    for url in URLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'JORT-Private-Research/1.0'})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read(150000)
                print(url, response.status, response.headers.get('Content-Type'), repr(data[:60]))
                if 'json' in response.headers.get('Content-Type', ''):
                    import json
                    result = json.loads(data)
                    for item in result.get('results', [])[:3]:
                        print(item.get('id'), item.get('title'))
                elif 'html' in response.headers.get('Content-Type', ''):
                    import re
                    for match in re.findall(r'.{0,150}(?:\.pdf|1956|download|Download).{0,150}', data.decode('utf-8', errors='replace'))[:12]:
                        print(match)
        except Exception as exc:
            print(url, type(exc).__name__, str(exc))
