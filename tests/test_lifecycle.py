"""Cross-domain correctness scenarios, NOT model performance evidence."""
import tempfile
import unittest

from goal_native.store import Store


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)

    def invocation(self, goal):
        assignment = self.store.assign(goal['id'])
        return self.store.invoke(goal['id'], assignment['id'], {'outcome': goal['outcome']})

    def test_quantity_revision_preserves_sources_not_old_acceptance_basis(self):
        goal = self.store.create_goal(
            'Compare delivered costs for 50 units to Berlin',
            criteria='Include shipping, cite terms, keep unknown costs explicit',
            constraints='Local draft; do not contact suppliers or order')
        invocation = self.invocation(goal)
        source = self.store.artifact(goal['id'], invocation['id'], 'source',
            'A: EUR 10/unit; EUR 8/unit at 100 units; shipping EUR 30. '
            'B: EUR 9/unit; shipping EUR 50. C: EUR 7/unit; shipping unknown.',
            name='Supplier terms', limitations='Commercial terms supplied as synthetic fixtures',
            trust='source')
        old = self.store.artifact(goal['id'], invocation['id'], 'comparison',
            'At 50 units: A EUR 530, B EUR 500, C EUR 350 plus unknown shipping.',
            inputs=[source['id']], limitations='Quantity tier coverage incomplete')
        evidence = self.store.verify(invocation['id'], old['id'], '50-unit arithmetic',
            True, {'quantity': 50, 'totals': {'A': 530, 'B': 500}}, trusted=True)
        revised = self.store.revise(goal['id'], goal['revision'],
            'Compare delivered costs for 100 units to Berlin',
            'Include shipping, cite terms, keep unknown costs explicit',
            'Local draft; do not contact suppliers or order')
        with self.assertRaises((PermissionError, ValueError)):
            self.store.accept(goal['id'], old['id'], evidence['id'], revised['revision'])
        resumed = self.invocation(revised)
        current = self.store.artifact(goal['id'], resumed['id'], 'comparison',
            'At 100 units: A EUR 830, B EUR 950, C EUR 700 plus unknown shipping. '
            'A is cheapest among quotes with complete delivered costs; C cannot be ranked.',
            inputs=[source['id']], limitations='C shipping unresolved; no supplier contact')
        checked = self.store.verify(resumed['id'], current['id'], '100-unit arithmetic and tier',
            True, {'quantity': 100, 'totals': {'A': 830, 'B': 950},
                   'unresolved': ['C shipping']}, trusted=True)
        accepted = self.store.accept(goal['id'], current['id'], checked['id'], revised['revision'])
        self.assertIsInstance(accepted, dict)
        products = self.store.artifacts(goal['id'])
        self.assertEqual(next(p for p in products if p['id'] == source['id'])['content'], source['content'])
        self.assertEqual(next(p for p in products if p['id'] == current['id'])['inputs'], [source['id']])

    def test_unexpressed_semantics_are_not_created_on_replacement(self):
        goal = self.store.create_goal('Investigate duplicate job submission')
        first = self.invocation(goal)
        self.store.receipt(first['id'], 'run', {'command': 'python repro.py'},
                           {'exit_code': 1, 'stdout': 'duplicate IDs observed'})
        self.store.finish(first['id'], 'interrupted')
        replacement = self.invocation(self.store.goal(goal['id']))
        self.assertNotEqual(first['id'], replacement['id'])
        # A receipt is a tool observation, not a fabricated root cause or next plan.
        with self.assertRaises((PermissionError, ValueError)):
            self.store.invoke(goal['id'], first['assignment_id'], 'stale assignment')
        self.assertFalse(any(p['kind'] in {'finding', 'plan', 'conclusion'}
                             for p in self.store.artifacts(goal['id'])))

    def test_response_loss_reconciles_after_revocation_without_second_effect(self):
        goal = self.store.create_goal('Update mock application record')
        goal = self.store.request(goal['id'], 'Permit this mock record update', control='allow_effects')
        invocation = self.invocation(goal)
        candidate = self.store.artifact(goal['id'], invocation['id'], 'candidate',
                                        'Approved report', name='Report')
        evidence = self.store.verify(invocation['id'], candidate['id'], 'human content review',
                                      True, {'reviewer': 'fixture human'}, trusted=True)
        effect = self.store.prepare_effect(invocation['id'], candidate['id'], 'report', 0, evidence['id'])
        self.store.approve(effect['id'])
        self.store.commit(effect['id'], lose_response=True)
        self.store.request(goal['id'], 'Stop all further effects', control='draft')
        self.store.reconcile(effect['id'])
        destination = self.store.destination('report')
        self.assertEqual(destination['content'], 'Approved report')
        self.assertEqual(destination['version'], 1)
        self.assertEqual(destination['operation_id'], effect['operation_id'])
        self.store.reconcile(effect['id'])
        self.assertEqual(self.store.destination('report')['version'], 1)


if __name__ == '__main__':
    unittest.main()
