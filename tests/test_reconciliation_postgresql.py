"""Régressions des règles d'identité et des reprises, sans base de production."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from regles_identite import identifier_parts, conflict_reason, person_name
from extraction_structuree import organization_label
import reconcilier_entites_postgresql as reconciliation


def company(name='Entreprise Exemple'):
    return dict(nature='personne_morale',label=name)


def observation(kind,value):
    return dict(kind=kind,raw_value=value,attribution='explicite',active=True)


class IdentityRulesTests(unittest.TestCase):
    def test_fiscal_formats_share_the_numeric_identity(self):
        self.assertEqual(identifier_parts('mf','30540R/A/M/000'),('30540','R'))
        self.assertEqual(identifier_parts('mf','030540/R/A/M/000'),('30540','R'))
        self.assertEqual(identifier_parts('identifiant_unique_publie','0030540'),('30540',''))
        rows=[observation('mf','30540R/A/M/000'),observation('mf','030540S/A/M/000')]
        self.assertIsNone(conflict_reason([company(),company()],rows))

    def test_rc_is_not_guessed_from_a_polluted_number(self):
        self.assertEqual(identifier_parts('rc','B131711997'),('131711997',''))
        self.assertIsNone(identifier_parts('rc','B131711997EN'))
        self.assertIsNone(identifier_parts('mf','000000'))

    def test_same_cin_with_different_people_is_merged(self):
        entities=[dict(nature='personne_physique',label='Rami Homrani'),dict(nature='personne_physique',label='Autre Personne')]
        self.assertIsNone(conflict_reason(entities,[observation('cin','01280376')]))
        self.assertEqual(person_name('Mr Khaled Abdeljaoued.'),person_name('Khaled Abdeljaoued'))

    def test_company_prefix_is_cosmetic_for_identity_key(self):
        self.assertEqual(organization_label('Société One Tech Mobile'),'One Tech Mobile')
        self.assertEqual(organization_label('Sté. One Tech Mobile'),'One Tech Mobile')
        self.assertEqual(organization_label('Société Anonyme'),'Société Anonyme')

    def test_name_only_group_does_not_override_distinct_cins(self):
        entities=[dict(nature='personne_physique',label='Ali Ben Ali')]*2
        self.assertEqual(conflict_reason(entities,[observation('cin','01234567'),observation('cin','11234567')]),'CIN contradictoires')

    def test_historical_rc_requires_a_common_fiscal_identity(self):
        rows=[observation('rc','B131711997'),observation('rc','B0322512006')]
        self.assertIsNotNone(conflict_reason([company(),company()],rows))
        self.assertIsNone(conflict_reason([company(),company()],rows+[observation('mf','030540R/A/M/000')]))

    def test_explicit_rename_keeps_unknown_effective_date(self):
        text='''- Changement de la dénomination
sociale de la société absorbante
actuellement dénommée TTE, par
ONE TECH BUSINESS SOLUTIONS
«OTBS».
- L'adoption d'un nouveau mode de gouvernance.'''
        changes=list(reconciliation.extract_name_changes(text))
        self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['old_name'],'TTE')
        self.assertEqual(changes[0]['new_name'],'ONE TECH BUSINESS SOLUTIONS')
        self.assertIsNone(changes[0]['valid_from'])
        self.assertIn('actuellement dénommée TTE',changes[0]['quote'])

    def test_absorption_does_not_rename_all_participants(self):
        text='Fusion par absorption des sociétés INTELCOM et ONE TECH DEVELOPPEMENT par TUNISIAN TELECOM ELECTRIC.'
        self.assertEqual(list(reconciliation.extract_name_changes(text)),[])


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'tmp').mkdir(exist_ok=True)
        self.directory=tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='verification-reconciliation-')
        self.addCleanup(self.directory.cleanup)
        self.plan=reconciliation.open_plan(Path(self.directory.name))
        self.addCleanup(self.plan.close)
        reconciliation.meta(self.plan,'run_id','verification')
        reconciliation.meta(self.plan,'source_counts',{})
        for key in ('a','b','c'):
            self.plan.execute('INSERT INTO groups(id,payload,state) VALUES(?,?,?)',(key,json.dumps({'entity_ids':[key,key+'2']}),'pending'))
        self.plan.commit()
        self.progress=MagicMock(directory=Path(self.directory.name))

    def invoke(self,callback,**options):
        with ExitStack() as stack:
            for name in ('assert_application_stopped','backup','ensure_schema','normalize_identifiers'):
                stack.enter_context(patch.object(reconciliation,name))
            stack.enter_context(patch.object(reconciliation,'connect',return_value=nullcontext(MagicMock())))
            stack.enter_context(patch('migration_postgresql.publish'))
            stack.enter_context(patch.object(reconciliation,'apply_one',side_effect=callback))
            reconciliation.apply_plan(ROOT,self.plan,self.progress,**options)

    def test_error_does_not_stop_following_groups_and_retry_is_selective(self):
        called=[]
        def initial(*args):
            key=args[3];called.append(key)
            if key=='b':raise ValueError('Conflit simulé')
            return dict(merged=1,event_id=key)
        self.invoke(initial)
        self.assertEqual(called,['a','b','c'])
        self.assertEqual(reconciliation.counts(self.plan)['completed'],2)
        self.assertEqual(reconciliation.meta(self.plan,'phase'),'done_with_issues')
        called.clear()
        def retry(*args):
            called.append(args[3]);return dict(merged=1,event_id=args[3])
        self.invoke(retry,retry_errors=True)
        self.assertEqual(called,['b'])
        self.assertEqual(reconciliation.counts(self.plan)['merged'],3)
        self.assertEqual(reconciliation.meta(self.plan,'phase'),'done')

    def test_sample_can_resume_without_repeating_completed_group(self):
        called=[]
        def work(*args):
            called.append(args[3]);return dict(merged=1,event_id=args[3])
        self.invoke(work,limit_groups=1)
        self.assertEqual(called,['a'])
        self.assertEqual(reconciliation.meta(self.plan,'phase'),'paused')
        called.clear();self.invoke(work)
        self.assertEqual(called,['b','c'])


if __name__=='__main__':unittest.main()
