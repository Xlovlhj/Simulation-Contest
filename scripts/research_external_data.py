"""只读检索外部数据并保留来源快照；不改变模型或采用外车标定值。"""
from concurrent.futures import ThreadPoolExecutor
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'records/external_research_20261018'
CACHE.mkdir(parents=True, exist_ok=True)


def fetch(item):
    name, url = item
    dest = CACHE / (name + '.html')
    try:
        response = requests.get(url, timeout=(8, 22), headers={'User-Agent': 'Mozilla/5.0'})
        dest.write_bytes(response.content)
        soup = BeautifulSoup(response.text, 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer']):
            node.decompose()
        text = soup.get_text(' ', strip=True)
        (CACHE / (name + '.txt')).write_text(text, encoding='utf-8')
        links = [{'title': a.get_text(' ', strip=True)[:120], 'url': a['href']}
                 for a in soup.find_all('a', href=True)
                 if any(key in (a['href'] + a.get_text()).lower() for key in
                        ['.xlsx', '.csv', '.zip', '.mat', 'download', 'dataset', 'battery', 'temperature', 'thermal', 'road load', 'd3', 'coastdown'])]
        result = {'name': name, 'url': url, 'resolved_url': response.url,
                  'status': response.status_code, 'bytes': len(response.content),
                  'sha256': hashlib.sha256(response.content).hexdigest(),
                  'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'links': links}
        (CACHE / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({**result, 'text': text[:8500], 'links': links[:14]}, ensure_ascii=True), flush=True)
        return result
    except requests.RequestException as error:
        result = {'name': name, 'url': url, 'error': str(error)}
        (CACHE / (name + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        return result


def catalog_search(item):
    index, query = item
    try:
        response = requests.get('https://api.datacite.org/dois',
                                params={'query': query, 'page[size]': 6}, timeout=(8, 25))
        (CACHE / f'datacite_{index}.json').write_bytes(response.content)
        data = response.json()
        rows = [{'doi': row['id'], 'url': row['attributes'].get('url'),
                 'title': row['attributes'].get('titles'),
                 'description': str(row['attributes'].get('descriptions'))[:6500]}
                for row in data.get('data', [])]
        print(json.dumps({'query': query, 'total': data.get('meta', {}).get('total'), 'results': rows}, ensure_ascii=True), flush=True)
    except (requests.RequestException, ValueError) as error:
        print(json.dumps({'query': query, 'error': str(error)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', action='store_true')
    args = parser.parse_args()
    if args.catalog:
        queries = ['titles.title:"Oxford Battery Degradation Dataset"',
                   'titles.title:"LG 18650HG2"',
                   'electric vehicle cabin temperature',
                   'vehicle battery thermal temperature dataset']
        list(ThreadPoolExecutor(max_workers=4).map(catalog_search, enumerate(queries)))
        raise SystemExit()
    sources = [
        ('epa', 'https://www.epa.gov/compliance-and-fuel-economy-data/data-cars-used-testing-fuel-economy'),
        ('nasa_battery', 'https://data.nasa.gov/dataset/li-ion-battery-aging-datasets'),
        ('calce', 'https://calce.umd.edu/battery-data'),
        ('mendeley_hg2_api', 'https://data.mendeley.com/public-api/datasets/cp3473x7xv/versions/3'),
        ('mendeley_pf_api', 'https://data.mendeley.com/public-api/datasets/wykht8y7tg/versions/1'),
        ('argonne_d3', 'https://www.anl.gov/amd/d3'),
        ('doe_d3', 'https://www.anl.gov/taps/dynamometer-drive-schedules'),
    ]
    list(ThreadPoolExecutor(max_workers=7).map(fetch, sources))
