import zipfile, json, struct, re, sys

def read_varint(data, pos):
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7

def parse_message(data, pos=0, end=None):
    if end is None:
        end = len(data)
    fields = {}
    while pos < end:
        try:
            tag, pos = read_varint(data, pos)
            field_number = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 0:
                val, pos = read_varint(data, pos)
                fields.setdefault(field_number, []).append(val)
            elif wire_type == 2:
                length, pos = read_varint(data, pos)
                val = data[pos:pos+length]
                fields.setdefault(field_number, []).append(val)
                pos += length
            elif wire_type == 5:
                val = struct.unpack_from('<f', data, pos)[0]
                fields.setdefault(field_number, []).append(val)
                pos += 4
            else:
                break
        except Exception:
            break
    return fields

def decode_string(b):
    try:
        return b.decode('utf-8')
    except:
        return None

def parse_player_info(data):
    f = parse_message(data)
    nickname = decode_string(f.get(1, [b''])[0])
    team = f.get(3, [0])[0]
    clan_tag = decode_string(f.get(5, [b''])[0]) if 5 in f else None
    return {'nickname': nickname, 'team': team, 'clan_tag': clan_tag}

def parse_player(data):
    f = parse_message(data)
    account_id = f.get(1, [0])[0]
    info = parse_player_info(f[2][0]) if 2 in f else {}
    return {'account_id': account_id, **info}

def parse_player_results_info(data):
    f = parse_message(data)
    return {
        'account_id': f.get(101, [0])[0],
        'tank_id': f.get(103, [0])[0],
        'damage_dealt': f.get(8, [0])[0],
        'kills': f.get(18, [0])[0],
        'shots': f.get(4, [0])[0],
        'hits': f.get(5, [0])[0],
        'penetrations': f.get(7, [0])[0],
        'damage_blocked': f.get(117, [0])[0],
        'damage_assisted': f.get(9, [0])[0],
        'base_xp': f.get(3, [0])[0],
    }

def parse_player_results(data):
    f = parse_message(data)
    result_id = f.get(1, [0])[0]
    info = parse_player_results_info(f[2][0]) if 2 in f else {}
    return {'result_id': result_id, **info}

def parse_battle_results(dat):
    # battle_results.dat has a pickle header we need to skip
    # Find the protobuf data after the pickle header
    # The protobuf data starts after a specific marker
    
    # Try parsing from different offsets to find valid protobuf
    for start in range(0, min(100, len(dat))):
        try:
            f = parse_message(dat, start)
            if 201 in f and 301 in f:  # players and player_results
                return f, start
        except:
            continue
    
    # Try from start
    f = parse_message(dat)
    return f, 0

def parse_replay(filepath):
    with zipfile.ZipFile(filepath, 'r') as z:
        with z.open('meta.json') as mf:
            meta = json.load(mf)
        with z.open('battle_results.dat') as bf:
            dat = bf.read()
    
    ROOM_TYPES = {
        0: 'Any', 1: 'Regular', 2: 'Training Room', 4: 'Tournament',
        5: 'Quick Tournament', 7: 'Rating', 8: 'Mad Games',
        22: 'Realistic Battles', 23: 'Uprising', 24: 'Gravity Mode',
        25: 'Skirmish', 26: 'Burning Games'
    }

    room_type_id = meta.get('arenaBonusType', -1)
    
    print(f"=== Battle Info ===")
    print(f"Player: {meta['playerName']}")
    print(f"Tank: {meta['playerVehicleName']}")
    print(f"Map: {meta['mapName']}")
    print(f"Room Type: {ROOM_TYPES.get(room_type_id, 'Unknown')}")
    print(f"Duration: {meta['battleDuration']:.0f}s")
    print()

    fields, offset = parse_battle_results(dat)
    
    winner = fields.get(3, [None])[0]
    print(f"Winner Team: {winner}")
    print()

    players = {}
    for pb in fields.get(201, []):
        p = parse_player(pb)
        if p.get('account_id'):
            players[p['account_id']] = p

    print("=== Players ===")
    for p in players.values():
        clan = f"[{p['clan_tag']}] " if p.get('clan_tag') else ""
        print(f"Team {p['team']}: {clan}{p['nickname']} (ID: {p['account_id']})")
    print()

    print("=== Stats ===")
    for rb in fields.get(301, []):
        r = parse_player_results(rb)
        acct = r.get('account_id')
        p = players.get(acct, {})
        name = p.get('nickname', f"ID:{acct}")
        team = p.get('team', '?')
        print(f"Team {team} | {name}")
        print(f"  Damage: {r['damage_dealt']} | Kills: {r['kills']} | Shots: {r['shots']} | Hits: {r['hits']} | Pens: {r['penetrations']}")
        print(f"  Blocked: {r['damage_blocked']} | Assisted: {r['damage_assisted']} | XP: {r['base_xp']}")
        print()

    return meta, fields, players

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python battle_results_parser.py <replay.wotbreplay>")
    else:
        parse_replay(sys.argv[1])
