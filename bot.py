# encoding: utf-8
import discord
from discord import app_commands
import wg_api
import zipfile
import json
import struct
import io
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

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
    "assisted_damage": 0,
    "survived": 0,
    "tank_counts": defaultdict(int),
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
        "damage_assisted": f.get(15, [0])[0],
        "survived": f.get(23, [0])[0],
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

def generate_scrim_image(results_list, total_games, map_name, team_scores):
    """Generate a dark-themed scrim stats image and return as bytes."""

    # --- Colors ---
    BG        = "#0f0f1a"
    HEADER_BG = "#16213e"
    ROW_ODD   = "#0f0f1a"
    ROW_EVEN  = "#13131f"
    DIVIDER   = "#1a1a2e"
    BORDER    = "#2a2a3e"
    TEXT_MAIN = "#ffffff"
    TEXT_MUT  = "#aaaaaa"
    TEXT_HEAD = "#7ec8e3"
    GREEN     = "#4ade80"
    YELLOW    = "#facc15"
    RED       = "#f87171"
    PURPLE    = "#a78bfa"

    # Column definitions: (header, key, width_ratio)
    COLS = [
        ("Player",      "name",       2.4),
        ("Main",        "main_tank",  1.4),
        ("Games",       "games",      0.7),
        ("Score",       "score",      0.7),
        ("Avg DMG",     "avg_dmg",    1.0),
        ("DMG Share",   "dmg_share",  1.0),
        ("Kills",       "avg_kills",  0.7),
        ("DMG Ratio",   "dmg_ratio",  1.0),
        ("Pen%",        "pen_pct",    0.7),
        ("Survived",    "survived",   0.9),
        ("Shots/G",     "shots_g",    0.8),
        ("Avg Assist",  "avg_assist", 1.0),
    ]

    n_rows = len(results_list) + len(set(r["team"] for r in results_list))  # players + dividers
    fig_height = 2.8 + n_rows * 0.38
    fig_width = 18
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis("off")

    total_w = sum(c[2] for c in COLS)
    col_x = []
    x = 0
    for _, _, w in COLS:
        col_x.append(x / total_w)
        x += w

    # --- Scoreboard header ---
    teams = list(team_scores.keys())
    score_text = ""
    winner_text = ""
    if len(teams) == 2:
        t1, t2 = teams[0], teams[1]
        s1, s2 = team_scores[t1], team_scores[t2]
        score_text = f"{t1}   {s1}  —  {s2}   {t2}"
        winner = t1 if s1 > s2 else (t2 if s2 > s1 else "Draw")
        winner_text = f"{winner} wins" if winner != "Draw" else "Draw"

    header_h = 0.13
    ax.add_patch(plt.Rectangle((0, 1 - header_h), 1, header_h,
                                transform=ax.transAxes, color=HEADER_BG, zorder=1))
    ax.text(0.5, 1 - header_h * 0.3, score_text,
            transform=ax.transAxes, color=TEXT_MAIN,
            fontsize=15, fontweight="bold", ha="center", va="center", zorder=2)
    meta_str = f"{map_name}   ·   {total_games} games   ·   {winner_text}"
    ax.text(0.5, 1 - header_h * 0.75, meta_str,
            transform=ax.transAxes, color=TEXT_MUT,
            fontsize=9, ha="center", va="center", zorder=2)

    # --- Column headers ---
    col_header_y = 1 - header_h - 0.06
    ax.add_patch(plt.Rectangle((0, col_header_y - 0.01), 1, 0.065,
                                transform=ax.transAxes, color=HEADER_BG, zorder=1))
    for i, (label, _, _) in enumerate(COLS):
        ha = "left" if i == 0 else "center"
        xpos = col_x[i] + (0.01 if i == 0 else 0)
        ax.text(xpos, col_header_y + 0.02, label,
                transform=ax.transAxes, color=TEXT_HEAD,
                fontsize=8, fontweight="bold", ha=ha, va="center", zorder=2)

    # --- Group players by team ---
    teams_order = []
    seen = set()
    for r in results_list:
        t = r["team"]
        if t not in seen:
            teams_order.append(t)
            seen.add(t)

    # Calculate DMG share per team
    team_totals = {}
    for t in teams_order:
        team_totals[t] = sum(r["avg_dmg"] for r in results_list if r["team"] == t)

    # --- Draw rows ---
    row_h = 0.042
    y = col_header_y - 0.015
    row_idx = 0

    for t in teams_order:
        # Divider row
        y -= row_h * 0.8
        ax.add_patch(plt.Rectangle((0, y - row_h * 0.1), 1, row_h * 0.9,
                                    transform=ax.transAxes, color=DIVIDER, zorder=1))
        team_total_dmg = team_totals[t]
        ax.text(0.005, y + row_h * 0.3, f"{t}   ·   Team total avg DMG: {team_total_dmg:,}",
                transform=ax.transAxes, color="#555566",
                fontsize=7.5, ha="left", va="center", zorder=2)
        y -= row_h * 0.6

        for r in results_list:
            if r["team"] != t:
                continue

            row_color = ROW_ODD if row_idx % 2 == 0 else ROW_EVEN
            ax.add_patch(plt.Rectangle((0, y - row_h), 1, row_h,
                                        transform=ax.transAxes, color=row_color, zorder=1))

            dmg_share = round(r["avg_dmg"] / team_totals[t] * 100, 1) if team_totals[t] > 0 else 0
            score = r["score"]
            score_color = GREEN if score >= 70 else (YELLOW if score >= 50 else RED)
            survived_color = GREEN if r["survived_pct"] >= 50 else RED

            values = [
                (r["name"],             TEXT_MAIN,  "left"),
                (r["main_tank"],        PURPLE,     "center"),
                (str(r["games"]),       TEXT_MUT,   "center"),
                (str(score),            score_color,"center"),
                (f"{r['avg_dmg']:,}",   TEXT_MUT,   "center"),
                (f"{dmg_share}%",       TEXT_MUT,   "center"),
                (str(r["avg_kills"]),   TEXT_MUT,   "center"),
                (str(r["dmg_ratio"]),   TEXT_MUT,   "center"),
                (f"{r['pen_pct']}%",    TEXT_MUT,   "center"),
                (f"{r['survived_pct']}%", survived_color, "center"),
                (str(r["shots_g"]),     TEXT_MUT,   "center"),
                (f"{r['avg_assist']:,}", TEXT_MUT,  "center"),
            ]

            for i, (val, color, ha) in enumerate(values):
                xpos = col_x[i] + (0.005 if ha == "left" else 0)
                ax.text(xpos, y - row_h * 0.45, val,
                        transform=ax.transAxes, color=color,
                        fontsize=8.5, ha=ha, va="center", zorder=2)

            y -= row_h
            row_idx += 1

    # --- Footer ---
    ax.add_patch(plt.Rectangle((0, 0), 1, 0.04,
                                transform=ax.transAxes, color=DIVIDER, zorder=1))
    ax.text(0.01, 0.02, "Score:  70+ high   50–69 mid   <50 low",
            transform=ax.transAxes, color="#555566", fontsize=7.5, ha="left", va="center")
    ax.text(0.99, 0.02, "Generated by WoTB Replay Bot",
            transform=ax.transAxes, color="#555566", fontsize=7.5, ha="right", va="center")

    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


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
            "assisted_damage": 0,
            "survived": 0,
            "tank_counts": defaultdict(int),
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

            avg_dmg     = round(p["damage"] / games)
            avg_kills   = round(p["kills"] / games, 1)
            avg_blocked = round(p["blocked"] / games)
            avg_cap_pts = round(p["capture_points"] / games)
            avg_assist  = round(p["assisted_damage"] / games)
            survived_pct= round(p["survived"] / games * 100)
            shots_g     = round(p["shots"] / games, 1)
            pen_pct     = round(p["penetrations"] / shots * 100) if shots > 0 else 0
            dmg_ratio   = round(p["damage"] / p["damage_received"], 2) if p["damage_received"] > 0 else "inf"
            score       = calc_score(avg_dmg, pen_pct, avg_blocked, avg_kills,
                                     dmg_ratio if dmg_ratio != "inf" else 10)
            clan        = f"[{p['clan_tag']}]" if p.get("clan_tag") else ""
            main_tank   = max(p["tank_counts"], key=p["tank_counts"].get) if p["tank_counts"] else "Unknown"

            results_list.append({
                "name":         f"{clan} {p['nickname']}".strip(),
                "team":         clan if clan else "No Clan",
                "clan_tag":     p.get("clan_tag") or "",
                "games":        games,
                "avg_dmg":      avg_dmg,
                "avg_kills":    avg_kills,
                "avg_blocked":  avg_blocked,
                "pen_pct":      pen_pct,
                "dmg_ratio":    dmg_ratio,
                "score":        score,
                "avg_cap_pts":  avg_cap_pts,
                "avg_assist":   avg_assist,
                "survived_pct": survived_pct,
                "shots_g":      shots_g,
                "main_tank":    main_tank,
            })

        results_list.sort(key=lambda x: x["score"], reverse=True)
        total_games = max(p["games"] for p in scrim_data.values())

        # Build team scores (wins per clan tag)
        team_scores = defaultdict(int)
        for p in scrim_data.values():
            clan = f"[{p['clan_tag']}]" if p.get("clan_tag") else "No Clan"
            team_scores[clan] += p.get("wins", 0)

        # Group by clan
        for r in results_list:
            clan = f"[{r['clan_tag']}]" if r["clan_tag"] else "No Clan"
            r["team"] = clan

        map_name = scrim_data[list(scrim_data.keys())[0]].get("last_map", "Unknown Map").title()

        try:
            img_buf = generate_scrim_image(results_list, total_games, map_name, team_scores)
            await interaction.followup.send(
                file=discord.File(fp=img_buf, filename="scrim_results.png")
            )
        except Exception as e:
            # Fallback to text if image fails
            lines = [f"Scrim Results - {total_games} games", ""]
            for i, p in enumerate(results_list, 1):
                lines.append(f"{i}. {p['name']} ({p['games']} games) | Score: {p['score']}/100")
                lines.append(f"   Avg DMG: {p['avg_dmg']} | Kills: {p['avg_kills']} | DMG Ratio: {p['dmg_ratio']} | Pen%: {p['pen_pct']}% | Survived: {p['survived_pct']}% | Shots/G: {p['shots_g']} | Avg Assist: {p['avg_assist']}")
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
    map_name = meta.get("mapName", "Unknown")
    tank_name = meta.get("playerVehicle", "Unknown")

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
            entry["assisted_damage"] += r.get("damage_assisted", 0)
            entry["survived"] += r.get("survived", 0)
            entry["last_map"] = map_name

            # Track tank usage per player
            if "tank_counts" not in entry or not isinstance(entry["tank_counts"], defaultdict):
                entry["tank_counts"] = defaultdict(int)

            # Use the uploader's tank from meta if this is the uploader
            uploader_name = meta.get("playerName", "")
            if p.get("nickname") == uploader_name:
                entry["tank_counts"][tank_name] += 1

        game_count = max(e["games"] for e in scrim_data.values()) if scrim_data else 0
        await message.channel.send(f"Replay added. {game_count} game(s) recorded so far. Use /scrim end when done.")

    else:
        lines = []
        lines.append(f"Map: {map_name.title()} | Mode: {room_type} | Duration: {int(meta['battleDuration'])}s")
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
