import requests

API_KEY = "2a90ced375eda9ceda27d4a6c6883e9b"

def get_player(name):
    url = "https://api.wotblitz.com/wotb/account/list/"
    params = {
        "application_id": API_KEY,
        "search": name
    }
    response = requests.get(url, params=params)
    return response.json()

def get_player_stats(account_id):
    url = "https://api.wotblitz.com/wotb/account/info/"
    params = {
        "application_id": API_KEY,
        "account_id": account_id
    }
    response = requests.get(url, params=params)
    data = response.json()

    stats = data["data"][str(account_id)]["statistics"]["all"]
    battles = stats["battles"]
    wins = stats["wins"]

    if battles == 0:
        return {"winrate": 0, "battles": 0, "wins": 0}

    winrate = round((wins / battles) * 100, 2)
    return {
        "winrate": winrate,
        "battles": battles,
        "wins": wins
    }
