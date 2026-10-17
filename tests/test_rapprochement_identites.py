import unittest

from scripts.rapprochement_identites import candidats_phonetiques, proposer_candidats


class RapprochementIdentitesTests(unittest.TestCase):
    def test_same_cin_is_pending_candidate_only(self):
        rows = [
            {'id': 'occ-1', 'type': 'personne', 'cin': ' 123 456 '},
            {'id': 'occ-2', 'type': 'personne', 'cin': '123456'},
        ]
        candidate, = proposer_candidats(rows)
        self.assertEqual((candidate['occurrence_a'], candidate['occurrence_b']), ('occ-1', 'occ-2'))
        self.assertEqual(candidate['status'], 'pending')
        self.assertEqual(candidate['method'], 'exact_cin')
        self.assertNotIn('123456', str(candidate['evidence']))

    def test_phonetic_strategy_stays_dormant_even_when_requested(self):
        rows = [{'id': '1', 'name': 'Mohamed'}, {'id': '2', 'name': 'Muhammad'}]
        self.assertEqual(candidats_phonetiques(rows), [])
        self.assertEqual(candidats_phonetiques(rows, enabled=True), [])
        self.assertEqual(proposer_candidats(rows, phonetic_enabled=True), [])


if __name__ == '__main__':
    unittest.main()
