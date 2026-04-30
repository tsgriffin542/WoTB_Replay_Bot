# encoding: utf-8
import discord
from discord import app_commands
import wg_api
import zipfile
import json
import struct
import io
import asyncio
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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

VEHICLE_NAMES = {
    4481: "Kranvagn",    20097: "Felice",  4737: "Emil II",
    8033: "Type 5",       3937: "Type 61",
    11281: "Leopard 1",  28689: "RHM",       11537: "Leopard PTA",
    12849: "116 F3",        13105: "113",           12593: "WZ-111 5A",
    257: "Object 140",   513: "Object 430U",     769: "IS-7",
    1025: "T-62A",       1281: "Object 907",     4353: "Object 277",
    2049: "M48 Patton",  2305: "T110E5",         2561: "T110E4",
    4097: "AMX 50B",
    5633: "FV215b",      5889: "Super Conq.",
    21505: "Progetto 65",
    22017: "60TP",
}

def get_vehicle_name(descriptor):
    if descriptor in VEHICLE_NAMES:
        return VEHICLE_NAMES[descriptor]
    nations = {0:"USSR", 1:"GER", 2:"USA", 3:"CHN", 4:"FRA",
               5:"UK", 6:"JPN", 7:"CZE", 8:"SWE", 9:"POL", 10:"ITA"}
    nation_id = (descriptor >> 4) & 0xF
    tank_idx = descriptor >> 8
    return f"{nations.get(nation_id, 'UNK')}-{tank_idx}"

def _fetch_tankopedia_sync():
    import requests
    total_loaded = 0
    try:
        page = 1
        while True:
            url = "https://api.wotblitz.com/wotb/encyclopedia/vehicles/"
            params = {
                "application_id": wg_api.API_KEY,
                "fields": "tank_id,name",
                "page_no": page,
            }
            data = requests.get(url, params=params, timeout=10).json()
            if data.get("status") != "ok":
                print(f"Tankopedia error: {data}")
                break
            vehicles = data.get("data", {})
            if not vehicles:
                break
            for tank_id_str, info in vehicles.items():
                VEHICLE_NAMES[int(tank_id_str)] = info.get("name", f"Tank-{tank_id_str}")
                total_loaded += 1
            if len(vehicles) < 100:
                break
            page += 1
        print(f"Tankopedia loaded: {total_loaded} tanks")
    except Exception as e:
        print(f"Tankopedia fetch failed: {e} — using hardcoded fallback")

scrim_sessions = {}  # guild_id -> {active, data, processed_ids, lock}

def get_session(guild_id):
    if guild_id not in scrim_sessions:
        scrim_sessions[guild_id] = {
            "active": False,
            "data": {},
            "processed_ids": set(),
            "lock": asyncio.Lock(),
        }
    return scrim_sessions[guild_id]

def fresh_entry():
    return {
        "nickname": "", "clan_tag": None, "games": 0, "damage": 0,
        "kills": 0, "shots": 0, "hits": 0, "penetrations": 0,
        "blocked": 0, "damage_received": 0, "capture_points": 0,
        "assisted_damage": 0, "survived": 0, "tank_counts": defaultdict(int),
        "last_map": "Unknown", "replay_team": 0,
        "team1_wins": 0, "team2_wins": 0,
    }

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
        "clan_tag": decode_string(f.get(5, [b""])[0]) if 5 in f else None,
    }

def parse_player(data):
    f = parse_message(data)
    account_id = f.get(1, [0])[0]
    info = parse_player_info(f[2][0]) if 2 in f else {}
    return {"account_id": account_id, **info}

def parse_player_results_info(data):
    f = parse_message(data)
    return {
        "account_id":       f.get(101, [0])[0],
        "damage_dealt":     f.get(8,   [0])[0],
        "kills":            f.get(18,  [0])[0],
        "shots":            f.get(4,   [0])[0],
        "hits":             f.get(5,   [0])[0],
        "penetrations":     f.get(7,   [0])[0],
        "damage_blocked":   f.get(117, [0])[0],
        "damage_received":  f.get(11,  [0])[0],
        "capture_points":   f.get(14,  [0])[0],
        "damage_assisted":  f.get(15,  [0])[0],
        "survived":         0 if 25 in f else 1,
        "vehicle_desc":     f.get(103, [0])[0],
        "team":             f.get(102, [0])[0],
    }

def parse_player_results(data):
    f = parse_message(data)
    return parse_player_results_info(f[2][0]) if 2 in f else {}

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

def generate_scrim_image(results_list, total_games):
    BG        = "#0f0f1a"
    HEADER_BG = "#16213e"
    ROW_ODD   = "#0f0f1a"
    ROW_EVEN  = "#13131f"
    TEXT_MAIN = "#ffffff"
    TEXT_MUT  = "#aaaaaa"
    TEXT_HEAD = "#7ec8e3"
    GREEN     = "#4ade80"
    YELLOW    = "#facc15"
    RED       = "#f87171"
    PURPLE    = "#a78bfa"

    COLS = [
        ("Player",     2.4),
        ("Main",       1.5),
        ("Games",      0.7),
        ("Score",      0.7),
        ("Avg DMG",    1.0),
        ("DMG Share",  1.0),
        ("Kills",      0.8),
        ("DMG Ratio",  1.0),
        ("Pen%",       0.7),
        ("Survived",   0.9),
        ("Shots/G",    0.8),
        ("DMG/Shot",   0.8),
        ("Avg Blocked", 1.0),
    ]

    n_rows = len(results_list)
    fig_height = 2.2 + n_rows * 0.42
    fig_width = 20
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis("off")

    total_w = sum(c[1] for c in COLS)
    col_x = []
    x = 0
    for _, w in COLS:
        col_x.append(x / total_w)
        x += w

    header_h = 0.08
    ax.add_patch(plt.Rectangle((0, 1 - header_h), 1, header_h,
                                transform=ax.transAxes, color=HEADER_BG, zorder=1))
    ax.text(0.5, 1 - header_h * 0.35, "Scrim Stats",
            transform=ax.transAxes, color=TEXT_MAIN,
            fontsize=14, fontweight="bold", ha="center", va="center", zorder=2)
    ax.text(0.5, 1 - header_h * 0.78, f"{total_games} games",
            transform=ax.transAxes, color=TEXT_MUT,
            fontsize=9, ha="center", va="center", zorder=2)
    ax.text(0.01, 1 - header_h * 0.5,
            "Score:  70+ high   50-69 mid   <50 low",
            transform=ax.transAxes, color="#666677",
            fontsize=9, ha="left", va="center", zorder=2)
    ax.text(0.99, 1 - header_h * 0.5,
            "Generated by WoTB Replay Bot",
            transform=ax.transAxes, color="#666677",
            fontsize=9, ha="right", va="center", zorder=2)

    col_header_y = 1 - header_h - 0.055
    ax.add_patch(plt.Rectangle((0, col_header_y - 0.005), 1, 0.06,
                                transform=ax.transAxes, color=HEADER_BG, zorder=1))
    for i, (label, _) in enumerate(COLS):
        ha = "left" if i == 0 else "center"
        xpos = col_x[i] + (0.008 if i == 0 else 0)
        ax.text(xpos, col_header_y + 0.022, label,
                transform=ax.transAxes, color=TEXT_HEAD,
                fontsize=8, fontweight="bold", ha=ha, va="center", zorder=2)

    team1_total = sum(r["avg_dmg"] for r in results_list if r.get("replay_team") == 1)
    team2_total = sum(r["avg_dmg"] for r in results_list if r.get("replay_team") == 2)

    row_h = 0.048
    y = col_header_y - 0.01
    for idx, r in enumerate(results_list):
        row_color = ROW_ODD if idx % 2 == 0 else ROW_EVEN
        ax.add_patch(plt.Rectangle((0, y - row_h), 1, row_h,
                                    transform=ax.transAxes, color=row_color, zorder=1))

        team_total = team1_total if r.get("replay_team") == 1 else team2_total
        dmg_share = round(r["avg_dmg"] / team_total * 100, 1) if team_total > 0 else 0.0

        score = r["score"]
        score_color = GREEN if score >= 70 else (YELLOW if score >= 50 else RED)
        survived_color = GREEN if r["survived_pct"] >= 50 else RED

        values = [
            (r["name"],               TEXT_MAIN,     "left"),
            (r["main_tank"],          PURPLE,        "center"),
            (str(r["games"]),         TEXT_MUT,      "center"),
            (str(score),              score_color,   "center"),
            (f"{r['avg_dmg']:,}",     TEXT_MUT,      "center"),
            None,  # DMG Share drawn as bar
            (str(r["total_kills"]),   TEXT_MUT,      "center"),
            (str(r["dmg_ratio"]),     TEXT_MUT,      "center"),
            (f"{r['pen_pct']}%",      TEXT_MUT,      "center"),
            (f"{r['survived_pct']}%", survived_color,"center"),
            (str(r["shots_g"]),        TEXT_MUT,      "center"),
            (f"{r['dmg_per_shot']:,}", TEXT_MUT,      "center"),
            (f"{r['avg_blocked']:,}",  TEXT_MUT,      "center"),
        ]

        for i, val_tuple in enumerate(values):
            if val_tuple is None:
                continue
            val, color, ha = val_tuple
            xpos = col_x[i] + (0.006 if ha == "left" else 0)
            ax.text(xpos, y - row_h * 0.48, val,
                    transform=ax.transAxes, color=color,
                    fontsize=9, ha=ha, va="center", zorder=2)

        # DMG Share bar
        bar_color = GREEN if dmg_share >= 15 else YELLOW
        bar_max_w = 0.032
        bar_w = bar_max_w * min(dmg_share / 40.0, 1.0)
        bar_x = col_x[5] - 0.03
        bar_h = row_h * 0.45
        bar_y = y - row_h * 0.5 - bar_h * 0.5
        ax.add_patch(plt.Rectangle((bar_x, bar_y), bar_w, bar_h,
                                   transform=ax.transAxes, color=bar_color, zorder=2))
        ax.text(bar_x + bar_max_w + 0.004, y - row_h * 0.48, f"{dmg_share}%",
                transform=ax.transAxes, color=bar_color,
                fontsize=9, ha="left", va="center", zorder=3)

        y -= row_h

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
    await asyncio.to_thread(_fetch_tankopedia_sync)
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
    await interaction.response.defer()
    session = get_session(interaction.guild_id)

    if action.lower() == "start":
        session["active"] = True
        session["data"] = {}
        session["processed_ids"].clear()
        await interaction.followup.send("Scrim session started! Upload replays whenever you are ready.")

    elif action.lower() == "end":
        if not session["active"]:
            await interaction.followup.send("No scrim session is active. Use /scrim start first.")
            return
        if not session["data"]:
            await interaction.followup.send("No replays were uploaded during this session.")
            return

        session["active"] = False
        scrim_data = session["data"]

        results_list = []
        for account_id, p in scrim_data.items():
            games = p["games"]
            shots = p["shots"]
            if shots == 0:
                continue

            avg_dmg      = round(p["damage"] / games)
            avg_kills    = round(p["kills"] / games, 1)
            total_kills  = p["kills"]
            avg_blocked  = round(p["blocked"] / games)
            avg_assist   = round(p["assisted_damage"] / games)
            survived_pct = round(p["survived"] / games * 100)
            shots_g      = round(p["shots"] / games, 1)
            dmg_per_shot = round(p["damage"] / p["penetrations"]) if p["penetrations"] > 0 else 0
            pen_pct      = round(p["penetrations"] / shots * 100) if shots > 0 else 0
            dmg_ratio    = round(p["damage"] / p["damage_received"], 2) if p["damage_received"] > 0 else "inf"
            score        = calc_score(avg_dmg, pen_pct, avg_blocked, avg_kills,
                                      dmg_ratio if dmg_ratio != "inf" else 10)
            clan         = f"[{p['clan_tag']}]" if p.get("clan_tag") else ""
            main_tank    = max(p["tank_counts"], key=p["tank_counts"].get) if p["tank_counts"] else "?"

            results_list.append({
                "name":         f"{clan} {p['nickname']}".strip(),
                "clan_tag":     p.get("clan_tag") or "",
                "replay_team":  p.get("replay_team", 0),
                "games":        games,
                "avg_dmg":      avg_dmg,
                "avg_kills":    avg_kills,
                "total_kills":  total_kills,
                "pen_pct":      pen_pct,
                "dmg_ratio":    dmg_ratio,
                "score":        score,
                "avg_assist":   avg_assist,
                "avg_blocked":  avg_blocked,
                "survived_pct": survived_pct,
                "shots_g":      shots_g,
                "dmg_per_shot": dmg_per_shot,
                "main_tank":    main_tank,
            })

        results_list.sort(key=lambda x: x["score"], reverse=True)
        total_games = max(p["games"] for p in scrim_data.values())

        try:
            img_buf = generate_scrim_image(results_list, total_games)
            await interaction.followup.send(
                file=discord.File(fp=img_buf, filename="scrim_results.png")
            )
        except Exception as e:
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


async def handle_replay(data, message, guild_id):
    meta, players, results, winner = parse_replay_bytes(data)
    map_name = meta.get("mapName", "Unknown")
    session = get_session(guild_id)

    async with session["lock"]:
        scrim_data = session["data"]

        for pid, p in players.items():
            r = results.get(pid, {})
            if r.get("shots", 0) == 0:
                continue

            if pid not in scrim_data:
                scrim_data[pid] = fresh_entry()

            entry = scrim_data[pid]
            entry["nickname"]         = p.get("nickname", "")
            entry["clan_tag"]         = p.get("clan_tag")
            entry["games"]           += 1
            entry["damage"]          += r.get("damage_dealt", 0)
            entry["kills"]           += r.get("kills", 0)
            entry["shots"]           += r.get("shots", 0)
            entry["hits"]            += r.get("hits", 0)
            entry["penetrations"]    += r.get("penetrations", 0)
            entry["blocked"]         += r.get("damage_blocked", 0)
            entry["damage_received"] += r.get("damage_received", 0)
            entry["capture_points"]  += r.get("capture_points", 0)
            entry["assisted_damage"] += r.get("damage_assisted", 0)
            entry["survived"]        += r.get("survived", 0)
            entry["last_map"]         = map_name
            entry["replay_team"]      = r.get("team", p.get("team", 0))

            veh_desc = r.get("vehicle_desc", 0)
            if veh_desc:
                entry["tank_counts"][get_vehicle_name(veh_desc)] += 1

            if r.get("team") == winner:
                if r.get("team") == 1:
                    entry["team1_wins"] += 1
                else:
                    entry["team2_wins"] += 1

        game_count = max(e["games"] for e in scrim_data.values()) if scrim_data else 0

    await message.channel.send(f"Replay added. {game_count} game(s) recorded so far. Use /scrim end when done.")


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if not message.guild:
        return

    guild_id = message.guild.id
    session = get_session(guild_id)

    if message.id in session["processed_ids"]:
        return
    session["processed_ids"].add(message.id)

    for attachment in message.attachments:
        if attachment.filename.endswith(".wotbreplay"):
            if not session["active"]:
                await message.channel.send("No scrim session active. Use /scrim start first.")
                continue
            data = await attachment.read()
            try:
                await handle_replay(data, message, guild_id)
            except Exception as e:
                await message.channel.send(f"Failed to parse replay: {e}")

        elif attachment.filename.endswith(".zip"):
            if not session["active"]:
                await message.channel.send("No scrim session active. Use /scrim start first.")
                continue
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
                        await handle_replay(replay_data, message, guild_id)
                    except Exception as e:
                        await message.channel.send(f"Failed to parse {name}: {e}")
            except Exception as e:
                await message.channel.send(f"Failed to open zip: {e}")

client.run("MTQ5MTkyOTQ4NDA5NjcwMDQ4Ng.GekR-4.nZM6eb-bT7PxgFkx9JfTGeFILnud_0ih6S9qTQ")
