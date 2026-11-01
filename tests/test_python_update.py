import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import python_update  # noqa: E402
from common import MANIFEST_NAME  # noqa: E402

LINUX = [{'platform': 'linux', 'arch': 'x64'}]
MANIFEST = {'datasets': {
    'en': {'file': 'en.json', 'sha256': 'aa', 'failure': None},
    'ja': {'file': 'ja.json', 'sha256': 'bb', 'failure': None},
}}
CHANGED = {'datasets': {
    'en': {'file': 'en.json', 'sha256': 'aa', 'failure': None},
    'ja': {'file': 'ja.json', 'sha256': 'cc', 'failure': None},
}}


def python(version, stable=True, files=LINUX):
    return {'version': version, 'stable': stable, 'files': files}


class NewerMinorsTests(unittest.TestCase):
    def test_lists_each_newer_minor_once_with_the_newest_first(self):
        manifest = [python('3.15.2'), python('3.15.1'), python('3.14.0'), python('3.13.5'), python('3.12.1')]
        self.assertEqual(python_update.newer_minors(manifest, '3.13'), ['3.15', '3.14'])

    def test_orders_versions_as_numbers(self):
        self.assertEqual(python_update.newer_minors([python('3.9.1'), python('3.10.0')], '3.8'), ['3.10', '3.9'])

    def test_a_minor_needs_a_stable_build(self):
        self.assertEqual(python_update.newer_minors([python('3.16.0-rc.1', stable=False)], '3.13'), [])

    def test_a_minor_needs_a_standard_linux_x64_build(self):
        manifest = [
            python('3.15.0', files=[{'platform': 'linux', 'arch': 'x64-freethreaded'}]),
            python('3.14.0', files=[{'platform': 'darwin', 'arch': 'x64'}]),
        ]
        self.assertEqual(python_update.newer_minors(manifest, '3.13'), [])

    def test_nothing_newer_lists_nothing(self):
        self.assertEqual(python_update.newer_minors([python('3.13.5')], '3.13'), [])


class HasWheelTests(unittest.TestCase):
    def has_wheel(self, returncode):
        result = subprocess.CompletedProcess([], returncode, stderr='ERROR: no matching distribution')
        with patch.object(python_update.subprocess, 'run', return_value=result) as download, patch('builtins.print'):
            return python_update.has_wheel('3.15'), download.call_args.args[0]

    def test_pip_finding_a_wheel_means_yes(self):
        self.assertTrue(self.has_wheel(0)[0])

    def test_pip_failing_means_no(self):
        self.assertFalse(self.has_wheel(1)[0])

    def test_asks_for_a_hash_checked_wheel_of_that_version_only(self):
        command = self.has_wheel(0)[1]
        self.assertIn('--only-binary=:all:', command)
        self.assertIn('--require-hashes', command)
        self.assertEqual(command[command.index('--python-version') + 1], '3.15')


class PickTests(unittest.TestCase):
    MANIFEST = [python('3.15.0'), python('3.14.0'), python('3.13.0')]

    def pick(self, wheels, proposed=()):
        with patch.object(python_update, 'has_wheel', side_effect=lambda version: version in wheels), \
                patch.object(python_update, 'already_proposed', side_effect=lambda _, version: version in proposed):
            return python_update.pick('o/r', '3.13', self.MANIFEST)

    def test_takes_the_newest_version_with_a_wheel(self):
        self.assertEqual(self.pick({'3.15', '3.14'}), '3.15')

    def test_falls_back_when_the_newest_has_no_wheel(self):
        self.assertEqual(self.pick({'3.14'}), '3.14')

    def test_no_wheel_at_all_picks_nothing(self):
        self.assertIsNone(self.pick(set()))

    def test_a_version_already_proposed_is_not_proposed_again(self):
        self.assertIsNone(self.pick({'3.14'}, proposed={'3.14'}))

    def test_any_earlier_pull_request_counts_as_proposed(self):
        with patch.object(python_update, 'run', return_value='[{"number": 1}]') as listing:
            self.assertTrue(python_update.already_proposed('o/r', '3.14'))
        self.assertEqual(listing.call_args.args[listing.call_args.args.index('--state') + 1], 'all')
        with patch.object(python_update, 'run', return_value='[]'):
            self.assertFalse(python_update.already_proposed('o/r', '3.14'))


class LandTests(unittest.TestCase):
    def land(self, manifest):
        git_calls, run_calls = [], []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version_file = root / '.python-version'
            version_file.write_text('3.13\n')
            for name, content in (('baseline', MANIFEST), ('output', manifest)):
                (root / name).mkdir()
                (root / name / MANIFEST_NAME).write_text(json.dumps(content))
            with patch.object(python_update, 'VERSION_FILE', version_file), \
                    patch.object(python_update, 'git', side_effect=lambda *arguments: git_calls.append(arguments)), \
                    patch.object(python_update, 'run', side_effect=lambda *arguments: run_calls.append(arguments)):
                try:
                    python_update.land('o/r', 'main', '3.14', root / 'baseline', root / 'output')
                finally:
                    self.written = version_file.read_text()
        return git_calls, run_calls

    def test_unchanged_data_commits_only_the_version_file_and_pushes_to_the_base_branch(self):
        git_calls, run_calls = self.land(MANIFEST)
        self.assertEqual(self.written, '3.14\n')
        self.assertEqual(len(git_calls), 2)
        self.assertIn('commit', git_calls[0])
        self.assertIn('user.name=github-actions[bot]', git_calls[0])
        self.assertTrue(git_calls[0][-1].endswith('.python-version'))
        self.assertEqual(git_calls[1], ('push', 'origin', 'HEAD:main'))
        self.assertEqual(run_calls, [])

    def test_changed_data_commits_to_a_bot_branch_and_opens_a_pull_request_naming_the_files(self):
        git_calls, run_calls = self.land(CHANGED)
        self.assertEqual(self.written, '3.14\n')
        self.assertEqual(git_calls[0], ('switch', '--force-create', 'automation/python-3.14'))
        self.assertIn('commit', git_calls[1])
        self.assertTrue(git_calls[1][-1].endswith('.python-version'))
        self.assertEqual(git_calls[2], ('push', '--force', 'origin', 'HEAD:refs/heads/automation/python-3.14'))
        command = run_calls[0]
        self.assertEqual(command[:3], ('gh', 'pr', 'create'))
        self.assertEqual(command[command.index('--title') + 1], 'Update Python to 3.14')
        body = command[command.index('--body') + 1]
        self.assertIn('differs in `ja.json`', body)
        self.assertNotIn('en.json', body)

    def test_a_dataset_that_failed_on_this_python_stops_before_anything_changes(self):
        manifest = {'datasets': {'ja': {'file': 'ja.json', 'sha256': 'bb', 'failure': 'ValueError: broken'}}}
        with self.assertRaisesRegex(RuntimeError, 'failed to build on Python 3.14'):
            self.land(manifest)
        self.assertEqual(self.written, '3.13\n')


if __name__ == '__main__':
    unittest.main()
