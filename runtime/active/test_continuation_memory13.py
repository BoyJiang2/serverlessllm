"""Observation accounts existing owned anon; new allocations retain full budget."""
import unittest
from preflight_control13 import validate_memory_budget

class ContinuationMemory(unittest.TestCase):
    def test_initial_allocation_requires_full_remaining_capacity(self):
        weight=756177645648;required=768799145984+weight+64*1024**3
        validate_memory_budget(required,weight)
        with self.assertRaisesRegex(RuntimeError,'insufficient host memory'):
            validate_memory_budget(required-1,weight)

    def test_actual_c4_owned_anon_is_not_budgeted_twice(self):
        available=1192909017088;own_anon=792834703360;weight=756177645648
        with self.assertRaisesRegex(RuntimeError,'insufficient host memory'):
            validate_memory_budget(available,weight)
        validate_memory_budget(available,weight,own_anon)

    def test_continuation_still_requires_uncommitted_working_margin(self):
        with self.assertRaisesRegex(RuntimeError,'working margin'):
            validate_memory_budget(64*1024**3-1,756177645648,2*1024**4)

    def test_invalid_anon_credit_refused(self):
        with self.assertRaisesRegex(RuntimeError,'invalid owned'):
            validate_memory_budget(2*1024**4,756177645648,-1)

if __name__=='__main__':unittest.main()
