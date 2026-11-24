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

    def test_restart_gap(self):
        d = logic.RemoteDrive()
        d.move(0.2, 0, 500, now=10)
        d.ended(now=11)
        self.assertEqual(d.move(0.2, 0, 500, now=12), [], "too soon after the end: nothing is sent")
        self.assertFalse(d.active)
        self.assertEqual([c for c, _ in d.move(0.2, 0, 500, now=11 + logic.RESTART_GAP_S)], ["app_rc_start", "app_rc_move"])

    def test_limits(self):
        p = logic.RemoteDrive().move(5, -500, 99999)[1][1][0]
        self.assertEqual((p["velocity"], p["omega"], p["duration"]), (0.3, round(math.radians(-179), 1), 3000))


class ThenMop(unittest.TestCase):
    G = (102, 235, 303)

    def test_on_from_empty(self):
        t = logic.then_mop_table([1, 2], [], self.G, True)
        self.assertEqual(t, [
            {"segment": 1, "fan_power": 102, "water_box_mode": 235, "mop_mode": 303, "repeat": 1, "seq_type": 1},
            {"segment": 2, "fan_power": 102, "water_box_mode": 235, "mop_mode": 303, "repeat": 1, "seq_type": 1},
        ])
        self.assertTrue(logic.then_mop_on([1, 2], t))
        self.assertFalse(logic.then_mop_stale([1, 2], t, self.G))
        self.assertTrue(logic.then_mop_stale([1, 2], t, (104, 235, 303)))

    def test_off_restores_empty(self):
        on = logic.then_mop_table([1, 2], [], self.G, True)
        self.assertEqual(logic.then_mop_table([1, 2], on, self.G, False), [])
        self.assertFalse(logic.then_mop_on([1, 2], []))

    def test_keeps_other_rooms_and_unknown_keys(self):
        table = [{"segment": 9, "fan_power": 101, "seq_type": 0}, {"segment": 1, "fan_power": 104, "water_box_mode": 200,
                 "mop_mode": 300, "repeat": 2, "seq_type": 0, "mop_power": 7}]
        on = logic.then_mop_table([1], table, self.G, True)
        self.assertIn(table[0], on)
        room = next(e for e in on if e["segment"] == 1)
        self.assertEqual((room["repeat"], room["mop_power"], room["fan_power"], room["seq_type"]), (2, 7, 102, 1))
        self.assertEqual(logic.then_mop_table([1], on, (104, 235, 303), False), [table[0]],
                         "off drops every room the order set, also after the global settings changed")
        with self.assertRaises(ValueError):
            logic.then_mop_table([1], [{"segment": 1}, {"segment": 1}], self.G, True)

    def test_refuses_custom_or_unknown(self):
        for g in ((106, 204, 302), (102, None, 303)):
            with self.assertRaises(ValueError):
                logic.then_mop_table([1], [], g, True)
        self.assertFalse(logic.then_mop_on([], []))
        with self.assertRaises(ValueError):
            logic.then_mop_table([], [], self.G, True)
        with self.assertRaises(ValueError):
            logic.then_mop_table([], [{"segment": 1, "seq_type": 1}], self.G, False)
        self.assertEqual(logic.then_mop_table([1], [], (106, 204, 302), False), [], "off works in custom mode")


if __name__ == "__main__":
    unittest.main()
