#!/usr/bin/env python3
"""Land Dependabot's updates once they pass their check.

Dependabot only opens pull requests. A GitHub Actions update lands once Validate passes on the new versions. An
OpenCC update lands if it leaves the data unchanged; otherwise it stays for a person, because the next daily run
publishes what the new version builds.
"""
import argparse
import json
import os
from pathlib import Path

from common import BOT_EMAIL, BOT_NAME, differing_datasets, failed_datasets, git, load_manifest, run

ACTIONS_PREFIX = 'dependabot/github_actions/'
OPENCC_PREFIX = 'dependabot/pip/'
REQUIREMENTS = 'requirements.txt'
WORKFLOWS = '.github/workflows/'


def changed_paths(update):
    return [file['path'] for file in update['files']]


def passed_validation(repository, head):
    """Read the newest run, so that a failure someone fixed by re-running it no longer counts."""
    runs = json.loads(run('gh', 'run', 'list', '--repo', repository, '--workflow', 'validate.yml',
                          '--commit', head, '--limit', '1', '--json', 'conclusion'))
    return bool(runs) and runs[0]['conclusion'] == 'success'


def pick(repository):
    """Take the oldest update of each kind that changes only what its check covers.

    Validate's own result is the check for GitHub Actions, so an update that has not passed it is not picked.
    """
    updates = sorted(json.loads(run('gh', 'pr', 'list', '--repo', repository, '--app', 'dependabot',
                                    '--state', 'open', '--json', 'number,headRefName,headRefOid,files')),
                     key=lambda update: update['number'])
    actions = next((update for update in updates
                    if update['headRefName'].startswith(ACTIONS_PREFIX)
                    and all(path.startswith(WORKFLOWS) for path in changed_paths(update))
                    and passed_validation(repository, update['headRefOid'])), None)
    opencc = next((update for update in updates
                   if update['headRefName'].startswith(OPENCC_PREFIX)
                   and changed_paths(update) == [REQUIREMENTS]), None)
    return actions, opencc


def unchanged(baseline, output):
    manifest = load_manifest(output)
    failures = failed_datasets(manifest)
    if failures:
        raise RuntimeError(f'Datasets failed to build on this update: {failures}')
    changed = differing_datasets(load_manifest(baseline), manifest)
    if changed:
        print(f"{', '.join(changed)} changed, so this update waits for a person.")
    return not changed


def land(repository, base, number, head):
    """Put the update on the base branch, rebasing it over anything that landed since it was opened.

    With nothing to rebase over this is a fast-forward, which keeps Dependabot's commit and marks the pull
    request as merged. A rebased commit is a new one, so its pull request has to be closed by hand.
    """
    git('fetch', 'origin', head)
    git('fetch', 'origin', base)
    git('switch', '--detach', head)
    git('-c', f'user.name={BOT_NAME}', '-c', f'user.email={BOT_EMAIL}', 'rebase', 'FETCH_HEAD')
    git('push', 'origin', f'HEAD:{base}')
    if git('rev-parse', 'HEAD').strip() != head:
        run('gh', 'pr', 'close', number, '--repo', repository, '--delete-branch',
            '--comment', f'Landed on `{base}` as a rebased commit.')


def write_outputs(**outputs):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for name, value in outputs.items():
            output.write(f'{name}={value}\n')


def reference(update):
    return f"{update['number']}:{update['headRefOid']}" if update else ''


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('pick')
    comparison = commands.add_parser('compare')
    comparison.add_argument('baseline', type=Path)
    comparison.add_argument('output', type=Path)
    landing = commands.add_parser('land')
    landing.add_argument('references', nargs='*')
    arguments = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    if arguments.command == 'compare':
        write_outputs(unchanged=str(unchanged(arguments.baseline, arguments.output)).lower())
    elif arguments.command == 'land':
        for number, head in (item.split(':') for item in filter(None, arguments.references)):
            land(repository, os.environ['BASE_BRANCH'], number, head)
    else:
        actions, opencc = pick(repository)
        print(f'GitHub Actions update: {reference(actions) or "none ready"}. '
              f'OpenCC update: {reference(opencc) or "none"}.')
        write_outputs(actions=reference(actions), opencc=reference(opencc),
                      opencc_head=opencc['headRefOid'] if opencc else '')


if __name__ == '__main__':
    main()
