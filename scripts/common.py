"""Names, commands and GitHub lookups shared by the build, publish and update steps."""
import json
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = 'sources.json'
BOT_NAME = 'github-actions[bot]'
BOT_EMAIL = '41898282+github-actions[bot]@users.noreply.github.com'


def run(*command):
    return subprocess.check_output(command, text=True)


def git(*arguments):
    return run('git', '-C', str(ROOT), *arguments)


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'ehpanda-tag-database'}), timeout=60) as response:
        return response.read()


def load_manifest(output):
    return json.loads((output / MANIFEST_NAME).read_text())


def failed_datasets(manifest):
    return {code: dataset['failure'] for code, dataset in manifest['datasets'].items() if dataset['failure']}


def differing_datasets(baseline, manifest):
    """List the files that differ between two builds, which tells whether updating the build changes the data.

    The baseline is a build of the same sources moments earlier, because the sources may have moved since the
    latest release, and that alone says nothing about the update.
    """
    return [dataset['file'] for code, dataset in manifest['datasets'].items()
            if baseline['datasets'][code]['sha256'] != dataset['sha256']]


def changed_datasets(release, manifest):
    """List the built files that differ from a release.

    Datasets only, because `sources.json` also records sources that produced identical data.
    """
    published = {asset['name']: asset.get('digest') for asset in release['assets']} if release else {}
    return [dataset['file'] for dataset in manifest['datasets'].values()
            if published.get(dataset['file']) != f"sha256:{dataset['sha256']}"]


def latest_release(repository):
    """Return the latest published release, or None for a repository with no release yet."""
    listing = subprocess.run(['gh', 'api', f'repos/{repository}/releases/latest'], capture_output=True, text=True)
    if listing.returncode != 0:
        if 'HTTP 404' in listing.stderr:
            return None
        raise RuntimeError(f'Cannot read the latest release: {listing.stderr}')
    return json.loads(listing.stdout)
