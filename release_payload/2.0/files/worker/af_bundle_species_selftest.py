
import sys as _af_sys
_af_sys.dont_write_bytecode = True  # Keep the verified engine cache immutable.
import sys
import unittest
import importlib.util
from pathlib import Path
import numpy as np
import tempfile
sys.path.insert(0,str(Path(__file__).parent))

class SpeciesTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('af_species_exposure'), 'AF must provide species analysis')
        import af_species_exposure
        return af_species_exposure

    def test_order_and_binary_closure(self):
        m=self.module()
        self.assertEqual(m.species_names('(domains (((1 geom-domain mixture) (material . mix)))) (materials ((mix mixture (species (names (alme3 n2) () ())))))'), ['alme3','n2'])
        y=m.mass_fractions(np.array([[.2],[.7]]),2)
        np.testing.assert_allclose(y,[[.2,.8],[.7,.3]])
        with self.assertRaises(ValueError):m.mass_fractions(np.array([[.4,.8]]),2)
        with self.assertRaises(ValueError):m.species_names('(materials ((mix mixture (species (names (n2 alme3) () ())))))')

    def test_weighting_and_no_false_saturation(self):
        m=self.module()
        self.assertAlmostEqual(m.statistics(np.array([.1,.3]),np.array([3.,1.]))['mean'],.15)
        text=m.render_section({'status':'not_available','reason':'<unsafe>'})
        self.assertIn('&lt;unsafe&gt;',text)
        self.assertIn('无法判定',text)

    def test_exact_tetrahedron_volume(self):
        m=self.module()
        mesh={'nc':1,'nf':4,'xyz':np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]),
              'conn':np.array([[0,1,2],[0,1,3],[0,2,3],[1,2,3]]),'owner':np.ones(4,dtype=int),
              'inside':np.array([],dtype=int),'neighbors':np.array([],dtype=int)}
        self.assertAlmostEqual(m.cell_volumes(mesh,np.array([[0.,0.,0.],[.25,.25,.25]]))[0],1/6)

    def test_segmented_face_indices_include_first_and_last(self):
        import h5py
        from af_adaptive_native import _indexed_datasets
        m=self.module()
        with tempfile.TemporaryDirectory(prefix='af_face_test_') as directory:
            with h5py.File(Path(directory)/'faces.h5','w') as f:
                g=f.create_group('faces')
                for key,first,values in [('a',1,[.1,.2,.3]),('b',8,[.7,.8,.9])]:
                    values=np.asarray(values)
                    ds=g.create_dataset('SV_Y/'+key,data=np.column_stack((values,1-values)))
                    ds.attrs['minId']=[first];ds.attrs['maxId']=[first+2]
                actual=m.face_fractions({'fields':g,'nf':10},np.array([0,2,7,9]),2,_indexed_datasets)
                np.testing.assert_allclose(actual[:,0],[.1,.3,.7,.9])

    def test_worker_entry_disables_bytecode_cache(self):
        import subprocess
        spec=importlib.util.find_spec('af_live_capture')
        self.assertIsNotNone(spec)
        code='import runpy,sys; runpy.run_path('+repr(spec.origin)+'); assert sys.dont_write_bytecode'
        proc=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)

    def test_real_cff_pipeline_with_reordered_species(self):
        self.check_pipeline(['alme3','n2'],[.1,.9],False)

    def test_three_species_with_last_component_omitted(self):
        self.check_pipeline(['ar','h2','nh3'],[.2,.3,.5],True)

    def test_five_species_without_nitrogen_or_tma(self):
        self.check_pipeline(['he','o2','co2','ch4','h2o'],[.1,.2,.3,.15,.25],False)

    def test_snapshot_report_has_spatial_charts_and_explicit_units(self):
        import json
        m=self.module()
        path=Path(__file__).parents[1]/'结果/混合气体分析数据_500ms.json'
        if not path.exists():self.skipTest('Real snapshot presentation is checked separately from bundled synthetic tests')
        result=json.loads(path.read_text(encoding='utf-8'))['species_exposure']
        text=m.render_section(result)
        for marker in ['0.55 s 当前帧','同一时刻','species-channel-map','第5百分位','晶圆片间朝向表面','species-coverage']:
            self.assertTrue(marker in text,marker)

    def check_pipeline(self,names,values,omit_last):
        m=self.module()
        import h5py
        from af_bundle_selftest import build_synthetic_ald_pair
        from af_adaptive_native import analyze_saved_pair
        with tempfile.TemporaryDirectory(prefix='af_species_test_') as folder:
            case,data=build_synthetic_ald_pair(Path(folder),nplates=8)
            with h5py.File(case,'a') as f:
                nc=int(f['meshes/1/cells/zoneTopology/maxId'][0])
                nf=len(f['meshes/1/faces/c0/1'])
                text='(domains (((1 geom-domain mixture) (material . mix)))) (materials ((mix mixture (species (names ('+' '.join(names)+') () ()))))) (rp-react? #f)'
                f.create_dataset('settings/Rampant Variables',data=[text.encode('utf-8')])
            with h5py.File(data,'a') as f:
                f.create_dataset('settings/Data Variables',data=[b'(flow-time 0.5)'])
                for scope,n in [('cells',nc),('faces',nf)]:
                    ds=f.create_dataset('results/1/phase-1/'+scope+'/SV_Y/1',data=np.tile(values[:-1] if omit_last else values,(n,1)))
                    ds.attrs['minId']=np.array([1]);ds.attrs['maxId']=np.array([n])
            result=analyze_saved_pair(case,data)
            r=result['species_exposure']
            self.assertEqual(r['status'],'computed',r)
            self.assertEqual(r['flow_time_s'],.5)
            self.assertEqual(len(r['regions']),4)
            self.assertEqual(r['species'],names)
            for row in r['regions']:
                for name,value in zip(names,values):
                    self.assertAlmostEqual(row['species'][name]['bulk']['mean'],value)
                    self.assertAlmostEqual(r['inlet_mass_fractions'][name],value)
                    self.assertAlmostEqual(row['species'][name]['surface']['mean'],value)
                    self.assertGreater(row['species'][name]['surface']['area_m2'],0)
            self.assertIsNone(r['dose_pa_s'])
            report=m.render_section(r)
            for name in names:self.assertIn('>'+name+'<',report)

if __name__=='__main__':unittest.main()
