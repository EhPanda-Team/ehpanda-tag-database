#!/usr/bin/env python3
"""Move to a newer Python once setup-python can install it and OpenCC ships a wheel for it.

Dependabot cannot do this: it updates the actions in a workflow, not the Python they install. Patch
releases need no help, because `.python-version` names a minor version and setup-python follows it.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from common import BOT_EMAIL, BOT_NAME, ROOT, differing_datasets, failed_datasets, fetch, git, load_manifest, run

VERSION_FILE = ROOT / '.python-version'
REQUIREMENTS = ROOT / 'requirements.txt'
# The list setup-python installs from.
MANIFEST_URL = 'https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json'


def version_key(version):
    return tuple(int(part) for part in version.split('.'))


def newer_minors(manifest, current):
    """List the minor versions newer than `current`, newest first, that have a stable Linux x64 build."""
    minors = {version_key(release['version'])[:2] for release in manifest
              if release['stable'] and any(build['platform'] == 'linux' and build['arch'] == 'x64'
                                           for build in release['files'])}
    return ['.'.join(map(str, minor)) for minor in sorted(minors, reverse=True) if minor > version_key(current)]


def has_wheel(version):
    """Ask pip, which also checks the wheel against the hashes pinned in the requirements.

    Wheels only, because building OpenCC from source needs a C++ toolchain the workflows do not set up.
    """
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
                                 '--require-hashes', '--python-version', version,
                                 '--requirement', str(REQUIREMENTS), '--dest', directory],
                                capture_output=True, text=True)
    if result.returncode != 0:
        print(f'Skipping Python {version}: {result.stderr.strip()}')
    return result.returncode == 0


def branch_name(version):
    return f'automation/python-{version}'


def already_proposed(repository, version):
    """A closed pull request counts too, because closing it was someone's decision."""
    return bool(json.loads(run('gh', 'pr', 'list', '--repo', repository, '--head', branch_name(version),
                               '--state', 'all', '--json', 'number')))


def pick(repository, current, manifest):
    return next((version for version in newer_minors(manifest, current)
                 if not already_proposed(repository, version) and has_wheel(version)), None)


def land(repository, base, version, baseline, output):
    """Push the update to the base branch once this run's tests and build have passed.

    An update that changes the data opens a pull request for a person instead, because the next daily run
    publishes what this Python builds. If another commit lands first the push fails, and next week's run retries.
    """
    manifest = load_manifest(output)
    failures = failed_datasets(manifest)
    if failures:
        raise RuntimeError(f'Datasets failed to build on Python {version}: {failures}')
    changed = differing_datasets(load_manifest(baseline), manifest)
    branch = branch_name(version)
    title = f'Update Python to {version}'
    VERSION_FILE.write_text(f'{version}\n')
    if changed:
        git('switch', '--force-create', branch)
    git('-c', f'user.name={BOT_NAME}', '-c', f'user.email={BOT_EMAIL}', 'commit', '--message', title,
        str(VERSION_FILE))
    if not changed:
        git('push', 'origin', f'HEAD:{base}')
        return
    # Force, because a run that pushed but failed to open the pull request leaves this bot-only branch behind.
    git('push', '--force', 'origin', f'HEAD:refs/heads/{branch}')
    names = ', '.join(f'`{name}`' for name in changed)
    body = (f'The tests and a full build passed on Python {version}, but a build of the same sources on the current '
            f'Python differs in {names}.\n\n'
            'The next daily run publishes what this Python builds, so compare the files before merging.\n')
    run('gh', 'pr', 'create', '--repo', repository, '--base', base, '--head', branch, '--title', title, '--body', body)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('pick')
    landing = commands.add_parser('land')
    landing.add_argument('version')
    landing.add_argument('baseline', type=Path)
    landing.add_argument('output', type=Path)
    arguments = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    if arguments.command == 'land':
        land(repository, os.environ['BASE_BRANCH'], arguments.version, arguments.baseline, arguments.output)
        return
    version = pick(repository, VERSION_FILE.read_text().strip(), json.loads(fetch(MANIFEST_URL)))
    print(f'Moving to Python {version}.' if version else 'No newer Python to move to.')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f"version={version or ''}\n")


if __name__ == '__main__':
    main()
