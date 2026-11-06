import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('audit',str(Path(__file__).with_name('audit_v4_whole_weights.py')));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class LogAuditTests(unittest.TestCase):
 def test_exact_all_files(self):m.validate_lines([{'file':'a'},{'file':'b'}],['a: OK','b: OK'])
 def test_missing(self):
  with self.assertRaises(ValueError):m.validate_lines([{'file':'a'},{'file':'b'}],['a: OK'])
 def test_duplicate(self):
  with self.assertRaises(ValueError):m.validate_lines([{'file':'a'},{'file':'b'}],['a: OK','a: OK'])
 def test_failure_and_extra(self):
  for lines in (['a: FAILED'],['a: OK','unknown: OK']):
   with self.subTest(lines=lines),self.assertRaises(ValueError):m.validate_lines([{'file':'a'}],lines)
unittest.main()
