"""Offline measurement, lightweight role selection and one runtime geometry load."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.scenes.loading import assets
from lastmile_dataflow.scenes.loading.asset_library import AssetRequest, AssetSelector
from lastmile_dataflow.scenes.loading.asset_profiles import (
    AssetProfile, build_asset_index, load_asset_profiles,
)
from lastmile_dataflow.utils.io import write_rows


class AssetProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'thor'

    def tearDown(self):
        self.tmp.cleanup()

    def asset(self, category, identity, *, nested=False, articulated=False):
        group = 'RoboTHOR/Furniture' if nested else 'Objects'
        path = self.source / group / category / 'Prefabs' / identity / (identity + '.xml')
        path.parent.mkdir(parents=True)
        joint = '<joint name="hinge"/>' if articulated else '<freejoint/>'
        path.write_text(f'<mujoco><worldbody><body name="object">{joint}'
                        '<geom type="box" size=".1 .2 .3" mass="1"/>'
                        '</body></worldbody></mujoco>')
        return path

    def test_nested_assets_and_unsupported_records_are_measured_without_source_edits(self):
        self.asset('Chair', 'chair')
        self.asset('OfficeChair', 'office', nested=True)
        self.asset('Chair', 'chair', nested=True)  # Same source identity occurs once.
        self.asset('Vase', 'hinged_vase', articulated=True)
        before = {p: p.read_bytes() for p in self.source.rglob('*.xml')}
        result = build_asset_index(self.source, self.root / 'index')
        profiles = {p.asset_id: p for p in load_asset_profiles(result['index'])}
        self.assertEqual(set(profiles), {'chair', 'office', 'hinged_vase'})
        self.assertEqual(result['counts'], {'loadable': 2, 'unsupported': 1})
        self.assertFalse(profiles['hinged_vase'].loadable)
        self.assertIn('articulated_asset', profiles['hinged_vase'].reason)
        self.assertIn('floor_obstacle', profiles['office'].roles)
        p = profiles['office']
        np.testing.assert_allclose([p.width_m, p.depth_m, p.height_m], [.2, .6, .4])
        self.assertAlmostEqual(p.footprint_m2, .12)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaises(FileExistsError):
            build_asset_index(self.source, self.root / 'index')

    def test_selector_excludes_target_and_filters_support_rotation_and_size_without_compile(self):
        profile = AssetProfile('vase', '/source/vase.xml', 'Vase', width_m=.2, depth_m=.4,
            height_m=.5, footprint_m2=.08, movable=True, loadable=True, support_kind='surface',
            compatible_supports=('Desk',), roles=('visual_occluder',), xml_path='/cached/vase.xml', root_body='root')
        index = self.root / 'assets.jsonl'
        write_rows(index, [asdict(profile),
            asdict(replace(profile, asset_id='kettle', category='Kettle')),
            asdict(replace(profile, asset_id='unloadable', loadable=False)),
            asdict(replace(profile, asset_id='large', width_m=2.))])
        request = AssetRequest('visual_occluder', support='Desk', excluded_categories=('vase',),
                               available_size_m=(.45, .25), yaw_rad=np.pi / 2)
        with (patch.object(Path, 'rglob', side_effect=AssertionError('online asset scan')),
              patch.object(assets, 'measure_asset', side_effect=AssertionError('online compile'))):
            selector = AssetSelector(index)
            chosen = selector.select(request, np.random.default_rng(42))
            self.assertEqual([p.asset_id for p in chosen], ['kettle'])
            self.assertEqual(selector.select(replace(request, yaw_rad=0.), np.random.default_rng(42)), [])
            self.assertEqual(selector.select(replace(request, support='floor'), np.random.default_rng(42)), [])
            self.assertEqual(selector.select(replace(request, size_bounds={'height_m': (0., .2)}), np.random.default_rng(42)), [])

    def test_shortlist_prefers_categories_and_actual_use_with_seeded_ties(self):
        index = self.root / 'assets.jsonl'
        write_rows(index, [asdict(AssetProfile(str(i), '/source', category, width_m=.2, depth_m=.2,
            height_m=.3, footprint_m2=.04, movable=True, loadable=True, roles=('arm_obstacle',),
            xml_path='/cached', root_body='root'))
            for i, category in enumerate(['Vase'] * 5 + ['Kettle', 'Toaster'])])
        selector = AssetSelector(index)
        request = AssetRequest('arm_obstacle', limit=10)
        chosen = selector.select(request, np.random.default_rng(42))
        self.assertEqual(len(chosen), 5)
        self.assertEqual(len({p.category for p in chosen[:3]}), 3)
        self.assertEqual(chosen, AssetSelector(index).select(request, np.random.default_rng(42)))
        for p in chosen:
            if p.category == 'Kettle':
                selector.record_use(p)
        next_choice = selector.select(replace(request, limit=1), np.random.default_rng(42))[0]
        self.assertNotEqual(next_choice.category, 'Kettle')

    def test_runtime_geometry_and_catalog_share_one_compile_and_return_editable_spec_copies(self):
        self.asset('Vase', 'vase')
        result = build_asset_index(self.source, self.root / 'index')
        profile = load_asset_profiles(result['index'])[0]
        cache = assets.RuntimeGeometryCache([profile.geometry_entry()])
        try:
            with patch.object(assets, 'measure_asset', wraps=assets.measure_asset) as measure:
                first = cache.geometry('vase')
                spec, description = cache.load('vase')
                all_geometry = {key: cache.geometry(key) for key in sorted(cache.assets)}
                self.assertEqual(measure.call_count, 1)
                self.assertEqual(cache.compile_count, 1)
            self.assertIs(first['model'], all_geometry['vase']['model'])
            self.assertIs(first['data'], all_geometry['vase']['data'])
            self.assertAlmostEqual(description['size']['height'], profile.height_m)
            spec.body(profile.root_body).pos = [3, 0, 0]
            another, _ = cache.load('vase')
            np.testing.assert_allclose(another.body(profile.root_body).pos, [0, 0, 0])
        finally:
            cache.close()
        self.assertFalse(cache._cache)

    def test_eviction_releases_cache_ownership_while_active_geometry_remains_usable(self):
        self.asset('Vase', 'vase')
        self.asset('Kettle', 'kettle')
        result = build_asset_index(self.source, self.root / 'index')
        cache = assets.RuntimeGeometryCache([p.geometry_entry() for p in load_asset_profiles(result['index'])], capacity=1)
        try:
            active = cache.geometry('vase')
            cache.load('kettle')
            self.assertEqual(list(cache._cache), ['kettle'])
            self.assertGreater(active['model'].ngeom, 0)
            self.assertTrue(np.isfinite(active['local_points']).all())
        finally:
            cache.close()
