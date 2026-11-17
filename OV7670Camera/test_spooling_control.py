"""Linear packing pitch, layer growth and persisted spool measurements."""
from pathlib import Path
import tempfile
import json
import math
import unittest

from spooling_control import SpoolGeometry, load_geometry, save_geometry, TakeupIntegrator


class SpoolGeometryTests(unittest.TestCase):
    def test_one_filament_pitch_per_spool_revolution(self):
        geometry = SpoolGeometry()
        geometry.validate()
        rpm = 12
        degrees_per_second = geometry.angular_speed(rpm)
        degrees_per_revolution = degrees_per_second * 60 / rpm
        mm_per_degree = geometry.traverse / (geometry.maximum_angle - geometry.minimum_angle)
        self.assertAlmostEqual(degrees_per_revolution * mm_per_degree, 1.75)
        self.assertAlmostEqual(geometry.traverse, 53.25)
        self.assertEqual(geometry.angular_speed(0), 0)
        self.assertEqual(geometry.angular_speed(-rpm), degrees_per_second)
        self.assertAlmostEqual(geometry.angular_speed(2 * rpm), 2 * degrees_per_second)

    def test_each_end_adds_a_layer_and_radius_does_not_change_pitch_at_fixed_rpm(self):
        geometry = SpoolGeometry()
        self.assertAlmostEqual(geometry.radius(0), 43.5)
        self.assertAlmostEqual(geometry.radius(1), 45.25)
        self.assertAlmostEqual(geometry.radius(2), 47)
        self.assertGreater(geometry.takeup_speed(10, 1), geometry.takeup_speed(10, 0))
        self.assertLess(geometry.target_motor_rpm(10, 1), geometry.target_motor_rpm(10, 0))
        other_radius = SpoolGeometry(core_diameter=120)
        self.assertEqual(other_radius.angular_speed(10), geometry.angular_speed(10))

    def test_direct_drive_and_migration_of_old_ratio_settings(self):
        geometry = SpoolGeometry(winding_width=71.75, minimum_angle=-20, maximum_angle=40)
        self.assertAlmostEqual(geometry.angular_speed(60), 1.75 * 60 / 70)
        self.assertTrue(geometry.command().startswith("WINDCFG 87 71.75 1.75 -20 40 1"))
        with self.assertRaises(ValueError):
            SpoolGeometry(motor_ratio=2).validate()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.json"
            save_geometry(geometry, path)
            data = json.loads(path.read_text())
            data["geometry"]["motor_ratio"] = 2
            path.write_text(json.dumps(data))
            self.assertEqual(load_geometry(path).motor_ratio, 1)

    def test_numerical_length_integration_zero_stop_reverse_gaps_and_reset(self):
        integrator = TakeupIntegrator()
        self.assertEqual(integrator.extruded_length_mm, 635)
        speed = 2 * math.pi * 43.5 * 10 / 60
        self.assertEqual(integrator.observe(10, 10, 43.5), (speed, 0))
        self.assertAlmostEqual(integrator.observe(11, 10, 43.5)[1], speed)
        self.assertAlmostEqual(integrator.observe(12, 0, 43.5)[1], 1.5 * speed)
        self.assertAlmostEqual(integrator.observe(13, 0, 43.5)[1], 1.5 * speed)
        self.assertAlmostEqual(integrator.observe(20, -10, 43.5)[1], 1.5 * speed)
        integrator.break_interval()
        self.assertAlmostEqual(integrator.observe(21, -10, 43.5)[1], 1.5 * speed)
        self.assertAlmostEqual(integrator.observe(22, -10, 43.5)[1], 2.5 * speed)
        self.assertAlmostEqual(integrator.extruded_length_mm, 635 + 2.5 * speed)
        integrator.reset()
        self.assertEqual(integrator.extruded_length_mm, 635)
        self.assertEqual(integrator.observe(23, 10, 43.5)[1], 0)

    def test_geometry_roundtrip_and_bad_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "geometry.json"
            self.assertEqual(load_geometry(path), SpoolGeometry())
            geometry = SpoolGeometry(core_diameter=92, winding_width=63)
            save_geometry(geometry, path)
            self.assertEqual(load_geometry(path), geometry)
            self.assertFalse(path.with_suffix(".tmp").exists())
            path.write_text('{"version":77}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_geometry(path)
        for geometry in (SpoolGeometry(winding_width=1), SpoolGeometry(core_diameter=0),
                         SpoolGeometry(minimum_angle=32, maximum_angle=31),
                         SpoolGeometry(motor_ratio=0), SpoolGeometry(core_diameter=float("nan"))):
            with self.assertRaises(ValueError):
                geometry.validate()


if __name__ == "__main__":
    unittest.main()
