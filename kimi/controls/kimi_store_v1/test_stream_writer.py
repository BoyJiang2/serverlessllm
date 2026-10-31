import hashlib
import gc
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

import stream_writer as writer


class StreamTests(unittest.TestCase):
    def tracked_reader(self):
        class Block(bytearray):pass
        references=[];requests=[]
        original=Path.open
        class Reader:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def fileno(self):return self.stream.fileno()
            def seek(self,offset):return self.stream.seek(offset)
            def read(self,count):
                if references:self_test.assertIsNone(references[-1](),'prior read buffer still retained')
                requests.append(count)
                block=Block(self.stream.read(count));references.append(weakref.ref(block));return block
        self_test=self
        return references,requests,lambda path,*a,**kw:Reader(original(path,*a,**kw))

    def test_verified_read_buffer_released_before_next_file_read(self):
        references,requests,opened=self.tracked_reader()
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'candidate'
            records=writer.write_records(target,[self.entry('x',b'abcdefghijkl')])
            with patch.object(Path,'open',new=opened), \
                    patch.object(writer,'CHUNK_BYTES',4):
                writer.verify_records(target,records)
            self.assertEqual(requests,[4,4,4])
            self.assertTrue(all(reference() is None for reference in references))

    def test_digest_failure_traceback_does_not_retain_consumer_read_buffer(self):
        references,requests,opened=self.tracked_reader()
        class Digest:
            def update(self,data):
                del data  # Simulate a C-call error, without a Python callback argument anchor.
                raise RuntimeError('digest fault control')
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'candidate'
            records=writer.write_records(target,[self.entry('x',b'abcdefgh')])
            failure=None
            with patch.object(Path,'open',new=opened),patch.object(writer,'CHUNK_BYTES',4), \
                    patch.object(writer.hashlib,'sha256',return_value=Digest()):
                try:writer.verify_records(target,records)
                except RuntimeError as error:failure=error
            self.assertIsNotNone(failure)
            self.assertIsNotNone(failure.__traceback__)
            self.assertEqual(requests,[4])
            self.assertIsNone(references[0]())
            self.assertTrue((target/'tensor.data_0').is_file())

    def entry(self, name, raw):
        return name, len(raw), (raw[i:i + 3] for i in range(0, len(raw), 3)), hashlib.sha256(raw).hexdigest()

    def test_native_record_and_partition_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'candidate'
            records = writer.write_records(target, [self.entry('a', b'abc'),
                self.entry('b', b'0123456789'), self.entry('c', b'\xff\x80')], partition_bytes=24)
            self.assertEqual([records[n]['offset'] for n in ['a', 'b', 'c']], [0, 8, 4096])
            self.assertEqual((target / 'tensor.data_0').read_bytes(),
                             b'abc' + bytes(5) + b'0123456789' + bytes(4078))
            self.assertEqual((target / 'tensor.data_1').stat().st_size, 4096)
            writer.verify_records(target, records)

    def test_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'candidate'
            records = writer.write_records(target, [self.entry('a', b'packed')])
            with (target / 'tensor.data_0').open('r+b') as stream:
                stream.write(b'X')
            with self.assertRaises(ValueError):
                writer.verify_records(target, records)

    def test_empty_and_oversized_single_record_follow_native_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'candidate'
            records = writer.write_records(target, [self.entry('a', b'x' * 40),
                self.entry('empty', b''), self.entry('c', b'z')], partition_bytes=24)
            self.assertEqual(records['a']['offset'], 0)
            self.assertEqual(records['empty']['offset'], 4096)
            self.assertEqual(records['c']['offset'], 4096)
            writer.verify_records(target, records)

    def test_bad_length_digest_and_duplicate_fail(self):
        for entries in [[('x', 4, [b'a'], None)], [('x', 1, [b'ab'], None)],
                        [('x', 1, [b'a'], '0' * 64)],
                        [self.entry('x', b'a'), self.entry('x', b'b')]]:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    writer.write_records(Path(directory) / 'candidate', entries)

    def test_existing_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                writer.write_records(directory, [])

    def test_short_writes_and_zero_progress(self):
        class ShortStream(io.BytesIO):
            def write(self, data):
                return super().write(data[:2])
        stream = ShortStream()
        writer._write_all(stream, b'abcdefg')
        self.assertEqual(stream.getvalue(), b'abcdefg')
        class StalledStream:
            def write(self, data):
                return 0
        with self.assertRaises(OSError):
            writer._write_all(StalledStream(), b'x')

    def test_invalid_read_ranges_fail_before_file_access(self):
        base = dict(partition=0, bytes=1, local_offset=0, sha256='0' * 64)
        for field in ('partition', 'bytes', 'local_offset'):
            for value in (-1, True, 0.5):
                row = dict(base, **{field: value})
                with patch.object(Path, 'open') as opened:
                    with self.assertRaises(ValueError):
                        writer.verify_records('unused', {'bad': row})
                    opened.assert_not_called()

    def test_out_of_file_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'candidate'
            records = writer.write_records(target, [self.entry('a', b'packed')])
            records['a']['bytes'] = 4097
            with self.assertRaises(ValueError):
                writer.verify_records(target, records)

    def test_consumed_buffer_released_before_next_producer_allocation(self):
        class Buffer(bytearray):
            pass
        released = []
        def chunks():
            for _ in range(2):
                block = Buffer(b'abcd')
                reference = weakref.ref(block)
                yield block
                del block
                gc.collect()
                released.append(reference() is None)
        with tempfile.TemporaryDirectory() as temp:
            writer.write_records(Path(temp) / 'candidate', [('x', 8, chunks(), None)])
        self.assertEqual(released, [True, True])


if __name__ == '__main__':
    unittest.main()
