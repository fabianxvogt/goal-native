import copy
import tempfile
import unittest
from pathlib import Path

from goal_native.store import Store


class ImportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'original')
        self.addCleanup(self.store.close)
        self.goal = self.store.create_goal('Publish local record')
        self.goal = self.store.request(self.goal['id'], 'Permit local mock write', control='allow_effects')
        self.assignment = self.store.assign(self.goal['id'])
        self.invocation = self.store.invoke(self.goal['id'], self.assignment['id'], 'Review candidate')
        self.artifact = self.store.artifact(self.goal['id'], self.invocation['id'], 'candidate', 'report')
        self.evidence = self.store.verify(self.invocation['id'], self.artifact['id'], 'content review',
                                          True, {'reviewer': 'human'}, trusted=True)

    def test_versioned_destination_requires_matching_operation_history(self):
        effect = self.store.prepare_effect(self.invocation['id'], self.artifact['id'], 'report', 0,
                                           self.evidence['id'])
        self.store.approve(effect['id'])
        self.store.commit(effect['id'])
        exported = self.store.export()
        exported['records']['destination_history'] = []
        with self.assertRaises(ValueError):
            Store.import_data(self.root / 'tampered', exported)

    def test_import_cannot_revive_any_active_assignment(self):
        exported = self.store.export()
        duplicate = copy.deepcopy(exported['records']['assignments'][0])
        duplicate['id'] = 'duplicated-assignment'
        exported['records']['assignments'].append(duplicate)
        imported = Store.import_data(self.root / 'imported', exported)
        self.addCleanup(imported.close)
        for identity in (self.assignment['id'], duplicate['id']):
            with self.assertRaises(PermissionError):
                imported.invoke(self.goal['id'], identity, 'Resume unauthorized imported worker')

    def test_replaced_assignment_cannot_support_new_acceptance(self):
        self.store.assign(self.goal['id'])
        with self.assertRaises(PermissionError):
            self.store.accept(self.goal['id'], self.artifact['id'], self.evidence['id'],
                              self.goal['revision'])


if __name__ == '__main__':
    unittest.main()
