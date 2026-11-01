import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import release  # noqa: E402

MANIFEST = {
    'upstream_release': 'v1',
    'datasets': {
        'en': {'file': 'en.json', 'sha256': 'aa', 'tags': 2, 'named tags': 0, 'failure': None},
        'ja': {'file': 'ja.json', 'sha256': 'bb', 'tags': 2, 'named tags': 1, 'failure': 'ValueError: broken'},
    },
}


def published(en='sha256:aa', ja='sha256:bb', tag='v1'):
    return {'tag_name': tag, 'assets': [
        {'name': 'en.json', 'digest': en},
        {'name': 'ja.json', 'digest': ja},
        {'name': 'sources.json', 'digest': 'sha256:other'},
    ]}


def listed(tag, published_at=None, author=release.BOT_NAME, identifier=0):
    return {'id': identifier, 'tag_name': tag, 'draft': published_at is None, 'published_at': published_at,
            'author': {'login': author}}


class ChangeDetectionTests(unittest.TestCase):
    def test_no_release_counts_as_changed(self):
        self.assertFalse(release.datasets_unchanged(None, MANIFEST))

    def test_identical_datasets_publish_nothing_even_if_the_manifest_differs(self):
        self.assertTrue(release.datasets_unchanged(published(), MANIFEST))

    def test_any_changed_or_missing_dataset_publishes(self):
        self.assertFalse(release.datasets_unchanged(published(ja='sha256:cc'), MANIFEST))
        self.assertFalse(release.datasets_unchanged({'assets': published()['assets'][:1]}, MANIFEST))

    def test_notes_call_out_reused_data(self):
        notes = release.release_notes(MANIFEST)
        self.assertIn('`en.json`: 2 tags, 0 named\n', notes)
        self.assertIn('previous data reused (ValueError: broken)', notes)


class NextTagTests(unittest.TestCase):
    def tag(self, *taken):
        return release.next_tag('v7.2.1', [{'tag_name': tag} for tag in taken])

    def test_a_new_upstream_release_names_the_release(self):
        self.assertEqual(self.tag(), 'v7.2.1')
        self.assertEqual(self.tag('v7.1.1', 'v7.1.1-2'), 'v7.2.1')

    def test_a_later_release_on_the_same_upstream_takes_a_number(self):
        self.assertEqual(self.tag('v7.2.1'), 'v7.2.1-2')
        self.assertEqual(self.tag('v7.2.1', 'v7.2.1-2'), 'v7.2.1-3')


class VerifyDownloadTests(unittest.TestCase):
    def verify(self, downloaded):
        def fake_run(*command):
            (Path(command[command.index('--dir') + 1]) / 'en.json').write_bytes(downloaded)

        with tempfile.TemporaryDirectory() as directory, patch.object(release, 'run', side_effect=fake_run):
            output = Path(directory)
            (output / 'en.json').write_bytes(b'built')
            release.verify_download('o/r', 'v1', output, ['en.json'])

    def test_accepts_identical_bytes(self):
        self.verify(b'built')

    def test_rejects_different_bytes(self):
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            self.verify(b'corrupt')


class PublishTests(unittest.TestCase):
    def test_uploads_to_a_draft_and_verifies_before_going_live(self):
        calls = []
        with patch.object(release, 'run', side_effect=lambda *command: calls.append(command)), \
             patch.object(release, 'verify_download', side_effect=lambda *_: calls.append(('verify',))), \
             patch.object(release, 'latest_release', return_value=published(tag='v2')):
            release.publish('o/r', 'v2', Path('dist'), MANIFEST, 'sha')
        self.assertEqual([call[:3] if call[0] == 'gh' else call for call in calls], [
            ('gh', 'release', 'create'), ('gh', 'release', 'upload'), ('verify',), ('gh', 'release', 'edit'),
        ])
        self.assertIn('--draft', calls[0])
        self.assertEqual(calls[1][4:7], ('dist/en.json', 'dist/ja.json', 'dist/sources.json'))

    def test_a_release_that_did_not_become_latest_raises(self):
        with patch.object(release, 'run'), patch.object(release, 'verify_download'), \
             patch.object(release, 'latest_release', return_value=published()):
            with self.assertRaisesRegex(RuntimeError, 'did not become the latest'):
                release.publish('o/r', 'v2', Path('dist'), MANIFEST, 'sha')


class CleanupTests(unittest.TestCase):
    def commands(self, action, releases):
        issued = []

        def fake_run(*command):
            if 'releases?per_page=100' in command[-1]:
                return json.dumps(releases)
            issued.append(command)

        with patch.object(release, 'run', side_effect=fake_run):
            action()
        return issued

    def prune(self, releases, keep):
        return self.commands(lambda: release.prune('o/r', keep=keep), releases)

    def test_prune_keeps_the_newest_by_publish_date_not_by_tag(self):
        deleted = self.prune([
            listed('v7.9.1', '2026-11-03T00:00:00Z'),
            listed('v7.10.1', '2026-11-04T00:00:00Z'),
            listed('v7.8.1', '2026-11-02T00:00:00Z'),
        ], keep=2)
        self.assertEqual([command[3] for command in deleted], ['v7.8.1'])
        self.assertIn('--cleanup-tag', deleted[0])

    def test_prune_leaves_hand_made_releases_alone(self):
        deleted = self.prune([
            listed('v7.2.1', '2026-11-04T00:00:00Z'),
            listed('v7.1.1', '2026-11-03T00:00:00Z'),
            listed('backup', '2026-01-01T00:00:00Z', author='someone'),
        ], keep=1)
        self.assertEqual([command[3] for command in deleted], ['v7.1.1'])

    def test_drafts_do_not_push_out_published_releases(self):
        drafts = [listed('v7.3.1', identifier=number) for number in range(5)]
        kept = [listed('v7.2.1', '2026-11-04T00:00:00Z'), listed('v7.1.1', '2026-11-03T00:00:00Z')]
        self.assertEqual(self.prune(drafts + kept, keep=2), [])

    def test_clearing_drafts_goes_by_id_and_only_touches_our_own(self):
        issued = self.commands(lambda: release.clear_drafts('o/r'), [
            listed('v7.3.1', identifier=11),
            listed('v7.3.1', identifier=12),
            listed('v7.4.1', author='someone', identifier=13),
            listed('v7.2.1', '2026-11-04T00:00:00Z', identifier=14),
        ])
        self.assertEqual([command[-1] for command in issued], ['repos/o/r/releases/11', 'repos/o/r/releases/12'])
        self.assertTrue(all(command[:4] == ('gh', 'api', '--method', 'DELETE') for command in issued))


class CommitStateTests(unittest.TestCase):
    def commit(self, state):
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state/latest.json'
            if state is not None:
                path.parent.mkdir()
                path.write_text(json.dumps(state))
            with patch.object(release, 'STATE_PATH', path), \
                    patch.object(release, 'git', side_effect=lambda *arguments: commands.append(arguments[0])):
                release.commit_state('v2', MANIFEST, 'main')
            return commands, json.loads(path.read_text())

    def test_resets_to_the_branch_tip_before_writing_and_pushing(self):
        commands, state = self.commit({'release': 'v1'})
        self.assertEqual(commands[:2], ['fetch', 'reset'])
        self.assertEqual(commands[-2:], ['commit', 'push'])
        self.assertEqual(state['release'], 'v2')

    def test_writes_the_first_state(self):
        self.assertEqual(self.commit(None)[1]['release'], 'v2')

    def test_an_already_recorded_release_commits_nothing(self):
        commands, _ = self.commit({'release': 'v2'})
        self.assertEqual(commands, ['fetch', 'reset'])


class MainTests(unittest.TestCase):
    def run_main(self, latest, manifest, releases=()):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / release.MANIFEST_NAME).write_text(json.dumps(manifest))
            environment = {'GITHUB_REPOSITORY': 'o/r', 'GITHUB_SHA': 'sha', 'BASE_BRANCH': 'main'}
            with patch.dict(release.os.environ, environment), patch('builtins.print'), \
                    patch.object(sys, 'argv', ['release.py', directory]), \
                    patch.object(release, 'latest_release', return_value=latest), \
                    patch.object(release, 'list_releases', return_value=list(releases)), \
                    patch.object(release, 'clear_drafts', side_effect=lambda *_: calls.append('clear')), \
                    patch.object(release, 'publish', side_effect=lambda *_: calls.append('publish')), \
                    patch.object(release, 'prune', side_effect=lambda *_: calls.append('prune')), \
                    patch.object(release, 'commit_state',
                                 side_effect=lambda tag, *_: calls.append(('state', tag))):
                release.main()
        return calls

    HEALTHY = {'upstream_release': 'v1', 'datasets': {
        'en': {'file': 'en.json', 'sha256': 'aa', 'tags': 2, 'named tags': 0, 'failure': None},
        'ja': {'file': 'ja.json', 'sha256': 'bb', 'tags': 2, 'named tags': 1, 'failure': None},
    }}

    def test_unchanged_data_publishes_nothing_but_still_reconciles_state(self):
        self.assertEqual(self.run_main(published(), self.HEALTHY), ['clear', 'prune', ('state', 'v1')])

    def test_changed_data_clears_drafts_then_publishes_then_prunes_then_records(self):
        calls = self.run_main(published(en='sha256:old', tag='v0'), self.HEALTHY, [{'tag_name': 'v0'}])
        self.assertEqual(calls, ['clear', 'publish', 'prune', ('state', 'v1')])

    def test_the_first_release_is_named_after_upstream(self):
        self.assertEqual(self.run_main(None, self.HEALTHY)[-1], ('state', 'v1'))

    def test_changed_data_on_the_same_upstream_takes_a_number(self):
        calls = self.run_main(published(en='sha256:old'), self.HEALTHY, [{'tag_name': 'v1'}])
        self.assertEqual(calls[-1], ('state', 'v1-2'))

    def test_a_reused_dataset_fails_the_run_after_everything_else_finished(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main(published(), MANIFEST)
        self.assertEqual(raised.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
