"""Compound resource-release contracts with synthetic observations; no Docker/GPU."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import v4_service_once as service


class ServiceControllerContracts(unittest.TestCase):
    def test_default_plan_has_no_gpu_or_docker_calls(self):
        output=io.StringIO()
        with mock.patch('sys.argv',['service','/cpu/new-run','a'*40]), \
                mock.patch.object(service,'call') as call,contextlib.redirect_stdout(output):
            service.main()
        call.assert_not_called()
        plan=json.loads(output.getvalue())
        self.assertFalse(plan['gpu_or_model_started'])
        self.assertEqual(plan['head_cpu']+plan['worker_cpu'],8)
        self.assertEqual(plan['memory_limit_total'],1024**4)
        self.assertEqual(sum(plan['memory_limits'].values()),1024**4)
        self.assertEqual(plan['memory_limits']['head'],16*1024**3)

    def test_gpu_coverage_context_and_foreign_process_remain_strict(self):
        baseline={f'GPU-cpu-{i}':345 for i in range(8)}
        gpu=dict(rows=[[str(i),uuid,'H20','143771','345'] for i,uuid in enumerate(baseline)],
                 apps='\n'.join('123, '+uuid+', 322' for uuid in baseline))
        self.assertTrue(service.model_unloaded(gpu,123,baseline))
        for kind in ('missing','duplicate','foreign','memory','extra'):
            invalid=copy.deepcopy(gpu)
            if kind=='missing':invalid['rows'].pop()
            elif kind=='duplicate':invalid['rows'][-1]=invalid['rows'][0]
            elif kind=='foreign':invalid['apps']=invalid['apps'].replace('123,','999,',1)
            elif kind=='memory':invalid['rows'][0][4]='362'
            else:invalid['apps']+='\n123, GPU-cpu-0, 322'
            with self.subTest(kind=kind):self.assertFalse(service.model_unloaded(invalid,123,baseline))

    def test_both_release_jobs_bind_all_original_processes_and_failure_propagates(self):
        roles={name:dict(container_id=letter*64,created='fixture',image='sha256:fixture')
               for name,letter in (('head','a'),('worker','b'))}
        groups={name:Path('/sys/fs/cgroup/docker-'+value['container_id']) for name,value in roles.items()}
        workers={(11,111):dict(pid=11,start_ticks=111),(22,222):dict(pid=22,start_ticks=222)}
        baseline=dict(rows=[[str(i),f'GPU-cpu-{i}','H20','143771','0'] for i in range(8)])
        clock=dict(boot_id='cpu',time_namespace='fixture')
        with tempfile.TemporaryDirectory() as directory:
            for fail in (False,True):
                def collect(job,path):
                    self.assertEqual(job['worker_processes'],list(workers.values()))
                    self.assertEqual(len(job['baseline_gpu_mib']),8)
                    if fail and path.name=='head-release':raise ValueError('CPU sibling failure')
                with mock.patch.object(service,'collect',side_effect=collect) as observe:
                    if fail:
                        with self.assertRaisesRegex(ValueError,'CPU sibling failure'):
                            service.release_roles(Path(directory),roles,groups,workers,baseline,clock,1,10**30)
                    else:service.release_roles(Path(directory),roles,groups,workers,baseline,clock,1,10**30)
                    self.assertEqual(observe.call_count,2)

    def test_process_reuse_cannot_count_as_the_original_model_process(self):
        stat='123 (CPU fixture) '+' '.join(['S']+['0']*18+['44'])
        with mock.patch.object(Path,'read_text',return_value=stat):
            self.assertTrue(service.process_alive(dict(pid=123,start_ticks=44)))
            self.assertFalse(service.process_alive(dict(pid=123,start_ticks=45)))
        with mock.patch.object(Path,'read_text',side_effect=FileNotFoundError):
            self.assertFalse(service.process_alive(dict(pid=123,start_ticks=44)))


if __name__=='__main__':unittest.main()
