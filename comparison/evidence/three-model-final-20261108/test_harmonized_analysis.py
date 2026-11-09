import unittest
from harmonized_analysis import span


class EndpointArithmetic(unittest.TestCase):
    def pairs(self):
        return [dict(rank=r,begin_ns=r*10**9,end_ns=(r+2)*10**9) for r in range(8)]

    def test_envelope_is_not_maximum_single_rank(self):
        self.assertEqual(span(self.pairs()),(9.0,2.0))

    def test_missing_rank_is_null(self):
        self.assertEqual(span(self.pairs()[:-1]),(None,None))

    def test_missing_endpoint_is_null(self):
        pairs=self.pairs();pairs[0]['end_ns']=None
        self.assertEqual(span(pairs),(None,None))

    def test_duplicate_rank_is_null(self):
        pairs=self.pairs();pairs[0]['rank']=1
        self.assertEqual(span(pairs),(None,None))

    def test_reversed_endpoint_is_error(self):
        pairs=self.pairs();pairs[0]['end_ns']=-1
        with self.assertRaises(ValueError):span(pairs)


if __name__=='__main__':
    unittest.main()
