# db.py
# Redis-backed persistence for TDD-Gitflow Game

import redis
import json

# Initialize Redis client
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# Key patterns
games_key      = 'tddgame:games'
game_hash      = 'tddgame:game:{game_id}'
players_set    = 'tddgame:game:{game_id}:players'
player_hash    = 'tddgame:game:{game_id}:player:{player_id}'
history_list   = 'tddgame:game:{game_id}:player:{player_id}:history'

# -----------------------------------------------------------------------------
# storage for games.
#
# Each player now has a 'history' list of dicts:
#   { "commit": <sha>, "score": <int>, "feedback": <string> }
#
# We also keep 'last_commit' to know where to resume pulling.
# For convenience, we also store 'score' and 'latest_feedback' at the top‐level
# of player_data, so the admin dashboard (which reads p.score and p.message) still works.
#
# Structure:
# games = {
#   "GAMEID": {
#     "name": "Game Name",
#     "status": "running" | "paused" | "stopped",
#     "players": {
#       "PLAYERID": {
#         "name": "Alice",
#         "repo_full_name": "username/repo",
#         "score": 0,               # same as history[-1]['score']
#         "latest_feedback": "",    # same as history[-1]['feedback']
#         "last_commit": None,
#         "repo_path": "cloned_repos/GAMEID/PLAYERID",
#         "history": [
#             { "commit": "<sha1>", "score": 1, "feedback": "..." },
#             { "commit": "<sha2>", "score": 2, "feedback": "..." },
#             ...
#         ]
#       },
#       ...
#     }
#   },
#   ...
# }
# -----------------------------------------------------------------------------

# ------------------- Game-level operations -------------------
def list_games():
    """Return a list of all game IDs."""
    return list(redis_client.smembers(games_key))


def create_game_entry(game_id: str, name: str, status: str = 'running'):
    """Create a new game with given ID, name, and status."""
    redis_client.sadd(games_key, game_id)
    redis_client.hset(
        game_hash.format(game_id=game_id),
        mapping={'name': name, 'status': status}
    )


def get_game(game_id: str) -> dict:
    """Retrieve game metadata (name, status)."""
    game = redis_client.hgetall(game_hash.format(game_id=game_id))
    if game == {}:
        return None
    return game

def load_game_with_histories(game_id):
    game = get_game(game_id)
    if not game:
        return None

    players = {}
    for pid in list_players(game_id):
        pdata = get_player(game_id, pid)
        pdata['history'] = get_history(game_id, pid)
        players[pid] = pdata

    game['players'] = players
    return game

def update_game_status(game_id: str, status: str):
    """Set a game's status (running, paused, stopped)."""
    redis_client.hset(
        game_hash.format(game_id=game_id),
        'status', status
    )

# ------------------- Player-level operations -------------------
def list_players(game_id: str) -> list:
    """Return all player IDs registered in a given game."""
    return list(redis_client.smembers(players_set.format(game_id=game_id)))


def create_player_entry(game_id: str, player_id: str, data: dict):
    """
    Register a new player in a game. Data should contain:
      - name, repo_full_name, score, latest_feedback, last_commit
    """
    redis_client.sadd(players_set.format(game_id=game_id), player_id)
    redis_client.hset(
        player_hash.format(game_id=game_id, player_id=player_id),
        mapping={
            'name': data['name'],
            'repo_full_name': data['repo_full_name'],
            'score': data.get('score', 0),
            'latest_feedback': data.get('latest_feedback', ''),
            'last_commit': data.get('last_commit', ''),
            "repo_path": data.get('repo_path')
        }
    )


def get_player(game_id: str, player_id: str) -> dict:
    """Retrieve a player's metadata."""
    player = redis_client.hgetall(player_hash.format(game_id=game_id, player_id=player_id))
    if player == {}:
        return None
    return player



def update_player_field(game_id: str, player_id: str, field: str, value):
    """Update a single field in a player's hash."""
    redis_client.hset(
        player_hash.format(game_id=game_id, player_id=player_id),
        field, value
    )

# ------------------- Commit history operations -------------------
def reset_player(game_id: str, player_id: str):
    redis_client.delete(history_list.format(game_id=game_id, player_id=player_id))

    # Reset their metadata fields
    update_player_field(game_id, player_id, 'last_commit', '')
    update_player_field(game_id, player_id, 'score', 0)
    update_player_field(game_id, player_id, 'latest_feedback', '')
    # Also clear the paused flag
    update_player_field(game_id, player_id, 'paused', 0)

def append_history_entry(game_id: str, player_id: str, entry: dict):
    """
    Append a commit entry to a player's history list.
    Entry is a dict; it will be JSON-serialized.
    """
    redis_client.rpush(
        history_list.format(game_id=game_id, player_id=player_id),
        json.dumps(entry)
    )


def get_history(game_id: str, player_id: str) -> list:
    """Load the full commit history for a player (list of dicts)."""
    raw = redis_client.lrange(
        history_list.format(game_id=game_id, player_id=player_id),
        0, -1
    )
    return [json.loads(item) for item in raw]

# ------------------- some data for debugging -----

def populate_db():
    games = {
        "GAMEID": {
            "name": "My TDD Battle",
            "status": "running",
            "players": {
             # "PLAYERID": {
             #     "name": "Test1",
             #     "repo_full_name": "EduardoFF/fizzbuzz-tdd-inclass",
             #     "score": 0,
             #     "latest_feedback": "",    # same as history[-1]['feedback']
             #     "last_commit": None,
             #     "is_local": True,  # repo is local
             #     "repo_path": "cloned_repos/GAMEID/PLAYERID",
             #     "history": []

             # },
                "TESTER": {
                    "name": "TestLocal",
                    "repo_full_name": "LOCAL/fizzbuzz-tdd-inclass",
                    "score": 0,
                    "latest_feedback": "",    # same as history[-1]['feedback']
                    "last_commit": '',
                    "is_local": True,  # repo is local
                    "repo_path": "cloned_repos/GAMEID/TESTER",
                    "history": []
                },

                "TESTER-REMOTE": {
                    "name": "TestRemote",
                    "repo_full_name": "EduardoFF/fizzbuzz-tdd-game1",
                    "score": 0,
                    "latest_feedback": "",    # same as history[-1]['feedback']
                    "last_commit": '',
                    "repo_path": "cloned_repos/GAMEID/TESTER-REMOTE",
                    "history": []
                },
            }
        }
    }
    for game_id in games:
        game = games.get(game_id)
        if get_game(game_id) == None:
            create_game_entry(game_id, game.get('name'),
                              game.get('status'))
        for player_id in game['players']:
            if get_player(game_id, player_id) == None:
                create_player_entry(game_id, player_id,
                                    game['players'].get(player_id))


#games = {}
