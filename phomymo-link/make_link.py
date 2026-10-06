#!/usr/bin/env python3
"""Usage: make_link.py DESIGN.json [Field=value ...]  ->  prints a Phomymo design link.

Each Field=value replaces {{Field}} in the design. Use \\n in a value for a line break.
"""
import base64
import json
import re
import sys
import zlib

BASE_URL = "https://phomymo.louietyj.me/"

text = open(sys.argv[1], encoding="utf-8").read()
for arg in sys.argv[2:]:
    field, value = arg.split("=", 1)
    text = text.replace("{{" + field + "}}", json.dumps(value.replace("\\n", "\n"))[1:-1])
for field in sorted(set(re.findall(r"\{\{(\w+)\}\}", text))):
    print(f"warning: {{{{{field}}}}} not filled", file=sys.stderr)

minified = json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False).encode()
deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
encoded = base64.urlsafe_b64encode(deflate.compress(minified) + deflate.flush()).rstrip(b"=").decode()
print(f"{BASE_URL}#design=v1.{encoded}")
