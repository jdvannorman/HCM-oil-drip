"""Regression checks for strip-relative arbitrary detection contours."""
import copy
import unittest
import cv2
import numpy as np
from unittest.mock import patch
from auto_roi import AreaTracker,regions_from_edges,source_to_strip,rectify
from drip_core import DEFAULT_CONFIG,LOGICAL_CPUS,RegionDetector,gray_blur,validate_config


class IrregularAreaTests(unittest.TestCase):
    def setUp(self):
        self.edges=dict(left=[-.10,.30],right=[.10,.72],quality=.9)

    def test_left_center_right_and_irregular_polygon_follow_width(self):
        regions=regions_from_edges(self.edges,copy.deepcopy(DEFAULT_CONFIG['regions']))
        self.assertEqual([r['name'] for r in regions],['Left','Center','Right'])
        templates=DEFAULT_CONFIG['regions']
        self.assertEqual(min(p[0] for p in templates[0]['strip_polygon']),0)
        self.assertAlmostEqual(max(p[0] for p in templates[0]['strip_polygon']),
                               min(p[0] for p in templates[1]['strip_polygon']))
        self.assertAlmostEqual(max(p[0] for p in templates[1]['strip_polygon']),
                               min(p[0] for p in templates[2]['strip_polygon']))
        self.assertEqual(max(p[0] for p in templates[2]['strip_polygon']),1)
        custom=dict(name='Zigzag',box=[.3,.7,.7,.98],strip_polygon=[
            [.15,.73],[.52,.70],[.82,.78],[.64,.88],[.88,.97],[.20,.95]])
        first=regions_from_edges(self.edges,[custom])[0]
        moved=dict(left=[-.10,.20],right=[.10,.82],quality=.9)
        second=regions_from_edges(moved,[custom])[0]
        self.assertEqual(len(first['polygon']),6)
        self.assertGreater(second['box'][2]-second['box'][0],first['box'][2]-first['box'][0])
        self.assertAlmostEqual(first['box'][1],second['box'][1],places=6)

    def test_drawn_image_points_round_trip_to_strip_coordinates(self):
        strip=[[.1,.72],[.7,.70],[.9,.94],[.2,.97]]
        source=regions_from_edges(self.edges,[dict(name='Custom',box=[0,0,1,1],strip_polygon=strip)])[0]
        x0,y0,x1,y1=source['box']
        points=[[x0+u*(x1-x0),y0+v*(y1-y0)] for u,v in source['polygon']]
        recovered=source_to_strip(points,self.edges)
        self.assertTrue(np.allclose(recovered,strip,atol=1e-6))

    def test_small_vertical_camera_shift_moves_the_contour(self):
        region=dict(name='Custom',box=[0,0,1,1],strip_polygon=[[.2,.72],[.8,.72],[.8,.9],[.2,.9]])
        first=regions_from_edges(self.edges,[region])[0]
        shifted=copy.deepcopy(self.edges)
        shifted['camera_shift']=[.01,.025]
        second=regions_from_edges(shifted,[region])[0]
        self.assertAlmostEqual(second['box'][1]-first['box'][1],.025,places=6)
        self.assertAlmostEqual(second['box'][3]-first['box'][3],.025,places=6)

    def test_arbitrary_mask_excludes_bounding_box_outside_polygon(self):
        background=np.full((224,224),100,np.float32)
        cfg=copy.deepcopy(DEFAULT_CONFIG)
        cfg['regions']=[dict(name='Test',box=[0,0,1,1])]
        validate_config(cfg)
        detector=RegionDetector('Test',(0,0,224,224),background.copy(),cfg)
        crop=np.full((224,224,3),100,np.uint8)
        cv2.circle(crop,(30,190),7,(230,230,230),-1)
        cv2.circle(crop,(150,55),7,(230,230,230),-1)
        triangle=[[.15,.1],[.9,.1],[.9,.7]]
        spots=detector.detect(crop,triangle)
        self.assertEqual(len(spots),1)
        self.assertGreater(spots[0]['center'][0],100)
        cached=detector.dynamic_valid
        detector.detect(crop,triangle)
        self.assertIs(detector.dynamic_valid,cached)
        detector.detect(crop,[[.1,.1],[.8,.1],[.8,.8],[.1,.8]])
        self.assertIsNot(detector.dynamic_valid,cached)

    def test_rectify_accepts_more_than_four_points(self):
        frame=np.zeros((300,500,3),np.uint8)
        region=dict(name='Six',box=[.2,.3,.7,.9],polygon=[
            [0,.1],[.5,0],[1,.2],[.9,.8],[.4,1],[0,.7]])
        crop,inverse=rectify(frame,region)
        self.assertEqual(crop.shape,(224,224,3))
        mapped=cv2.perspectiveTransform(np.array([[[0,0],[224,224]]],np.float32),inverse)[0]
        self.assertTrue(np.allclose(mapped,[[100,90],[350,270]],atol=1))

    def test_area_tracking_becomes_sparse_after_startup(self):
        config=dict(roi_startup_seconds=1,roi_startup_stride=5,
                    roi_camera_check_seconds=1,roi_stable_recheck_seconds=10)
        tracker=AreaTracker(30,config)
        tracker.estimate_camera_shift=lambda frame:[0,0]
        located=dict(left=[0,.25],right=[0,.75],quality=.9)
        frame=np.zeros((100,160,3),np.uint8)
        with patch('auto_roi.locate_strip',return_value=located):
            states=[tracker.update(frame,i) for i in range(120)]
        self.assertEqual(tracker.full_checks,5)
        self.assertEqual(tracker.camera_checks,4)
        self.assertEqual(sum(s['geometry_check']=='full' for s in states),5)
        self.assertTrue(all(s['status']=='tracking' for s in states))

    def test_cpu_thread_setting_is_within_this_computer(self):
        cfg=copy.deepcopy(DEFAULT_CONFIG)
        self.assertEqual(cfg['cpu_threads'],'auto')
        validate_config(cfg)
        cfg['cpu_threads']=max(2,min(7,LOGICAL_CPUS))
        validate_config(cfg)


if __name__=='__main__':unittest.main(verbosity=2)
