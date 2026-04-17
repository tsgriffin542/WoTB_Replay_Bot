import zipfile
import json
import struct
from google.protobuf import descriptor_pb2
from google.protobuf.internal.decoder import _DecodeVarint

ROOM_TYPES = {
    0: "Any", 1: "Regular", 2: "Training Room", 4: "Tournament",
    5: "Quick Tournament", 7: "Rating", 8: "Mad Games",
    22: "Realistic Battles", 23: "Uprising", 24: "Gravity Mode",
    25: "Skirmish", 26: "Burning Games"
}

def read_varint(data, pos):
    result, new_pos = _DecodeVarint(data, pos)
    return result, new_pos

def parse_string(data, pos, length):
    return data[pos:pos+length].decode("utf-8", errors="ignore"), pos+length

def parse_player_results(data):
    results = {}
    pos = 0
    while pos < len(data):
        try:
            tag_varint, pos = read_varint(data, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 0:
                val, pos = read_varint(data, pos)
                results[field_number] = val
            elif wire_type == 2:
                length, pos = read_varint(data, pos)
                sub = data[pos:pos+length]
                results[field_number] = sub
                pos += length
            else:
                break
        except Exception:
            break
    return results

def parse_replay(filepath):
    with zipfile.ZipFile(filepath, "r") as z:
        # Read meta.json
        with z.open("meta.json") as f:
            meta = json.load(f)

        # Read battle_results.dat
        with z.open("battle_results.dat") as f:
            dat = f.read()

    return meta, dat

def extract_nicknames(dat):
    """Extract all player nicknames from the binary data."""
    import re
    names = re.findall(b"[\x20-\x7e]{3,}", dat)
    return [n.decode("ascii") for n in names if len(n) >= 3]

def parse_battle(filepath):
    meta, dat = parse_replay(filepath)

    room_type_id = meta.get("arenaBonusType", -1)
    room_type = ROOM_TYPES.get(room_type_id, f"Unknown ({room_type_id})")

    print(f"Player: {meta['playerName']}")
    print(f"Tank: {meta['playerVehicleName']}")
    print(f"Map: {meta['mapName']}")
    print(f"Room Type: {room_type}")
    print(f"Duration: {meta['battleDuration']:.0f}s")
    print(f"Is Training Room: {room_type_id == 2}")

    return {
        "player": meta["playerName"],
        "tank": meta["playerVehicleName"],
        "map": meta["mapName"],
        "room_type": room_type,
        "is_training_room": room_type_id == 2,
        "duration": meta["battleDuration"]
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python replay_parser.py <replay_file>")
    else:
        result = parse_battle(sys.argv[1])
        print(result)
