
import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import importlib.util
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))

class FlowDiagnosticsTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.find_spec('af_flow_diagnostics')
        self.assertIsNotNone(spec,'AF has no axial/pressure localization module')
        import af_flow_diagnostics
        return af_flow_diagnostics

    def test_axis_variance_distinguishes_common_trend_from_row_imbalance(self):
        channels=[{'channel_id':f'R{r}-G{i}','row':r,'gap_index':i,'x_center_m':float(i),
                   'net_kg_s':q} for r in (1,2) for i,q in enumerate((1.,2.,1.))]
        result=self.module().channel_diagnostics({'upper':{'channels':channels}})['upper']
        self.assertAlmostEqual(result['axial_variance_pct'],100.)
        self.assertAlmostEqual(result['weakest_mean_pct'],75.)
        self.assertAlmostEqual(sum(x['variance_share_pct'] for x in result['x_bins']),100.)
        self.assertEqual(result['weakest']['net_kg_s'],1.)

    def test_uniform_and_single_row_do_not_invent_variance_decomposition(self):
        channels=[{'channel_id':str(i),'row':1,'gap_index':i,'x_center_m':i,'net_kg_s':2.} for i in range(3)]
        result=self.module().channel_diagnostics({'upper':{'channels':channels}})['upper']
        self.assertEqual(result['cv_pct'],0.)
        self.assertIsNone(result['axial_variance_pct'])
        self.assertTrue(all(x['variance_share_pct'] is None for x in result['x_bins']))

    def test_staggered_rows_are_not_paired_by_gap_index(self):
        channels=[{'channel_id':f'R{r}-G{i}','row':r,'gap_index':i,'x_center_m':i+(r-1)*.5,
                   'net_kg_s':q} for r in (1,2) for i,q in enumerate((1.,2.,1.))]
        result=self.module().channel_diagnostics({'upper':{'channels':channels}})['upper']
        self.assertIsNone(result['axial_variance_pct'])

    def test_incomplete_field_fails_closed(self):
        with self.assertRaises(ValueError):
            self.module().pressure_statistics(np.array([1.,1.]),np.array([2.,np.nan]),np.array([2.,2.]),np.array([3.,3.]),np.array([3.,3.]))

    def test_constant_pressure_and_reverse_flux_are_explicit(self):
        s=self.module().pressure_statistics(np.array([2.,-1.]),*[np.array([p,p]) for p in (2.,2.,3.,3.)])
        self.assertEqual(s['total_pa'],3.)
        self.assertEqual(s['static_pa'],2.)
        self.assertEqual(s['net_kg_s'],1.)
        self.assertEqual(s['reverse_kg_s'],1.)

    def test_segment_deltas_require_same_physical_stations(self):
        m=self.module()
        a={'pressure_stations':[{'id':'a','y_m':1.},{'id':'b','y_m':0.}],
           'segments':[{'id':'a-b','drop_pa':1.}], 'pressure_definition':'same'}
        b={**a,'pressure_stations':[{'id':'a','y_m':1.},{'id':'b','y_m':-.01}]}
        self.assertFalse(m.compare_pressure_diagnostics(a,b)['same_physical_stations'])
        self.assertIsNone(m.compare_pressure_diagnostics(a,b)['segments'][0]['delta_pa'])
        b={**a,'segments':[{'id':'a-b','drop_pa':1.5}]}
        self.assertAlmostEqual(m.compare_pressure_diagnostics(a,b)['segments'][0]['delta_pa'],.5)

    def test_pressure_origin_distinguishes_supply_from_suction(self):
        mod=self.module()
        supply=[{'x_center_m':i,'net_kg_s':q,'inlet_total_pa':10+q,'outlet_total_pa':10.,'drive_pa':q}
                for i,q in enumerate((1.,2.,1.))]
        self.assertEqual(mod.driving_summary(supply)['origin'],'supply_side')
        suction=[{**s,'inlet_total_pa':10.,'outlet_total_pa':10.-s['drive_pa']} for s in supply]
        self.assertEqual(mod.driving_summary(suction)['origin'],'downstream_side')

    def test_plan_does_not_follow_shifted_bottom_plate(self):
        m=self.module()
        geo={'layers':[{'id':1,'y_min_m':-.6,'y_max_m':-.5,'y_cut_m':-.55},
                       {'id':2,'y_min_m':-.4,'y_max_m':-.3,'y_cut_m':-.35}]}
        p={'stations':[{'y_m':-.27},{'y_m':-.28}]}
        b={'diagnostics':{'selected_plate':{'floor_y_m':-.65,'lower_y_m':-.63,'upper_y_m':-.628}}}
        a=m.pressure_plan(geo,p,b)
        b['diagnostics']['selected_plate'].update(lower_y_m=-.625,upper_y_m=-.623)
        self.assertEqual(a,m.pressure_plan(geo,p,b))
        self.assertTrue(all(a[i]['y_m']>a[i+1]['y_m'] for i in range(len(a)-1)))

if __name__=='__main__':unittest.main()
