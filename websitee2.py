import streamlit as st
import pandas as pd
import joblib
import requests
import urllib3

# Disable SSL warnings for localhost calls to LiveClientData
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Page config and title
st.set_page_config(layout="wide")
st.title("🏆 League of Legends Mid-Game Win Predictor")

# Load model and champion class CSV once at startup
model = joblib.load("linear_discriminant_analysis_model.pkl")
champion_classes_df = pd.read_csv("champion_classes.csv")
champion_classes_df['subclass'] = champion_classes_df['subclass'].fillna(champion_classes_df['class'])
champion_class_map = champion_classes_df.set_index("name")[["class", "subclass"]].to_dict(orient="index")
all_classes = champion_classes_df["class"].unique().tolist()
all_subclasses = champion_classes_df["subclass"].unique().tolist()

roles = ["Top", "Jungle", "Mid", "ADC", "Support"]
champion_list = champion_classes_df["name"].tolist()
dragon_types = [
    "None",
    "Infernal",
    "Mountain",
    "Ocean",
    "Cloud",
    "Hextech",
    "Chemtech",
    "Elder",
]

# === Function Definitions ===

def load_live_data():
    """Fetch live match data from the Riot Local LiveClientData API."""
    try:
        r = requests.get("https://127.0.0.1:2999/liveclientdata/allgamedata", timeout=1, verify=False)
        if r.status_code == 200:
            return r.json()
        else:
            return None
    except Exception:
        return None

def fill_inputs_from_live_data(live_data):
    global champion_list
    participants = live_data.get("allPlayers", [])
    active_player = live_data.get("activePlayer", None)

    # Add active player manually if not already in allPlayers
    if active_player and active_player.get("summonerName") not in [p.get("summonerName") for p in participants]:
        active_stats = active_player.get("championStats", {})
        active_scores = active_player.get("scores", {})
        active_data = {
            "summonerName": active_player.get("summonerName", "You"),
            "championName": active_player.get("championName", "Unknown"),
            "level": active_stats.get("level", 1),
            "position": "",  # Riot doesn't provide this here
            "team": "ORDER",  # Default guess (red side)
            "scores": active_scores
        }
        participants.append(active_data)

    roles = ["Top", "Jungle", "Mid", "ADC", "Support"]

    role_map_live_to_ui = {
        "TOP": "Top",
        "JUNGLE": "Jungle",
        "MIDDLE": "Mid",
        "MID": "Mid",
        "BOTTOM": "ADC",
        "ADC": "ADC",
        "SUPPORT": "Support",
        "UTILITY": "Support"
    }

    team_map = {100: "t1", 200: "t2"}

    def map_team(team_str):
        team_str = team_str.upper()
        return 100 if team_str == "ORDER" else 200 if team_str == "CHAOS" else None

    role_order_map = {role: i for i, role in enumerate(roles)}

    def sort_key(p):
        team_num = map_team(p.get("team", ""))
        raw_role = p.get("position", "").upper()
        ui_role = role_map_live_to_ui.get(raw_role, None)
        team_sort = team_num if team_num in team_map else 99
        role_sort = role_order_map.get(ui_role, 99) if ui_role else 99
        return (team_sort, role_sort)

    participants.sort(key=sort_key)

    role_assigned = {"t1": set(), "t2": set()}

    for p in participants:
        team_num = map_team(p.get("team", ""))
        if team_num not in team_map:
            continue
        team_id = team_map[team_num]

        raw_role = p.get("position", "").upper()
        ui_role = role_map_live_to_ui.get(raw_role)

        if ui_role not in roles or ui_role in role_assigned[team_id]:
            for r in roles:
                if r not in role_assigned[team_id]:
                    ui_role = r
                    break
            else:
                continue

        role_assigned[team_id].add(ui_role)
        prefix = f"{team_id}_{ui_role}"

        scores = p.get("scores", {})
        cs_val = scores.get("creepScore", 0)
        champ_name = p.get("championName", "Unknown")
        if champ_name not in champion_list:
            champ_name = champion_list[0]

        level = p.get("level", 1)

        st.session_state[f"k_{prefix}"] = scores.get("kills", 0)
        st.session_state[f"d_{prefix}"] = scores.get("deaths", 0)
        st.session_state[f"a_{prefix}"] = scores.get("assists", 0)
        st.session_state[f"cs_{prefix}"] = cs_val
        st.session_state[f"gold_{prefix}"] = 0
        st.session_state[f"champ_{prefix}"] = champ_name
        st.session_state[f"level_{prefix}"] = level

    events = live_data.get("events", {}).get("Events", [])
    participants = live_data.get("allPlayers", [])

    barons = {100: 0, 200: 0}
    towers = {100: 0, 200: 0}
    heralds = {100: False, 200: False}
    first_blood_team = None
    first_turret_team = None

    voidgrubs = {100: 0, 200: 0}

    epic_camps = ["Dragon", "RiftHerald", "Baron"]
    epic_camps_taken_count = {100: 0, 200: 0}
    first_three_epic_camps_winner = None

    first_three_kills_count = {100: 0, 200: 0}
    first_three_kills_winner = None

    dragon_types_list = ["Infernal", "Mountain", "Ocean", "Cloud", "Hextech", "Chemtech", "Elder"]
    dragon_counts = {100: {d: 0 for d in dragon_types_list}, 200: {d: 0 for d in dragon_types_list}}

    dragon_type_map = {
        "INFERNAL": "Infernal",
        "MOUNTAIN": "Mountain", "EARTH": "Mountain",
        "OCEAN": "Ocean", "WATER": "Ocean",
        "CLOUD": "Cloud", "AIR": "Cloud",
        "HEXTECH": "Hextech",
        "CHEMTECH": "Chemtech",
        "ELDER": "Elder"
    }

    def resolve_team_from_name(name):
        for p in participants:
            if p.get("summonerName") == name:
                return 100 if p.get("team", "").upper() == "ORDER" else 200
        return None

    def is_epic_camp(event_name):
        return any(ec.lower() in event_name.lower() for ec in epic_camps)

    camp_counter = {100: 0, 200: 0}
    kill_counter = {100: 0, 200: 0}

    first_blood_recorded = False

    for e in events:
        ev_name = e.get("EventName", "")
        killer_team = e.get("killerTeam")
        if not killer_team:
            killer_name = e.get("KillerName") or e.get("Acer")
            killer_team = resolve_team_from_name(killer_name)

        if killer_team not in (100, 200):
            continue

        if ev_name == "ChampionKill" and not first_blood_recorded:
            first_blood_team = killer_team
            first_blood_recorded = True

        if ev_name == "DragonKill":
            raw_type = e.get("DragonType", "") or e.get("monsterType", "")
            dt = dragon_type_map.get(raw_type.strip().upper(), None)
            if dt:
                dragon_counts[killer_team][dt] += 1

        elif ev_name in ("RiftHeraldKill", "HeraldKill"):
            heralds[killer_team] = True

        elif ev_name == "BaronKill":
            barons[killer_team] += 1

        elif ev_name == "TurretKilled":
            towers[killer_team] += 1
            if first_turret_team is None:
                first_turret_team = killer_team

        elif ev_name == "VoidGrubKill":
            voidgrubs[killer_team] += 1

        elif ev_name == "ChampionKill":
            if kill_counter[killer_team] < 3:
                kill_counter[killer_team] += 1
                if kill_counter[killer_team] == 3 and first_three_kills_winner is None:
                    first_three_kills_winner = killer_team

        if is_epic_camp(ev_name):
            if camp_counter[killer_team] < 3:
                camp_counter[killer_team] += 1
                if camp_counter[killer_team] == 3 and first_three_epic_camps_winner is None:
                    first_three_epic_camps_winner = killer_team

    st.session_state["b100"] = barons[100]
    st.session_state["b200"] = barons[200]
    st.session_state["t100"] = towers[100]
    st.session_state["t200"] = towers[200]
    st.session_state["herald_100"] = "Yes" if heralds[100] else "No"
    st.session_state["herald_200"] = "Yes" if heralds[200] else "No"
    st.session_state["fb100"] = "Yes" if first_blood_team == 100 else "No"
    st.session_state["fb200"] = "Yes" if first_blood_team == 200 else "No"
    st.session_state["first_turret_100"] = "Yes" if first_turret_team == 100 else "No"
    st.session_state["first_turret_200"] = "Yes" if first_turret_team == 200 else "No"
    st.session_state["voidgrubs_100"] = voidgrubs[100]
    st.session_state["voidgrubs_200"] = voidgrubs[200]
    st.session_state["first_three_epic_camps_100"] = "Yes" if first_three_epic_camps_winner == 100 else "No"
    st.session_state["first_three_epic_camps_200"] = "Yes" if first_three_epic_camps_winner == 200 else "No"
    st.session_state["first_three_kills_100"] = "Yes" if first_three_kills_winner == 100 else "No"
    st.session_state["first_three_kills_200"] = "Yes" if first_three_kills_winner == 200 else "No"

    def fill_dragons(team_key, team_num):
        expanded = []
        for dt in dragon_types_list:
            expanded.extend([dt] * dragon_counts[team_num][dt])
        for i in range(5):
            val = expanded[i] if i < len(expanded) else "None"
            st.session_state[f"drag{team_key}_{i}"] = val

    fill_dragons("100", 100)
    fill_dragons("200", 200)

    game_time_sec = live_data.get("gameData", {}).get("gameTime", 0)
    snapshot_min = int(game_time_sec // 60)
    st.session_state["snapshot_time_min"] = snapshot_min
    st.session_state["snapshot_time_sec_partial"] = game_time_sec % 60








def player_input_extended(team_id, role):
    st.markdown(f"<div class='player-header'>{role}</div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        kills = st.number_input("Kills", 0, 100, step=1, key=f"k_{team_id}_{role}")
        deaths = st.number_input("Deaths", 0, 100, step=1, key=f"d_{team_id}_{role}")
        assists = st.number_input("Assists", 0, 100, step=1, key=f"a_{team_id}_{role}")
    with col2:
        cs = st.number_input("CS (Minions Killed)", 0, 2000, step=1, key=f"cs_{team_id}_{role}")
        champ = st.selectbox("Champion", champion_list, key=f"champ_{team_id}_{role}")
    with col3:
        level = st.number_input("Level", 1, 18, step=1, key=f"level_{team_id}_{role}")

    return kills, deaths, assists, cs, champ, level

def get_class_subclass_features(champs):
    features = {f: 0 for f in (
        [f"class_100_{c}" for c in all_classes] +
        [f"class_200_{c}" for c in all_subclasses] +
        [f"subclass_100_{s}" for s in all_subclasses] +
        [f"subclass_200_{s}" for s in all_subclasses]
    )}
    for i, champ in enumerate(champs):
        prefix = "100" if i < 5 else "200"
        info = champion_class_map.get(champ)

        if info is None:
            cls = "None"
            subcls = "None"
        else:
            cls = info["class"]
            subcls = info["subclass"]

        cls_key = f"class_{prefix}_{cls}"
        subcls_key = f"subclass_{prefix}_{subcls}"

        if cls_key in features:
            features[cls_key] += 1
        if subcls_key in features:
            features[subcls_key] += 1
    return features

def dragon_selectboxes(team_prefix):
    dragons = []
    chosen_dragons = set()
    for i in range(5):
        options = ["None"] + [d for d in dragon_types if d != "None" and d not in chosen_dragons]
        selected = st.selectbox(f"Dragon {i+1} (Team {team_prefix})", options, key=f"drag{team_prefix}_{i}")
        dragons.append(selected)
        if selected != "None":
            chosen_dragons.add(selected)
    return dragons

# === Live Match Data Controls ===
col_load, col_clear = st.columns([1, 1])

with col_load:
    if st.button("🔄 Load Live Match Data"):
        live_data = load_live_data()
        if live_data is None:
            st.session_state["live_message"] = ("warning", "❌ Live client data not available or game not running.")
            st.session_state["live_loaded"] = False
        else:
            st.session_state["raw_live_data"] = live_data
            st.session_state["live_loaded"] = True
            st.session_state["live_message"] = ("success", "✅ Live client data has been loaded and applied.")
        st.rerun()

with col_clear:
    if st.button("❎ Clear Live Data"):
        st.session_state["live_loaded"] = False
        st.session_state["raw_live_data"] = None
        st.session_state["live_message"] = ("info", "🧹 Live client data has been cleared.")
        st.rerun()

# Prefill inputs if live data is loaded
if st.session_state.get("live_loaded", False):
    fill_inputs_from_live_data(st.session_state.get("raw_live_data"))

# Display message with appropriate style
if "live_message" in st.session_state:
    level, message = st.session_state["live_message"]
    if level == "success":
        st.success(message)
    elif level == "warning":
        st.warning(message)
    elif level == "error":
        st.error(message)
    elif level == "info":
        st.info(message)





# === UI Layout and Inputs ===

from base64 import b64encode

with open("League Of Legends Wallpaper.jpg", "rb") as f:
    data = b64encode(f.read()).decode()

st.markdown(f"""
<style>
/* Transparent background for the main app container */
.stApp {{
    background-color: transparent !important;
}}

/* Add wallpaper as a fixed layer behind everything */
.stApp::before {{
    content: "";
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background-image: url("data:image/jpeg;base64,{data}");
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    z-index: -1;
    pointer-events: none;
}}

/* Add filter on a pseudo-element above the wallpaper */
.stApp::after {{
    content: "";
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: rgba(0, 0, 0, 0.4); /* darken */
    backdrop-filter: brightness(40%) contrast(100%);
    z-index: -1;
    pointer-events: none;
}}
</style>
""", unsafe_allow_html=True)




with st.expander("Game Data", expanded=False):  # expanded=True means it's open by default, omit or set False to start collapsed
    
    kills, deaths, assists, cs, champions, levels = [], [], [], [], [], []

    col1, col2 = st.columns(2)

    with col1:
        with st.container():
            st.markdown("## 👤 Players")

            for role in roles:
                st.markdown(f'<div class="role-header">{role}</div>', unsafe_allow_html=True)
                cols_kda = st.columns(3)
                with cols_kda[0]:
                    k_val = st.number_input("Kills", 0, 100, step=1, key=f"k_t1_{role}")
                with cols_kda[1]:
                    d_val = st.number_input("Deaths", 0, 100, step=1, key=f"d_t1_{role}")
                with cols_kda[2]:
                    a_val = st.number_input("Assists", 0, 100, step=1, key=f"a_t1_{role}")

                cs_val = st.number_input("CS (Minions Killed)", 0, 2000, step=1, key=f"cs_t1_{role}")
                champ_val = st.selectbox("Champion", champion_list, key=f"champ_t1_{role}")
                level_val = st.number_input("Level", 1, 18, step=1, key=f"level_t1_{role}")

                kills.append(k_val)
                deaths.append(d_val)
                assists.append(a_val)
                cs.append(cs_val)
                champions.append(champ_val)
                levels.append(level_val)

            st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        with st.container():
            st.markdown("## ")

            for role in roles:
                st.markdown(f'<div class="role-header">{role}</div>', unsafe_allow_html=True)
                cols_kda = st.columns(3)
                with cols_kda[0]:
                    k_val = st.number_input("Kills", 0, 100, step=1, key=f"k_t2_{role}")
                with cols_kda[1]:
                    d_val = st.number_input("Deaths", 0, 100, step=1, key=f"d_t2_{role}")
                with cols_kda[2]:
                    a_val = st.number_input("Assists", 0, 100, step=1, key=f"a_t2_{role}")

                cs_val = st.number_input("CS (Minions Killed)", 0, 2000, step=1, key=f"cs_t2_{role}")
                champ_val = st.selectbox("Champion", champion_list, key=f"champ_t2_{role}")
                level_val = st.number_input("Level", 1, 18, step=1, key=f"level_t2_{role}")

                kills.append(k_val)
                deaths.append(d_val)
                assists.append(a_val)
                cs.append(cs_val)
                champions.append(champ_val)
                levels.append(level_val)

            st.markdown("</div>", unsafe_allow_html=True)

    # Objectives input
    st.markdown("## 🏹 Objectives")
    col_obj1, col_obj2 = st.columns(2)

    with col_obj1:
        st.markdown('<div class="objectives-col">', unsafe_allow_html=True)
        dragons_100 = dragon_selectboxes("100")
        barons_100 = st.slider("Barons (Team 100)", 0, 2, 0, step=1, key="b100")
        towers_100 = st.slider("Towers (Team 100)", 0, 11, 0, step=1, key="t100")
        herald_100 = st.radio("Rift Herald Taken (Team 100)", ["No", "Yes"], key="herald_100", horizontal=True)
        first_blood_100 = st.radio("First Blood Taken (Team 100)", ["No", "Yes"], key="fb100", horizontal=True)
        first_turret_100 = st.radio("First Turret Taken (Team 100)", ["No", "Yes"], key="first_turret_100", horizontal=True)
        voidgrubs_100 = st.slider("Void Grubs Killed (Team 100)", 0, 3, 0, step=1, key="voidgrubs_100")
        first_three_epic_camps_100 = st.radio("First Three Epic Camps Taken (Team 100)", ["No", "Yes"], key="first_three_epic_camps_100", horizontal=True)
        first_three_kills_100 = st.radio("First Three Kills Taken (Team 100)", ["No", "Yes"], key="first_three_kills_100", horizontal=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with col_obj2:
        st.markdown('<div class="objectives-col">', unsafe_allow_html=True)
        dragons_200 = dragon_selectboxes("200")
        barons_200 = st.slider("Barons (Team 200)", 0, 2, 0, step=1, key="b200")
        towers_200 = st.slider("Towers (Team 200)", 0, 11, 0, step=1, key="t200")
        herald_200 = st.radio("Rift Herald Taken (Team 200)", ["No", "Yes"], key="herald_200", horizontal=True)
        first_blood_200 = st.radio("First Blood Taken (Team 200)", ["No", "Yes"], key="fb200", horizontal=True)
        first_turret_200 = st.radio("First Turret Taken (Team 200)", ["No", "Yes"], key="first_turret_200", horizontal=True)
        voidgrubs_200 = st.slider("Void Grubs Killed (Team 200)", 0, 3, 0, step=1, key="voidgrubs_200")
        first_three_epic_camps_200 = st.radio("First Three Epic Camps Taken (Team 200)", ["No", "Yes"], key="first_three_epic_camps_200", horizontal=True)
        first_three_kills_200 = st.radio("First Three Kills Taken (Team 200)", ["No", "Yes"], key="first_three_kills_200", horizontal=True)

        st.markdown("</div>", unsafe_allow_html=True)

    # Rift Herald and First Blood exclusivity warnings
    if herald_100 == "Yes" and herald_200 == "Yes":
        st.warning("⚠️ Rift Herald can only be taken by one team!")
    if first_blood_100 == "Yes" and first_blood_200 == "Yes":
        st.warning("⚠️ First Blood can only be taken by one team!")

    # Metadata inputs
    st.markdown("## 🗂 Metadata")

    col_min, col_sec = st.columns([1, 1])
    with col_min:
        snapshot_min = st.number_input("Minutes", 0, 120, value=22, step=1, key="snapshot_time_min")
    with col_sec:
        snapshot_sec = st.number_input("Seconds", 0, 59, value=0, step=1, key="snapshot_time_sec_partial")

    # Calculate total seconds
    snapshot_time_sec = snapshot_min * 60 + snapshot_sec

    if snapshot_time_sec == 0:
        st.warning("⏱️ Please enter a snapshot time greater than 0 before predicting.")




st.markdown("## 🌍 Manual input")

platform_display = ["NA1 (default)", "EUW1", "KR"]
platform_values = ["NA1", "EUW1", "KR"]

rank_display = ["Iron", "Bronze", "Silver", "Gold (default)", "Platinum", "Diamond", "Master", "Challenger"]
rank_values = ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Challenger"]

rank_display_selected = st.selectbox("Rank", rank_display, index=3)
rank = rank_values[rank_display.index(rank_display_selected)]

platform_display_selected = st.selectbox("Platform ID", platform_display, index=0)
platform_id = platform_values[platform_display.index(platform_display_selected)]




# Prediction logic
if st.button("⚔️ Predict Match Outcome"):
    if snapshot_time_sec == 0:
        st.error("❌ Please enter a valid snapshot time (greater than 0) before predicting.")
    else:

        df = {}
        for i in range(10):
            team_id = "t1" if i < 5 else "t2"
            role = roles[i % 5]

            df[f"kills_p{i+1}"] = [st.session_state[f"k_{team_id}_{role}"]]
            df[f"deaths_p{i+1}"] = [st.session_state[f"d_{team_id}_{role}"]]
            df[f"assists_p{i+1}"] = [st.session_state[f"a_{team_id}_{role}"]]
            df[f"total_minions_killed_p{i+1}"] = [st.session_state[f"cs_{team_id}_{role}"]]
            df[f"level_p{i+1}"] = [st.session_state[f"level_{team_id}_{role}"]]  # <-- added level here

        df.update({
            "dragons_100": sum(1 for d in [st.session_state[f"drag100_{i}"] for i in range(5)] if d != "None"),
            "dragons_200": sum(1 for d in [st.session_state[f"drag200_{i}"] for i in range(5)] if d != "None"),
            "heralds_100": 1 if st.session_state["herald_100"] == "Yes" else 0,
            "heralds_200": 1 if st.session_state["herald_200"] == "Yes" else 0,
            "voidgrubs_100": st.session_state.get("voidgrubs_100", 0),
            "voidgrubs_200": st.session_state.get("voidgrubs_200", 0),
            "barons_100": st.session_state["b100"],
            "barons_200": st.session_state["b200"],
            "towers_100": st.session_state["t100"],
            "towers_200": st.session_state["t200"],
            "first_blood_100": 1 if st.session_state["fb100"] == "Yes" else 0,
            "first_blood_200": 1 if st.session_state["fb200"] == "Yes" else 0,
            "first_turret_100": 1 if st.session_state.get("first_turret_100", "No") == "Yes" else 0,
            "first_turret_200": 1 if st.session_state.get("first_turret_200", "No") == "Yes" else 0,
            "first_three_epic_camps_100": 1 if st.session_state.get("first_three_epic_camps_100", "No") == "Yes" else 0,
            "first_three_epic_camps_200": 1 if st.session_state.get("first_three_epic_camps_200", "No") == "Yes" else 0,
            "first_three_kills_100": 1 if st.session_state.get("first_three_kills_100", "No") == "Yes" else 0,
            "first_three_kills_200": 1 if st.session_state.get("first_three_kills_200", "No") == "Yes" else 0,
            "snapshot_time_sec": snapshot_time_sec,
            "platform_id": platform_id,
            "rank": rank,
        })

        df.update(get_class_subclass_features(champions))
        df = pd.DataFrame(df)

        feature_columns = joblib.load("feature_columns.pkl")

        df = pd.get_dummies(df, columns=["platform_id", "rank"])
        df = df.reindex(columns=feature_columns, fill_value=0)

        st.write("📊 Model Input Data", df)

        pred = model.predict(df)[0]
        proba = model.predict_proba(df)[0]

        winner = "🟥 Team 1 Wins" if pred == 100 else "🟦 Team 2 Wins"
        st.success(f"🏁 {winner}")
        st.info(f"Confidence → Team 1: {proba[0]:.2f}, Team 2: {proba[1]:.2f}")
