import importlib.util
import math
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "logic", pathlib.Path(__file__).parents[1] / "custom_components/roborock_camera/logic.py"
)
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


class Drive(unittest.TestCase):
    def test_session(self):
        d = logic.RemoteDrive()
        first = d.move(0.2, 90, 500)
        self.assertEqual([c for c, _ in first], ["app_rc_start", "app_rc_move"])
        self.assertEqual(first[1][1][0], {"omega": round(math.pi / 2, 1), "velocity": 0.2, "seqnum": 1, "duration": 500})
        second = d.move(0.1, 0, 500)
        self.assertEqual([c for c, _ in second], ["app_rc_move"])
        self.assertEqual(second[0][1][0]["seqnum"], 2)
        self.assertTrue(d.active)
        d.ended()
        self.assertFalse(d.active)
        self.assertEqual(d.move(0, 0, 500)[1][1][0]["seqnum"], 1)

    def test_limits(self):
        p = logic.RemoteDrive().move(5, -500, 99999)[1][1][0]
        self.assertEqual((p["velocity"], p["omega"], p["duration"]), (0.3, round(math.radians(-179), 1), 3000))


if __name__ == "__main__":
    unittest.main()
