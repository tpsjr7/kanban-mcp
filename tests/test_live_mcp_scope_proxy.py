"""The live client fixture guard must not authorize any production write."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('fixture_proxy', Path(__file__).with_name('live_mcp_scope_proxy.py'))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = {'client': 'codex', 'chat_id': 'exact-worker', 'item_id': 33,
                      'read_items': [33, 34], 'dispatches': ['exact-dispatch'], 'plan': 'docs/fixture.md'}

    def test_reads_and_requests_are_limited_to_fixture_ids(self):
        self.assertTrue(proxy.allowed('workflow_ticket', {'item_id': 34}, self.scope))
        self.assertFalse(proxy.allowed('workflow_ticket', {'item_id': 20}, self.scope))
        args = {'item_id': 33, 'client': 'codex', 'chat_id': 'exact-worker'}
        self.assertTrue(proxy.allowed('workflow_revise_plan', args, self.scope))
        for change in ({'item_id': 20}, {'chat_id': 'foreign'}, {'client': 'opencode'}):
            self.assertFalse(proxy.allowed('workflow_revise_plan', {**args, **change}, self.scope))

    def test_reports_cannot_start_implementation_or_change_scope(self):
        args = {'client': 'codex', 'chat_id': 'exact-worker', 'dispatch_id': 'exact-dispatch',
                'kind': 'plan_ready', 'payload': json.dumps({'plan': 'docs/fixture.md'})}
        self.assertTrue(proxy.allowed('workflow_report', args, self.scope))
        for change in ({'kind': 'integrated'}, {'kind': 'preview_ready'},
                       {'dispatch_id': 'foreign'}, {'payload': json.dumps({'plan': 'docs/product.md'})},
                       {'payload': json.dumps({'plan': 'docs/fixture.md', 'extra': 'value'})}):
            self.assertFalse(proxy.allowed('workflow_report', {**args, **change}, self.scope))
        for name in ('set_status', 'workflow_assign', 'new_item', 'workflow_status'):
            self.assertFalse(proxy.allowed(name, {}, self.scope))

    def test_configuration_must_be_private_owned_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scope.json'
            path.write_text(json.dumps(self.scope))
            path.chmod(0o600)
            self.assertEqual(proxy.configuration(path)['item_id'], 33)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                proxy.configuration(path)
            link = Path(directory) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                proxy.configuration(link)


if __name__ == '__main__':
    unittest.main()
