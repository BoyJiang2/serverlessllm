"""Acceptance rejects incomplete ranks, wrong physical binding and bad data."""
import copy
import importlib.util
import pathlib
import os
import json
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('tp8_controller', pathlib.Path(__file__).resolve().parents[1]/'gpu512_once_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class AcceptanceTest(unittest.TestCase):
    def messages(self):
        rows = [{'event':'PYTHON_ENTERED'} for _ in range(8)]
        for rank, uuid in enumerate(controller.UUIDS):
            rows.append({'event':'TP8_CUDA_NCCL_MICRO_PASS','rank':rank,'local_rank':rank,'world_size':8,'device_count':8,'device_uuid':uuid,'cuda_visible_count':8,'nvidia_visible':'void','master_addr':'127.0.0.1','master_port':'29500','payload_bytes':1048576,'dtype':'torch.float32','reduced_value':28,'raw_sha256':'2e042dc2f9f98002bb4fa72e283fcbfe046c03321dfc68f094834ca03891b814','allocator_peak_bytes':1048576,'python_entered_monotonic':100.0,'rank_ready_monotonic':102.0,'nccl_init_seconds':1.0})
        return rows

    def test_complete_result(self):
        self.assertEqual(len(controller.validate_messages(self.messages())),8)

    def test_missing_duplicate_or_unentered_rank(self):
        original = self.messages()
        for rows in (original[:-1], original+[original[-1]], original[1:]):
            with self.subTest(rows=len(rows)), self.assertRaises(AssertionError):
                controller.validate_messages(rows)
        rows = copy.deepcopy(original); rows[-1]['rank'] = 0
        with self.assertRaises(AssertionError): controller.validate_messages(rows)

    def test_wrong_binding_collective_or_payload(self):
        for key, value in [('device_uuid',controller.UUIDS[1]),('local_rank',1),('world_size',7),('device_count',7),('payload_bytes',1),('dtype','torch.float16'),('reduced_value',27),('raw_sha256','0'*64),('allocator_peak_bytes',0),('nccl_init_seconds',float('nan')),('rank_ready_monotonic',99.0)]:
            rows = self.messages(); rows[8][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                controller.validate_messages(rows)

    def test_wrong_delivered_environment(self):
        # The controller pins what the worker actually received, so a runtime that
        # stops rewriting to "void", or a rendezvous that silently falls back to a
        # resolvable hostname, fails here instead of passing unnoticed.
        for key, value in [('nvidia_visible',','.join(controller.UUIDS)),('nvidia_visible','all'),
                           ('nvidia_visible',None),('master_addr','localhost'),('master_addr','::1'),
                           ('master_port','29501'),('master_port',29500),('cuda_visible_count',7),
                           ('cuda_visible_count',None)]:
            rows = self.messages(); rows[8][key] = value
            with self.subTest(key=key, value=value), self.assertRaises((AssertionError, KeyError)):
                controller.validate_messages(rows)

class ConfigurationTest(unittest.TestCase):
    def configuration(self):
        return {'Image':controller.IMAGE,
                'HostConfig':{'PidsLimit':4096,'Memory':16*(1<<30),'MemorySwap':16*(1<<30),'NanoCpus':32*10**9,'ShmSize':8*(1<<30),'ReadonlyRootfs':True,'NetworkMode':'none','Runtime':'nvidia','RestartPolicy':{'Name':'no'},'Tmpfs':{'/tmp':'rw,nosuid,nodev,size=64m'},'Ulimits':[{'Name':'core','Soft':0,'Hard':0}]},
                'Config':{'User':'2147:2147','Env':['CUDA_VISIBLE_DEVICES='+','.join(controller.UUIDS),'NVIDIA_VISIBLE_DEVICES='+','.join(controller.UUIDS),'OMP_NUM_THREADS=1','MKL_NUM_THREADS=1','OPENBLAS_NUM_THREADS=1'],
                          'Entrypoint':['python3'],'Cmd':['-I','-m','torch.distributed.run','--rdzv-backend=static','--nnodes=1','--node-rank=0','--master-addr=127.0.0.1','--master-port=29500','--nproc_per_node=8','--max_restarts=0','--no-python','/usr/bin/python3','-I','/source/sllm_store/tests/cuda_micro_probe.py','--tp8']},
                'Mounts':[{'Source':str(controller.ROOT/'sllm_store'),'Destination':'/source/sllm_store','RW':False}]}

    def test_static_loopback_configuration(self):
        controller.validate_create_request(self.configuration())

    def test_reject_dynamic_hostname_restart_or_network_change(self):
        for old, new in [('--rdzv-backend=static','--standalone'),('--master-addr=127.0.0.1','--master-addr=unresolvable-host'),('--max_restarts=0','--max_restarts=1')]:
            row = self.configuration()
            row['Config']['Cmd'] = [new if flag == old else flag for flag in row['Config']['Cmd']]
            with self.subTest(flag=old), self.assertRaises(AssertionError):
                controller.validate_create_request(row)
        row = self.configuration(); row['HostConfig']['NetworkMode'] = 'host'
        with self.assertRaises(AssertionError): controller.validate_create_request(row)

class RealTorchrunCpuTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('KIMI_REAL_TORCHRUN_CPU_TEST') == '1', 'requires reviewed immutable CPU-only runtime')
    def test_actual_static_worker_environment(self):
        self.assertFalse(list(pathlib.Path('/dev').glob('nvidia*')))
        self.assertEqual(os.environ.get('CUDA_VISIBLE_DEVICES'), '')
        self.assertEqual(os.environ.get('NVIDIA_VISIBLE_DEVICES'), 'void')
        configuration = ConfigurationTest().configuration()
        controller.validate_create_request(configuration)
        # This fixture is accepted by the same exact-command admission used by
        # the real controller. Only replace the worker after --no-python.
        command = configuration['Config']['Cmd']
        prefix = command[:command.index('--no-python')+1]
        worker = ("import json,os,pathlib,sys; "
                  "assert 'torch' not in sys.modules; "
                  "assert not list(pathlib.Path('/dev').glob('nvidia*')); "
                  "print(json.dumps({'event':'CPU_STATIC_WORKER_ENV',**{k:os.environ.get(k) for k in "
                  "('RANK','LOCAL_RANK','WORLD_SIZE','MASTER_ADDR','MASTER_PORT','CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES')}}),flush=True)")
        result = subprocess.run(['/usr/bin/python3',*prefix,'/usr/bin/python3','-I','-c',worker],
                                capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr[-6000:])
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertEqual(len(rows),8,result.stdout)
        self.assertEqual({int(row['RANK']) for row in rows},set(range(8)))
        for row in rows:
            self.assertEqual(row['event'],'CPU_STATIC_WORKER_ENV')
            self.assertEqual(row['LOCAL_RANK'],row['RANK'])
            self.assertEqual(row['WORLD_SIZE'],'8')
            self.assertEqual((row['MASTER_ADDR'],row['MASTER_PORT']),('127.0.0.1','29500'))
            self.assertEqual((row['CUDA_VISIBLE_DEVICES'],row['NVIDIA_VISIBLE_DEVICES']),('','void'))
        print(json.dumps({'event':'CPU_STATIC_RENDEZVOUS_ENV_PASS','command_prefix':prefix,'workers':rows}),flush=True)

if __name__ == '__main__': unittest.main()
