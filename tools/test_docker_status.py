import copy
import unittest
from docker_status import assess


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=dict(hostname='qjhs3',image_inspect_exit=0,observed_image_id='expected',expected_image_id='expected',
                           gpus=[dict(index=i,uuid=str(i),used_MiB=0) for i in range(8)],gpu_compute_pids=[])

    def test_idle_never_grants_admission(self):
        result=assess(self.snapshot)
        self.assertEqual(result['status'],'RESOURCE_SNAPSHOT_ONLY')
        self.assertFalse(result['admission'])

    def test_memory_or_process_occupancy_blocks(self):
        for field in ('memory','pid'):
            snapshot=copy.deepcopy(self.snapshot)
            if field=='memory':snapshot['gpus'][0]['used_MiB']=1
            else:snapshot['gpu_compute_pids']=['123']
            self.assertEqual(assess(snapshot)['status'],'BLOCKED')

    def test_missing_image_or_incomplete_cards_blocks(self):
        self.snapshot['image_inspect_exit']=1
        self.assertEqual(assess(self.snapshot)['status'],'BLOCKED')
        self.snapshot['image_inspect_exit']=0
        self.snapshot['gpus'].pop()
        self.assertEqual(assess(self.snapshot)['status'],'BLOCKED')


if __name__=='__main__':unittest.main()
