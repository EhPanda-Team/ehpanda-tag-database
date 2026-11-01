import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import dependabot_update  # noqa: E402
from common import MANIFEST_NAME  # noqa: E402

HEAD = 'a' * 40


def update(number, branch='dependabot/pip/opencc-1.5', files=('requirements.txt',)):
    return {'number': number, 'headRefName': branch, 'headRefOid': f'{number}' * 40,
            'files': [{'path': path} for path in files]}


def built(ja='bb', failure=None):
    return {'datasets': {
        'en': {'file': 'en.json', 'sha256': 'aa', 'failure': None},
        'ja': {'file': 'ja.json', 'sha256': ja, 'failure': failure},
    }}


def actions_update(number, files=('.github/workflows/validate.yml',)):
    return update(number, branch='dependabot/github_actions/all-updates', files=files)


class PickTests(unittest.TestCase):
    def pick(self, *updates, validated=True):
        with patch.object(dependabot_update, 'run', return_value=json.dumps(updates)) as listing, \
                patch.object(dependabot_update, 'passed_validation', return_value=validated):
            picked = dependabot_update.pick('o/r')
        self.command = listing.call_args.args
        return picked

    def test_takes_the_oldest_opencc_update(self):
        self.assertEqual(self.pick(update(2), update(1))[1]['number'], 1)

    def test_takes_the_oldest_github_actions_update(self):
        self.assertEqual(self.pick(actions_update(2), actions_update(1))[0]['number'], 1)

    def test_takes_one_of_each_kind(self):
        actions, opencc = self.pick(update(1), actions_update(2))
        self.assertEqual((actions['number'], opencc['number']), (2, 1))

    def test_asks_only_for_dependabots_open_pull_requests(self):
        self.pick()
        self.assertEqual(self.command[self.command.index('--app') + 1], 'dependabot')
        self.assertEqual(self.command[self.command.index('--state') + 1], 'open')

    def test_leaves_a_github_actions_update_that_has_not_passed_validation(self):
        self.assertIsNone(self.pick(actions_update(1), validated=False)[0])

    def test_validation_says_nothing_about_opencc_updates(self):
        self.assertEqual(self.pick(update(1), validated=False)[1]['number'], 1)

    def test_leaves_an_opencc_update_that_touches_anything_else(self):
        self.assertIsNone(self.pick(update(1, files=('requirements.txt', 'scripts/update.py')))[1])

    def test_leaves_a_github_actions_update_that_touches_anything_else(self):
        self.assertIsNone(self.pick(actions_update(1, files=('.github/workflows/a.yml', 'README.md')))[0])

    def test_leaves_other_ecosystems_alone(self):
        self.assertEqual(self.pick(update(1, branch='dependabot/npm_and_yarn/left-pad')), (None, None))

    def test_no_update_picks_nothing(self):
        self.assertEqual(self.pick(), (None, None))


class PassedValidationTests(unittest.TestCase):
    def passed(self, runs):
        with patch.object(dependabot_update, 'run', return_value=json.dumps(runs)) as listing:
            result = dependabot_update.passed_validation('o/r', HEAD)
        self.command = listing.call_args.args
        return result

    def test_the_newest_run_succeeding_passes(self):
        self.assertTrue(self.passed([{'conclusion': 'success'}]))
        self.assertEqual(self.command[self.command.index('--workflow') + 1], 'validate.yml')
        self.assertEqual(self.command[self.command.index('--commit') + 1], HEAD)
        self.assertEqual(self.command[self.command.index('--limit') + 1], '1')

    def test_any_other_conclusion_does_not(self):
        for conclusion in ('failure', 'action_required', 'cancelled', ''):
            self.assertFalse(self.passed([{'conclusion': conclusion}]))

    def test_no_run_does_not(self):
        self.assertFalse(self.passed([]))


class ReferenceTests(unittest.TestCase):
    def test_joins_the_number_and_head(self):
        self.assertEqual(dependabot_update.reference(update(7)), f"7:{'7' * 40}")

    def test_no_update_is_empty(self):
        self.assertEqual(dependabot_update.reference(None), '')


class UnchangedTests(unittest.TestCase):
    def unchanged(self, manifest):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in (('baseline', built()), ('output', manifest)):
                (root / name).mkdir()
                (root / name / MANIFEST_NAME).write_text(json.dumps(content))
            with patch('builtins.print') as self.printed:
                return dependabot_update.unchanged(root / 'baseline', root / 'output')

    def test_identical_builds_are_unchanged(self):
        self.assertTrue(self.unchanged(built()))

    def test_a_differing_file_is_changed_and_named(self):
        self.assertFalse(self.unchanged(built(ja='cc')))
        self.assertIn('ja.json', self.printed.call_args.args[0])
        self.assertNotIn('en.json', self.printed.call_args.args[0])

    def test_a_dataset_that_failed_stops_the_run(self):
        with self.assertRaisesRegex(RuntimeError, 'failed to build'):
            self.unchanged(built(failure='ValueError: broken'))


class LandTests(unittest.TestCase):
    def land(self, tip):
        git_calls, run_calls = [], []

        def git(*arguments):
            git_calls.append(arguments)
            return f'{tip}\n'

        with patch.object(dependabot_update, 'git', side_effect=git), \
                patch.object(dependabot_update, 'run', side_effect=lambda *arguments: run_calls.append(arguments)):
            dependabot_update.land('o/r', 'main', '7', HEAD)
        return git_calls, run_calls

    def test_rebases_the_tested_commit_as_the_bot_and_pushes_it_to_the_base_branch(self):
        git_calls, _ = self.land(HEAD)
        self.assertEqual(git_calls[:3], [('fetch', 'origin', HEAD), ('fetch', 'origin', 'main'),
                                         ('switch', '--detach', HEAD)])
        self.assertEqual(git_calls[3][-2:], ('rebase', 'FETCH_HEAD'))
        self.assertIn('user.name=github-actions[bot]', git_calls[3])
        self.assertEqual(git_calls[4], ('push', 'origin', 'HEAD:main'))

    def test_a_fast_forward_leaves_the_pull_request_to_github(self):
        self.assertEqual(self.land(HEAD)[1], [])

    def test_a_rebased_commit_closes_the_pull_request(self):
        command = self.land('b' * 40)[1][0]
        self.assertEqual(command[:4], ('gh', 'pr', 'close', '7'))
        self.assertIn('--delete-branch', command)


if __name__ == '__main__':
    unittest.main()
