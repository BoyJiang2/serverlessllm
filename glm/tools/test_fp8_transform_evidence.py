import unittest

from fp8_transform_evidence import check_raw_tensor, selected


class TransformEvidenceTests(unittest.TestCase):
    def test_alias_metadata_is_retained_but_not_a_tensor_value_field(self):
        tensor = dict(shape=[2],stride=[1],dtype='torch.float8_e4m3fn',bytes=2,sha256='same')
        self.assertFalse(check_raw_tensor(tensor,tensor,{}))
        self.assertTrue(check_raw_tensor(dict(tensor,alias='base'),tensor,{'base':tensor}))
        with self.assertRaises(AssertionError):
            check_raw_tensor(dict(tensor,alias='base'),tensor,{'base':dict(tensor,sha256='different')})
        for key, value in [('dtype','torch.bfloat16'),('shape',[1,2]),('stride',[2]),('sha256','different')]:
            with self.assertRaises(AssertionError):
                check_raw_tensor(dict(tensor,**{key:value}),tensor,{})

    def test_parameter_alias_paths_are_not_collapsed(self):
        values = {'parameter:model.kv_b_proj.weight':1,
                  'parameter:model.attn.kv_b_proj.weight':2,
                  'attribute:model.W_UV':3,
                  'attribute:model.kv_b_proj.weight':4}
        self.assertEqual(selected(values),dict(list(values.items())[:3]))


if __name__ == '__main__':
    unittest.main()
