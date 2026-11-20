import importlib.util
import json
import pathlib
import tempfile
import unittest
import urllib.parse

spec = importlib.util.spec_from_file_location("src", pathlib.Path(__file__).parents[1] / "tools/source_from_ha.py")
src = importlib.util.module_from_spec(spec)
spec.loader.exec_module(src)


def rriot(u):
    return {"u": u, "s": "S", "k": "K", "r": {"m": "ssl://mqtt-eu-1.example:8883"}}


class Source(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = pathlib.Path(self.tmp.name)
        (self.store / "roborock").mkdir()
        self.entries = []

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, entry_id, user, home):
        self.entries.append({"domain": "roborock", "entry_id": entry_id, "title": entry_id, "data": {"user_data": {"rriot": rriot(user)}}})
        if home is not None:
            (self.store / "roborock" / entry_id).write_text(json.dumps({"data": {"homeData": home}}))
        (self.store / "core.config_entries").write_text(json.dumps({"data": {"entries": self.entries}}))

    def test_build(self):
        url = src.build(rriot("U"), {"duid": "D", "localKey": "L&"}, "1235789")
        self.assertTrue(url.startswith("roborock://mqtt-eu-1.example:8883?"))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.split("?", 1)[1]).items()}
        self.assertEqual(q, {"u": "U", "s": "S", "k": "K", "did": "D", "key": "L&", "pin": "1235789"})

    def test_second_account_and_shared_device(self):
        self.add("a", "UA", {"devices": [{"name": "Other", "duid": "x", "localKey": "y"}]})
        self.add("b", "UB", {"devices": [], "receivedDevices": [{"name": "Vac", "duid": "D", "localKey": "L"}]})
        found, _, _ = src.matches(str(self.store), "Vac")
        self.assertEqual([(r["u"], d["duid"]) for r, d in found], [("UB", "D")])

    def test_repeated_name_is_ambiguous(self):
        self.add("a", "UA", {"devices": [{"name": "Vac", "duid": "1", "localKey": "y"}]})
        self.add("b", "UB", {"devices": [{"name": "Vac", "duid": "2", "localKey": "y"}]})
        self.assertEqual(len(src.matches(str(self.store), "Vac")[0]), 2)

    def test_missing_cache(self):
        self.add("a", "UA", None)
        self.assertEqual(src.matches(str(self.store), "Vac"), ([], [], ["a"]))


if __name__ == "__main__":
    unittest.main()
