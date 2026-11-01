#!/usr/bin/env python3
"""Publish a built output directory as one complete, verified GitHub release."""
import argparse
import json
import os
import sys
import tempfile
from itertools import count
from pathlib import Path

from common import (BOT_EMAIL, BOT_NAME, MANIFEST_NAME, ROOT, changed_datasets, failed_datasets, git, latest_release,
                    load_manifest, run)

STATE_PATH = ROOT / 'state/latest.json'
# Only the latest release is ever read; the rest are a short rollback window.
KEEP_RELEASES = 7


def datasets_unchanged(release, manifest):
    return not changed_datasets(release, manifest)


def release_notes(manifest):
    lines = [f"Upstream release: `{manifest['upstream_release']}`", '']
    for dataset in manifest['datasets'].values():
        line = f"- `{dataset['file']}`: {dataset['tags']} tags, {dataset['named tags']} named"
        if dataset['failure']:
            line += f", previous data reused ({dataset['failure']})"
        lines.append(line)
    return '\n'.join(lines) + '\n'


def list_releases(repository):
    return json.loads(run('gh', 'api', f'repos/{repository}/releases?per_page=100'))


def next_tag(upstream_release, releases):
    """Name a release after its upstream release, numbering any later one built on that same release.

    The other sources and this pipeline can change the data while upstream stays put, and a tag must stay unique.
    """
    taken = {release['tag_name'] for release in releases}
    if upstream_release not in taken:
        return upstream_release
    return next(tag for number in count(2) if (tag := f'{upstream_release}-{number}') not in taken)


def verify_download(repository, tag, output, names):
    with tempfile.TemporaryDirectory() as directory:
        run('gh', 'release', 'download', tag, '--repo', repository, '--dir', directory)
        for name in names:
            if (Path(directory) / name).read_bytes() != (output / name).read_bytes():
                raise RuntimeError(f'Uploaded {name} differs from the built file')


def publish(repository, tag, output, manifest, target):
    """Upload to a draft and verify it first, so the app never sees a partial or corrupt release."""
    names = [dataset['file'] for dataset in manifest['datasets'].values()] + [MANIFEST_NAME]
    with tempfile.TemporaryDirectory() as directory:
        notes = Path(directory) / 'notes.md'
        notes.write_text(release_notes(manifest))
        run('gh', 'release', 'create', tag, '--repo', repository, '--draft', '--target', target,
            '--title', tag, '--notes-file', str(notes))
    run('gh', 'release', 'upload', tag, *[str(output / name) for name in names], '--repo', repository)
    verify_download(repository, tag, output, names)
    run('gh', 'release', 'edit', tag, '--repo', repository, '--draft=false', '--prerelease=false', '--latest')
    latest = latest_release(repository)
    if latest is None or latest['tag_name'] != tag or not datasets_unchanged(latest, manifest):
        raise RuntimeError('The published release did not become the latest with the built datasets')


def clear_drafts(repository):
    """Delete drafts left behind by failed runs, which would otherwise receive the next upload to that tag.

    Runs never overlap, so any draft of ours is stale. They go by id because several can share one tag name.
    A release someone made by hand is left alone, here and when pruning.
    """
    for release in list_releases(repository):
        if release['draft'] and release['author']['login'] == BOT_NAME:
            run('gh', 'api', '--method', 'DELETE', f"repos/{repository}/releases/{release['id']}")


def prune(repository, keep=KEEP_RELEASES):
    """Keep the newest published releases.

    `published_at` gives the order: tags do not sort by age, and `created_at` is the date of the tagged commit.
    """
    ours = [release for release in list_releases(repository)
            if not release['draft'] and release['author']['login'] == BOT_NAME]
    ours.sort(key=lambda release: release['published_at'], reverse=True)
    for release in ours[keep:]:
        run('gh', 'release', 'delete', release['tag_name'], '--repo', repository, '--cleanup-tag', '--yes')


def commit_state(tag, manifest, branch):
    """Record the latest release in git as the audit trail, unless it is already recorded.

    Every run calls this, so a run that failed between publishing and committing heals on the next one.
    A re-run checks out the commit it first ran on, hence the reset to the branch's current tip. The
    commits also keep the scheduled workflow enabled: GitHub disables schedules after 60 idle days.
    """
    git('fetch', '--depth=1', 'origin', branch)
    git('reset', '--hard', 'FETCH_HEAD')
    if STATE_PATH.exists() and json.loads(STATE_PATH.read_text())['release'] == tag:
        return
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps({'release': tag, **manifest}, ensure_ascii=False, indent=2) + '\n')
    git('config', 'user.name', BOT_NAME)
    git('config', 'user.email', BOT_EMAIL)
    git('add', str(STATE_PATH))
    git('commit', '-m', f'Update tag databases ({tag})')
    git('push', 'origin', f'HEAD:{branch}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    arguments = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    manifest = load_manifest(arguments.output)
    clear_drafts(repository)
    latest = latest_release(repository)
    if datasets_unchanged(latest, manifest):
        tag = latest['tag_name']
        print('Every dataset matches the latest release; nothing to publish.')
    else:
        tag = next_tag(manifest['upstream_release'], list_releases(repository))
        publish(repository, tag, arguments.output, manifest, os.environ['GITHUB_SHA'])
        print(f'Published {tag}.')
    prune(repository)
    commit_state(tag, manifest, os.environ['BASE_BRANCH'])
    failures = failed_datasets(manifest)
    if failures:
        # The healthy languages are out; fail the run so the owner is told about the broken source.
        print(f'Datasets that need review: {failures}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
