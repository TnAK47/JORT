import unittest

from scripts.extraire_entites import enrichir, _cin_near


class EnrichirTests(unittest.TestCase):
    def test_cin_is_assigned_to_closest_person_only(self):
        text='Mohamed Jerbi CIN 12345678, Moncef Abidi'
        first=(0,len('Mohamed Jerbi'))
        second=(text.index('Moncef'),len(text))
        self.assertEqual(_cin_near(text,*first,[second]),'12345678')
        self.assertIsNone(_cin_near(text,*second,[first]))

    def test_cin_after_role_is_a_candidate_not_a_merge(self):
        text='Société : Exemple SA\nPrésident : Mohamed Jerbi CIN : 12345678'
        result=enrichir([dict(id='a',text=text,reference='2024R12345SANB2')])
        person=next(e for e in result['entities'] if e['type']=='personne')
        self.assertEqual(person['cin'],'12345678')
        self.assertEqual(person['identity_match_status'],'a_verifier')

    def test_labeled_organisation_and_explicit_commission_roles_have_source_quotes(self):
        text = """Constitution d'une association
Nom : Association Exemple Solidaire
Siège social : Tunis
Commission :
- Présidente de l'association : Ilhem Azazi
- Secrétaire générale : Amna Ennassar
- Trésorière : Latifa Essbouy"""
        result = enrichir([{"id": "a1", "text": text, "category": "a_classer"}])
        self.assertEqual(result["entities"][0]["name"], "Association Exemple Solidaire")
        self.assertEqual(result["entities"][0]["type"], "association")
        self.assertEqual([r["role"] for r in result["relations"]], ["présidente", "secrétaire générale", "trésorière"])
        normalized = " ".join(text.split())
        for relation in result["relations"]:
            self.assertIn(relation["quote"], normalized)
            self.assertEqual(relation["status"], "extrait_automatiquement")

    def test_repeated_founders_are_deduplicated_per_announcement(self):
        text = "Nom de la société à constituer : Société Tabarka Abeilles\nType de société : locale\nFondateurs : Karim Barouali, Hadiya Khadhraoui et Karim Barouali."
        result = enrichir([{"id": "one", "reference": "2024T01234SODB1", "text": text}, {"id": "two", "reference": "2024T01234SODB1", "text": text}])
        people = [entity for entity in result["entities"] if entity["type"] == "personne"]
        karims = [p for p in people if p["name"] == "Karim Barouali"]
        self.assertEqual(len(karims), 2)  # une occurrence par annonce, pas une par répétition du nom
        self.assertEqual({p["announcement_id"] for p in karims}, {"one", "two"})
        self.assertEqual(len(result["relations"]), 4)

    def test_cooccurrence_does_not_create_link_and_ambiguous_notice_stays_unclassified(self):
        announcements = [
            {"id": "a", "text": "Association Exemple\nNom : Association Exemple\nIlhem Azazi a participé à la réunion avec Karim Barouali."},
            {"id": "b", "text": "Avis important\nIlhem Azazi, Karim Barouali"},
        ]
        result = enrichir(announcements)
        self.assertEqual(result["relations"], [])
        self.assertEqual(announcements[1]["category"], "a_classer")

    def test_rejects_long_role_name(self):
        result = enrichir([{"id": "a", "text": "Nom : Association Exemple\nAssociation\nPrésident : Association des Amis de Tunis"}])
        self.assertEqual(result["relations"], [])

    def test_company_name_and_classification_do_not_match_substrings(self):
        result = enrichir([
            {"id": "metal", "reference": "2024R03229SANB2", "text": "MetalPlast S.A\nAdresse : Radès\nCapital social : 100 DT"},
            {"id": "x", "text": "Constitution d'une société\nNom : Société Sans Limite\nUne association est citée dans le texte."},
        ])
        self.assertEqual(result["entities"][0]["name"], "MetalPlast")
        self.assertEqual(result["entities"][0]["type"], "societe")
        self.assertEqual(result["entities"][1]["name"], "Société Sans Limite")
        self.assertEqual(result["entities"][1]["type"], "societe")

    def test_appointment_and_revocation_are_separate_events(self):
        text = "Société : MetalPlast SA\nNomination de Monsieur Mohamed Jerbi en qualité d'administrateur. Révocation de Monsieur Moncef Abidi de son poste d'administrateur."
        result = enrichir([{"id": "metal", "reference": "2024R03229SANB2", "text": text}])
        self.assertEqual([e["name"] for e in result["entities"] if e["type"] == "personne"], ["Mohamed Jerbi", "Moncef Abidi"])
        self.assertEqual([r["event_type"] for r in result["relations"]], ["appointment", "revocation"])
        self.assertIn("Mohamed Jerbi", result["relations"][0]["quote"])
        self.assertEqual(result["relations"][0]["role"], "administrateur")

    def test_explicit_mandates_and_gestion_are_extracted_with_people(self):
        text = """Société : ONE TECH MOBILE
Il a été décidé le renouvellement du mandat de M. Selim Ferchiou en sa qualité de directeur général.
Le renouvellement du mandat des administrateurs suivants : Messieurs Hedi Karim Sellami, Slim Sellami et Selim Ferchiou.
Gérance : Monsieur Hichem Bchir est nommé gérant de la société."""
        result = enrichir([{"id": "roles", "reference": "2017R03405SANB2", "text": text}])
        names = {entity["id"]: entity["name"] for entity in result["entities"]}
        links = {(names[relation["from"]], relation["role"], relation["event_type"])
                 for relation in result["relations"]}
        self.assertIn(("Selim Ferchiou", "directeur général", "renewal"), links)
        self.assertIn(("Selim Ferchiou", "administrateur", "renewal"), links)
        self.assertIn(("Hichem Bchir", "gérant", "appointment"), links)
        self.assertEqual(result["stats"]["personnes"], 4)

    def test_liquidator_is_an_explicit_role(self):
        text = "Société : Exemple SA\nLes associés ont décidé de nommer Mr Hichem Bchir en tant que liquidateur."
        result = enrichir([{"id": "liquidation", "reference": "2010G00420SRLB2", "text": text}])
        person = next(entity for entity in result["entities"] if entity["type"] == "personne")
        relation = next(iter(result["relations"]))
        self.assertEqual(person["name"], "Hichem Bchir")
        self.assertEqual((relation["role"], relation["event_type"]), ("liquidateur", "appointment"))

    def test_collective_appointment_links_each_named_person(self):
        text = """Société : Exemple SA
La nomination de deux nouveaux administrateurs non médecins, en l'occurrence Mr Radhouane Zouari et Mr Salah Kooli, est approuvée."""
        result = enrichir([{"id": "collectif", "reference": "2017R03410SANB2", "text": text}])
        names = {entity["id"]: entity["name"] for entity in result["entities"]}
        links = {(names[relation["from"]], relation["role"], relation["event_type"])
                 for relation in result["relations"]}
        self.assertEqual(links, {("Radhouane Zouari", "administrateur", "appointment"),
                                 ("Salah Kooli", "administrateur", "appointment")})


if __name__ == "__main__":
    unittest.main()
