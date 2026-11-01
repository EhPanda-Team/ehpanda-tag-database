#!/usr/bin/env python3
"""Fetch the sources and build every dataset into an output directory, ready for release.py."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import datasets
from common import MANIFEST_NAME, ROOT, fetch, latest_release

DEFAULT_REPOSITORY = 'EhPanda-Team/ehpanda-tag-database'
UPSTREAM = 'EhTagTranslation/Database'
UPSTREAM_ASSET_PREFIX = f'https://github.com/{UPSTREAM}/releases/download/'
UPSTREAM_LICENSE = ROOT / 'licenses/EhTagTranslation-Database-LICENSE.md'
JAPANESE_URL = 'https://e-hentaidb.com/tag/'
KOREAN = 'project-violet/tag-info'
KOREAN_LICENSE = ROOT / 'licenses/project-violet-tag-info-LICENSE'
# About two thirds of the inventory in 2026. A fixed floor, because the shrink guard compares against
# the previous release and a slow decline would pass it every day.
MIN_UPSTREAM_ENTRIES = 30000
MAX_SHRINK = 0.10


def digest(data):
    return hashlib.sha256(data).hexdigest()


def github_json(path):
    return json.loads(subprocess.check_output(['gh', 'api', path]))


def load_previous(repository, directory):
    """Download the latest release's datasets and manifest. With no release yet there are none."""
    release = latest_release(repository)
    if release is None:
        return {}, {}
    subprocess.run(['gh', 'release', 'download', release['tag_name'], '--repo', repository,
                    '--pattern', '*.json', '--dir', str(directory)], check=True)
    paths = list(Path(directory).glob('*.json'))
    manifest = next(json.loads(path.read_text()) for path in paths if path.name == MANIFEST_NAME)
    return {path.stem: path.read_bytes() for path in paths if path.name != MANIFEST_NAME}, manifest


def load_upstream():
    """Fetch upstream. Every dataset derives from it, so any problem here stops the whole run."""
    release = github_json(f'repos/{UPSTREAM}/releases/latest')
    asset = next(asset for asset in release['assets'] if asset['name'] == 'db.raw.json')
    url = asset['browser_download_url']
    if not url.startswith(UPSTREAM_ASSET_PREFIX):
        raise ValueError('Unexpected upstream asset URL')
    data = fetch(url)
    if asset['digest'] != f'sha256:{digest(data)}':
        raise ValueError('Upstream asset does not match its published digest')
    license_url = f"https://raw.githubusercontent.com/{UPSTREAM}/{release['tag_name']}/LICENSE.md"
    if fetch(license_url) != UPSTREAM_LICENSE.read_bytes():
        raise ValueError('Upstream license changed; manual review required')
    database = json.loads(data)
    if len(datasets.flatten(database)) < MIN_UPSTREAM_ENTRIES:
        raise ValueError('Unexpected upstream inventory size; inspect source before updating')
    source = {'upstream-db.raw.json': {'url': url, 'sha256': digest(data)}}
    return release['tag_name'], source, database


def build_japanese(upstream):
    data = fetch(JAPANESE_URL)
    seed = json.loads((ROOT / 'seeds/ja.json').read_text())
    entries = datasets.japanese(upstream, seed, data.decode())
    return entries, {'e-hentaidb.html': {'url': JAPANESE_URL, 'sha256': digest(data)}}


def build_korean(upstream):
    revision = github_json(f'repos/{KOREAN}/commits/master')['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Invalid Korean source revision')
    base = f'https://raw.githubusercontent.com/{KOREAN}/{revision}/'
    if fetch(base + 'LICENSE') != KOREAN_LICENSE.read_bytes():
        raise ValueError('Korean source license changed; manual review required')
    url = base + 'result-korean-merge.json'
    data = fetch(url)
    return datasets.korean(upstream, json.loads(data)), {'korean-tags.json': {'url': url, 'sha256': digest(data)}}


def build_traditional_chinese(upstream):
    import opencc  # Only this dataset needs the pinned converter.
    overrides = json.loads((ROOT / 'overrides/zh-Hant.json').read_text())
    return datasets.traditional_chinese(upstream, opencc.OpenCC('s2twp').convert, overrides), {}


def counts(data):
    entries = datasets.flatten(json.loads(data))
    return {'tags': len(entries), 'named tags': sum(1 for entry in entries.values() if entry['name'])}


def check_not_shrunk(data, previous):
    """Named tags count too: Korean and Japanese keep every upstream key even when their source empties."""
    if previous is None:
        return
    before = counts(previous)
    for label, count in counts(data).items():
        if count < before[label] * (1 - MAX_SHRINK):
            raise ValueError(f'{label} shrank from {before[label]} to {count}; manual source review required')


def build_all(recipes, previous, previous_manifest, upstream_release, upstream_source, accept_shrink=False):
    """Build each dataset in isolation. A failure keeps that language's previous data and is reported.

    One broken source, such as a redesigned Japanese page, must not hold back the other languages. With
    nothing to fall back on, the first release fails outright instead of shipping an incomplete set.
    Reused data keeps its original provenance, so the manifest always describes the bytes it lists.
    """
    built = {}
    for code, recipe in recipes.items():
        try:
            entries, sources = recipe()
            data = datasets.encode(entries)
            if not accept_shrink:
                check_not_shrunk(data, previous.get(code))
            built[code] = {'data': data, 'upstream_release': upstream_release,
                           'sources': {**upstream_source, **sources}, 'failure': None}
        except Exception as error:
            if code not in previous:
                raise RuntimeError(f'{code} failed with no previous release to reuse') from error
            prior = previous_manifest['datasets'][code]
            built[code] = {'data': previous[code], 'upstream_release': prior['upstream_release'],
                           'sources': prior['sources'], 'failure': f'{type(error).__name__}: {error}'}
    return built


def build_manifest(upstream_release, built):
    described = {}
    for code, dataset in built.items():
        described[code] = {
            'file': f'{code}.json',
            'sha256': digest(dataset['data']),
            **counts(dataset['data']),
            'upstream_release': dataset['upstream_release'],
            'sources': dataset['sources'],
            'failure': dataset['failure'],
        }
    return {'upstream_release': upstream_release, 'datasets': described}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    repository = os.environ.get('GITHUB_REPOSITORY', DEFAULT_REPOSITORY)
    accept_shrink = os.environ.get('ACCEPT_SHRINK') == 'true'
    release, source, upstream = load_upstream()
    recipes = {
        'en': lambda: (datasets.english(upstream), {}),
        'ja': lambda: build_japanese(upstream),
        'ko': lambda: build_korean(upstream),
        'zh-Hans': lambda: (datasets.flatten(upstream), {}),
        'zh-Hant': lambda: build_traditional_chinese(upstream),
    }
    with tempfile.TemporaryDirectory() as directory:
        previous, previous_manifest = load_previous(repository, directory)
        built = build_all(recipes, previous, previous_manifest, release, source, accept_shrink)
    arguments.output.mkdir(parents=True, exist_ok=True)
    for code, dataset in built.items():
        (arguments.output / f'{code}.json').write_bytes(dataset['data'])
        if dataset['failure']:
            print(f"::warning::{code} reused its previous data: {dataset['failure']}")
    manifest = build_manifest(release, built)
    (arguments.output / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
