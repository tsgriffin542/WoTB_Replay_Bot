# encoding: utf-8
import discord
from discord import app_commands
import wg_api
import zipfile
import json
import struct
import io
from collections import defaultdict

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

ROOM_TYPES = {
    0: "Any", 1: "Regular", 2: "Training Room", 4: "Tournament",
    5: "Quick Tournament", 7: "Rating", 8: "Mad Games",
    22: "Realistic Battles", 23: "Uprising", 24: "Gravity Mode",
    25: "Skirmish", 26: "Burning Games"
}

scrim_active = False
scrim_data = defaultdict(lambda: {
    "nickname": "",
    "clan_tag": None,
    "games": 0,
    "damage": 0,
    "kills": 0,
    "shots": 0,
    "hits": 0,
    "penetrations": 0,
    "blocked": 0,
    "damage_received": 0,
    "capture_points": 0,
})

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
                val = struct.unpack_from("<f", data, pos)[0]
                fields.setdefault(field_number, []).append(val)
                pos += 4
            else:
                break
        except Exception:
            break
    return fields

def decode_string(b):
    try:
        return b.decode("utf-8")
    except:
        return None

def parse_player_info(data):
    f = parse_message(data)
    return {
        "nickname": decode_string(f.get(1, [b""])[0]),
        "team": f.get(3, [0])[0],
        "clan_tag": decode_string(f.get(5, [b""])[0]) if 5 in f else None
    }

def parse_player(data):
    f = parse_message(data)
    account_id = f.get(1, [0])[0]
    info = parse_player_info(f[2][0]) if 2 in f else {}
    return {"account_id": account_id, **info}

def parse_player_results_info(data):
    f = parse_message(data)
    return {
        "account_id": f.get(101, [0])[0],
        "damage_dealt": f.get(8, [0])[0],
        "kills": f.get(18, [0])[0],
        "shots": f.get(4, [0])[0],
        "hits": f.get(5, [0])[0],
        "penetrations": f.get(7, [0])[0],
        "damage_blocked": f.get(117, [0])[0],
        "damage_received": f.get(11, [0])[0],
        "capture_points": f.get(14, [0])[0],
    }

def parse_player_results(data):
    f = parse_message(data)
    info = parse_player_results_info(f[2][0]) if 2 in f else {}
    return info

def parse_replay_bytes(data):
    with zipfile.ZipFile(io.BytesIO(data), "r") as z:
        with z.open("meta.json") as mf:
            meta = json.load(mf)
        with z.open("battle_results.dat") as bf:
            dat = bf.read()

    fields = {}
    for start in range(0, 200):
        try:
            f = parse_message(dat, start)
            if 201 in f and 301 in f:
                fields = f
                break
        except:
            continue

    players = {}
    for pb in fields.get(201, []):
        p = parse_player(pb)
        if p.get("account_id"):
            players[p["account_id"]] = p

    results = {}
    for rb in fields.get(301, []):
        r = parse_player_results(rb)
        if r.get("account_id"):
            results[r["account_id"]] = r

    winner = fields.get(3, [0])[0]
    return meta, players, results, winner

def calc_score(avg_dmg, pen_pct, avg_blocked, avg_kills, dmg_ratio):
    raw = (
        (avg_dmg * 1.0) +
        (pen_pct * 20) +
        (avg_blocked * 0.1) +
        (avg_kills * 150) +
        (dmg_ratio * 100)
    )
    score = round(raw / 67)
    return min(score, 100)

def format_player_line(p, r):
    clan = f"[{p['clan_tag']}] " if p.get("clan_tag") else ""
    dmg = r.get("damage_dealt", 0)
    kills = r.get("kills", 0)
    shots = r.get("shots", 0)
    hits = r.get("hits", 0)
    pens = r.get("penetrations", 0)
    blocked = r.get("damage_blocked", 0)
    dmg_recv = r.get("damage_received", 0)
    cap_pts = r.get("capture_points", 0)
    hit_ratio = f"{round(hits/shots*100)}%" if shots > 0 else "N/A"
    pen_ratio = f"{round(pens/shots*100)}%" if shots > 0 else "N/A"
    dmg_ratio = round(dmg / dmg_recv, 2) if dmg_recv > 0 else "inf"
    line = f"{clan}{p['nickname']}\n"
    line += f"  DMG: {dmg} | Kills: {kills} | Blocked: {blocked} | DMG Ratio: {dmg_ratio} | Cap Pts: {cap_pts}\n"
    line += f"  Shots: {shots} | Hit%: {hit_ratio} | Pen%: {pen_ratio}"
    return line

def send_in_chunks(text, max_length=1900):
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_length:
            chunks.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks

@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")
    print("Slash commands synced!")
    for guild in client.guilds:
        print(f"Connected to server: {guild.name} (ID: {guild.id})")

@tree.command(name="player", description="Search for a WoT Blitz player by name")
async def player(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    data = wg_api.get_player(name)
    if data["meta"]["count"] == 0:
        await interaction.followup.send("No players found.")
        return
    players = data["data"]
    result = f"Found {data['meta']['count']} player(s):\n"
    for p in players:
        result += f"- {p['nickname']} ID: {p['account_id']}\n"
    await interaction.followup.send(result)

@tree.command(name="stats", description="Get win rate for a WoT Blitz player")
async def stats(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    search = wg_api.get_player(name)
    if search["meta"]["count"] == 0:
        await interaction.followup.send("Player not found.")
        return
    player = search["data"][0]
    account_id = player["account_id"]
    nickname = player["nickname"]
    s = wg_api.get_player_stats(account_id)
    result = (
        f"{nickname}\n"
        f"Battles: {s['battles']}\n"
        f"Win Rate: {s['winrate']}%"
    )
    await interaction.followup.send(result)

@tree.command(name="scrim", description="Start or end a scrim session")
async def scrim(interaction: discord.Interaction, action: str):
    global scrim_active, scrim_data
    await interaction.response.defer()

    if action.lower() == "start":
        scrim_active = True
        scrim_data = defaultdict(lambda: {
            "nickname": "",
            "clan_tag": None,
            "games": 0,
            "damage": 0,
            "kills": 0,
            "shots": 0,
            "hits": 0,
            "penetrations": 0,
            "blocked": 0,
            "damage_received": 0,
            "capture_points": 0,
        })
        await interaction.followup.send("Scrim session started! Upload replays whenever you are ready.")

    elif action.lower() == "end":
        if not scrim_active:
            await interaction.followup.send("No scrim session is active. Use /scrim start first.")
            return
        if not scrim_data:
            await interaction.followup.send("No replays were uploaded during this session.")
            return

        scrim_active = False

        results_list = []
        for account_id, p in scrim_data.items():
            games = p["games"]
            shots = p["shots"]
            if shots == 0:
                continue
            avg_dmg = round(p["damage"] / games)
            avg_kills = round(p["kills"] / games, 1)
            avg_blocked = round(p["blocked"] / games)
            avg_cap_pts = round(p["capture_points"] / games)
            pen_pct = round(p["penetrations"] / shots * 100) if shots > 0 else 0
            hit_pct = round(p["hits"] / shots * 100) if shots > 0 else 0
            dmg_ratio = round(p["damage"] / p["damage_received"], 2) if p["damage_received"] > 0 else "inf"
            score = calc_score(avg_dmg, pen_pct, avg_blocked, avg_kills, dmg_ratio if dmg_ratio != "inf" else 10)
            clan = f"[{p['clan_tag']}] " if p.get("clan_tag") else ""
            results_list.append({
                "name": f"{clan}{p['nickname']}",
                "games": games,
                "avg_dmg": avg_dmg,
                "avg_kills": avg_kills,
                "avg_blocked": avg_blocked,
                "pen_pct": pen_pct,
                "hit_pct": hit_pct,
                "dmg_ratio": dmg_ratio,
                "score": score,
                "avg_cap_pts": avg_cap_pts,
            })

        results_list.sort(key=lambda x: x["score"], reverse=True)
        total_games = max(p["games"] for p in scrim_data.values())
        lines = [f"Scrim Results - {total_games} games", ""]

        for i, p in enumerate(results_list, 1):
            lines.append(f"{i}. {p['name']} ({p['games']} games) | Score: {p['score']}/100")
            lines.append(f"   Avg DMG: {p['avg_dmg']} | Kills: {p['avg_kills']} | Blocked: {p['avg_blocked']} | DMG Ratio: {p['dmg_ratio']} | Pen%: {p['pen_pct']}% | Hit%: {p['hit_pct']}% | Avg Cap Pts: {p['avg_cap_pts']}")
            lines.append("")

        chunks = send_in_chunks("\n".join(lines))
        first = True
        for chunk in chunks:
            if first:
                await interaction.followup.send(chunk)
                first = False
            else:
                await interaction.channel.send(chunk)

    else:
        await interaction.followup.send("Invalid action. Use /scrim start or /scrim end.")

async def handle_replay(data, message):
    meta, players, results, winner = parse_replay_bytes(data)
    room_type_id = meta.get("arenaBonusType", -1)
    room_type = ROOM_TYPES.get(room_type_id, "Unknown")

    if scrim_active:
        for pid, p in players.items():
            r = results.get(pid, {})
            if r.get("shots", 0) == 0:
                continue
            entry = scrim_data[pid]
            entry["nickname"] = p.get("nickname", "")
            entry["clan_tag"] = p.get("clan_tag")
            entry["games"] += 1
            entry["damage"] += r.get("damage_dealt", 0)
            entry["kills"] += r.get("kills", 0)
            entry["shots"] += r.get("shots", 0)
            entry["hits"] += r.get("hits", 0)
            entry["penetrations"] += r.get("penetrations", 0)
            entry["blocked"] += r.get("damage_blocked", 0)
            entry["damage_received"] += r.get("damage_received", 0)
            entry["capture_points"] += r.get("capture_points", 0)

        game_count = max(e["games"] for e in scrim_data.values()) if scrim_data else 0
        await message.channel.send(f"Replay added. {game_count} game(s) recorded so far. Use /scrim end when done.")

    else:
        lines = []
        lines.append(f"Map: {meta['mapName'].title()} | Mode: {room_type} | Duration: {int(meta['battleDuration'])}s")
        lines.append("")
        team1 = [(pid, p) for pid, p in players.items() if p["team"] == 1]
        team2 = [(pid, p) for pid, p in players.items() if p["team"] == 2]
        lines.append(f"Team 1{' WINNER' if winner == 1 else ''}")
        lines.append("```")
        for pid, p in team1:
            r = results.get(pid, {})
            if r.get("shots", 0) == 0:
                continue
            lines.append(format_player_line(p, r))
            lines.append("")
        lines.append("```")
        lines.append(f"Team 2{' WINNER' if winner == 2 else ''}")
        lines.append("```")
        for pid, p in team2:
            r = results.get(pid, {})
            if r.get("shots", 0) == 0:
                continue
            lines.append(format_player_line(p, r))
            lines.append("")
        lines.append("```")
        await message.channel.send("\n".join(lines))

@client.event
async def on_message(message):
    global scrim_data

    if message.author == client.user:
        return

    for attachment in message.attachments:
        if attachment.filename.endswith(".wotbreplay"):
            data = await attachment.read()
            try:
                await handle_replay(data, message)
            except Exception as e:
                await message.channel.send(f"Failed to parse replay: {e}")

        elif attachment.filename.endswith(".zip"):
            raw = await attachment.read()
            try:
                with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
                    replays = [n for n in z.namelist() if n.endswith(".wotbreplay")]
                if not replays:
                    await message.channel.send("No .wotbreplay files found in that zip.")
                    continue
                await message.channel.send(f"Found {len(replays)} replay(s) in zip, processing...")
                for name in replays:
                    with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
                        replay_data = z.read(name)
                    try:
                        await handle_replay(replay_data, message)
                    except Exception as e:
                        await message.channel.send(f"Failed to parse {name}: {e}")
            except Exception as e:
                await message.channel.send(f"Failed to open zip: {e}")

client.run("MTQ5MTkyOTQ4NDA5NjcwMDQ4Ng.GekR-4.nZM6eb-bT7PxgFkx9JfTGeFILnud_0ih6S9qTQ")
