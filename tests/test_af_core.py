import sys
import json
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
from af_core import scan, export_template, make_plan, publish, AFError
from af_metrics import flux_metrics
from af_compare import compare_records

class Setting:
    def __init__(self, value, fail=False):
        self.value=value;self.fail=fail
    def get_state(self):return self.value
    def set_state(self, value):
        if self.fail:raise RuntimeError('test failure')
        self.value=value

class Node:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
class Named(dict):
    def get_object_names(self):return list(self.keys())

class TestCore(unittest.TestCase):
    def setUp(self):
        self.mass=Setting({'option':'constant','constant':4.166666e-6})
        self.p=Setting(100.0)
        self.pressure=Setting(0.0)
        self.fluid=Setting('nitrogen')
        mass=Node(momentum=Node(mass_flow_rate=self.mass),thermal=Node(temperature=Setting(300.0)))
        pressure=Node(momentum=Node(gauge_pressure=self.pressure),thermal=Node(backflow_total_temperature=Setting(300.0)))
        self.settings=Node(
          setup=Node(general=Node(solver=Node(type=Setting('pressure-based'),time=Setting('steady')),
                                    operating_conditions=Node(operating_pressure=self.p)),
                     models=Node(viscous=Node(model=Setting('laminar')),energy=Node(enabled=Setting(True))),
                     boundary_conditions=Node(mass_flow_inlet=Named({'inlet-a':mass}),pressure_outlet=Named({'outlet-a':pressure})),
                     cell_zone_conditions=Node(fluid=Named({'fluid-region':Node(material=self.fluid)}))),
          solution=Node(methods=Node(p_v_coupling=Node(flow_scheme=Setting('Coupled')),gradient_scheme=Setting('least-square-cell-based'))))

    def template(self):
        d=export_template(scan(self.settings))
        d['verified_with_real_case']=True
        return d

    def test_scan_does_not_modify(self):
        before=self.p.value
        d=scan(self.settings)
        self.assertEqual(d['schema'],'autofluent.snapshot.v1')
        self.assertEqual(before,self.p.value)
        self.assertEqual(len(d['boundaries']),2)
        self.assertEqual(len(d['fluid_zones']),1)

    def test_export_and_preview(self):
        d=self.template();self.assertEqual(len(d['zones']),2)
        plan=make_plan(self.settings,d)
        self.assertEqual(plan['blocker_count'],0)
        self.assertEqual(plan['change_count'],0)
        self.p.value=200
        self.assertEqual(make_plan(self.settings,d)['change_count'],1)

    def test_publish_readback(self):
        d=self.template();self.p.value=200
        signature=make_plan(self.settings,d)['plan_signature']
        result=publish(self.settings,d,'发布工况',signature)
        self.assertEqual(result['status'],'verified_partial_scope')
        self.assertEqual(self.p.value,100)

    def test_unapproved_blocked(self):
        d=self.template();d['verified_with_real_case']=False
        with self.assertRaises(AFError):publish(self.settings,d,'发布工况',make_plan(self.settings,d)['plan_signature'])

    def test_missing_boundary_blocks_without_writes(self):
        d=self.template();d['zones'][0]['zone']='missing'
        self.p.value=200
        plan=make_plan(self.settings,d)
        self.assertEqual(plan['blocker_count'],2)
        with self.assertRaises(AFError):publish(self.settings,d,'发布工况',make_plan(self.settings,d)['plan_signature'])
        self.assertEqual(self.p.value,200)

    def test_failed_setter_rolls_back(self):
        d=self.template();self.p.value=200
        self.mass.value={'option':'constant','constant':9e-6};self.mass.fail=True
        with self.assertRaises(AFError) as context:publish(self.settings,d,'发布工况',make_plan(self.settings,d)['plan_signature'])
        self.assertIn('rollback',str(context.exception))
        self.assertEqual(self.p.value,200)

    def test_additional_unmapped_inlet_blocks(self):
        d=self.template()
        self.settings.setup.boundary_conditions.mass_flow_inlet['inlet-b']=self.settings.setup.boundary_conditions.mass_flow_inlet['inlet-a']
        plan=make_plan(self.settings,d)
        self.assertGreaterEqual(plan['blocker_count'],1)
        self.assertIn('inlet-b',str(plan['items']))

    def test_duplicate_boundary_target_rejected(self):
        d=self.template()
        d['zones'].append(dict(d['zones'][0],role='second-role'))
        with self.assertRaises(AFError):make_plan(self.settings,d)

    def test_missing_essential_global_blocked(self):
        d=self.template()
        d['fields']=[f for f in d['fields'] if f['path']!='setup.general.solver.time']
        with self.assertRaises(AFError):publish(self.settings,d,'发布工况',make_plan(self.settings,d)['plan_signature'])

    def test_material_property_difference_blocks(self):
        d=self.template()
        d['fluid_zones'][0]['material_properties']={'density': 1.23}
        plan=make_plan(self.settings,d)
        self.assertGreaterEqual(plan['blocker_count'],1)

    def test_unknown_path_blocked(self):
        d=self.template();d['fields'].append({'path':'solution.run_calculation.iterate','value':999})
        with self.assertRaises(AFError):make_plan(self.settings,d)


    def test_preview_signature_changes_when_live_field_changes(self):
        d=self.template()
        signature=make_plan(self.settings,d)['plan_signature']
        self.p.value=250
        with self.assertRaisesRegex(AFError,'预检不一致'):
            publish(self.settings,d,'发布工况',signature)
        self.assertEqual(self.p.value,250)

    def test_publish_requires_recent_preview_token(self):
        d=self.template()
        with self.assertRaisesRegex(AFError,'先运行发布前'):
            publish(self.settings,d,'发布工况')

    def test_preview_signature_changes_with_template(self):
        d=self.template()
        old=make_plan(self.settings,d)['plan_signature']
        d['fields'][0]['value']='another-solver'
        with self.assertRaisesRegex(AFError,'预检不一致'):
            publish(self.settings,d,'发布工况',old)


    def test_comparison_blocks_missing_provenance(self):
        raw={'source':'verified-RT0','inlet_mass_kg_s':1e-5,
           'layers':{'upper':[2e-6,2e-6]},'provenance':{'model_id':'11.4'}}
        self.assertEqual(compare_records(raw,raw)['status'],'not_qualified')

    def test_comparison_blocks_different_conditions(self):
        a={'source':'verified-RT0','inlet_mass_kg_s':1e-5,'layers':{'upper':[2e-6,2e-6]},
           'provenance':{'model_id':'11.4','condition_template_sha256':'a'*64,
             'measurement_definition':'ALD-1','partition_rule_version':'1','case_data_identity':'case-a'}}
        b=json.loads(json.dumps(a));b['provenance']['model_id']='11.6';b['provenance']['condition_template_sha256']='b'*64
        result=compare_records(a,b)
        self.assertEqual(result['status'],'not_qualified')
        self.assertIn('condition_template_sha256',str(result['reasons']))

    def test_imported_results_only_preliminary(self):
        a={'source':'verified-RT0','inlet_mass_kg_s':1e-5,'layers':{'upper':[2e-6,2e-6]},
           'provenance':{'model_id':'11.4','condition_template_sha256':'a'*64,
             'measurement_definition':'ALD-1','partition_rule_version':'1','case_data_identity':'case-a'}}
        b=json.loads(json.dumps(a));b['provenance']['model_id']='11.6';b['provenance']['case_data_identity']='case-b'
        b['layers']['upper']=[3e-6,3e-6]
        result=compare_records(a,b)
        self.assertEqual(result['status'],'logical_preliminary_only')
        self.assertAlmostEqual(result['indicators']['upper']['fraction_delta_pp'],20)

    def test_metrics(self):
        d={'source':'verified-RT0','inlet_mass_kg_s':1.25e-5,
           'outlet_net_mass_kg_s':1.249e-5,'layers':{'upper':[1e-6,1e-6,1e-6]}}
        result=flux_metrics(d)
        self.assertAlmostEqual(result['layers']['upper']['cv_pct'],0)
        self.assertAlmostEqual(result['layers']['upper']['fraction_of_inlet_pct'],24)
        self.assertAlmostEqual(result['mass_imbalance_pct'],.08)
        d['source']='user-guessed'
        with self.assertRaises(ValueError):flux_metrics(d)

if __name__=='__main__':unittest.main()
