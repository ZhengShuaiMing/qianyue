"""城市名到各平台城市码的对照表。数据在 city_codes.json。"""

import json
from pathlib import Path

_TABLE = json.loads((Path(__file__).parent / "city_codes.json").read_text(encoding="utf-8"))


def cities_of(platform: str) -> dict[str, str]:
    return _TABLE[platform]
