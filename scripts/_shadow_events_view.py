"""Print the authoritative vs shadow event streams side by side."""

from __future__ import annotations

import glob
import json

path = sorted(glob.glob("artifacts/graph/shadow/*.json"))[-1]
record = json.load(open(path, encoding="utf-8"))
sequence = record["diff"]["event_sequence"]
auth = sequence["authoritative"]
shadow = sequence["shadow"]

print("record:", path)
for index in range(max(len(auth), len(shadow))):
    left = auth[index] if index < len(auth) else "--"
    right = shadow[index] if index < len(shadow) else "--"
    marker = "   <== DIV" if left != right else ""
    print(f"{index:2d} {left:22s} | {right:22s}{marker}")
