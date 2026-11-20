#!/usr/bin/env python3
"""Print a go2rtc roborock:// source built from the core Roborock integration's stored login.

Run on the Home Assistant host, in a terminal: python3 source_from_ha.py "Vacuum name" [/config]
It asks for the pattern digits. The output holds credentials: paste it into your private go2rtc config only.
"""
import getpass
import json
import os
import re
import sys
import urllib.parse
import warnings


def build(rriot, dev, pin):
    host = rriot["r"]["m"].split("://", 1)[1]
    q = urllib.parse.urlencode(
        {"u": rriot["u"], "s": rriot["s"], "k": rriot["k"], "did": dev["duid"], "key": dev["localKey"], "pin": pin}
    )
    return "roborock://%s?%s" % (host, q)


def matches(store, name):
    """Every (rriot, device) named `name` across all Roborock entries, own and shared devices."""
    entries = json.load(open(os.path.join(store, "core.config_entries")))["data"]["entries"]
    found, names, missing = [], [], []
    for e in entries:
        if e["domain"] != "roborock":
            continue
        try:
            home = json.load(open(os.path.join(store, "roborock", e["entry_id"])))["data"]["homeData"]
        except (OSError, KeyError):
            missing.append(e["title"])
            continue
        for d in home.get("devices", []) + home.get("receivedDevices", []):
            names.append(d["name"])
            if d["name"] == name:
                found.append((e["data"]["user_data"]["rriot"], d))
    return found, names, missing


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    name, config = sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "/config"
    found, names, missing = matches(os.path.join(config, ".storage"), name)
    if len(found) != 1:
        hint = " Reload the Roborock integration and try again (no device cache yet for: %s)." % ", ".join(missing) if missing else ""
        sys.exit("%d vacuums named %r; found: %s.%s" % (len(found), name, ", ".join(names) or "none", hint))
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("run this in a terminal")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        pin = getpass.getpass("Pattern digits: ").strip()
    if not re.fullmatch(r"[1-9]+", pin):
        sys.exit("the pattern must be grid digits 1-9")
    print(build(*found[0], pin))


if __name__ == "__main__":
    main()
