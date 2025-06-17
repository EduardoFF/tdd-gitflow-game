import os
import string
import random
import threading
import time
import subprocess
import logging
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from commit_analysis import (
    classify_commits,
    classify_commit,
    is_merge_commit,
    get_commit_other_parents,
    find_merge_commits
    )
from score import score_all

from db import (
    list_games,
    create_game_entry,
    get_game,
    load_game_with_histories,
    update_game_status,
    list_players,
    create_player_entry,
    get_player,
    update_player_field,
    append_history_entry,
    reset_player,
    get_history,
    populate_db
)

from llm_analysis import analyze_commits_with_llm

app = Flask(__name__)

# Create a stream handler
#stream_handler = logging.StreamHandler()

# Add the handler to the logger
#app.logger.addHandler(stream_handler)

# Set the logging level
app.logger.setLevel(logging.INFO)


# Base directory where all repos will be cloned
BASE_CLONE_DIR = os.path.join(os.getcwd(), 'cloned_repos')
os.makedirs(BASE_CLONE_DIR, exist_ok=True)

def generate_id(length=6):
    """Generate a random uppercase alphanumeric ID."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def parse_full_name(url: str):
    """
    Given a GitHub URL (HTTPS or SSH), return "username/repo" (no .git suffix).
    Returns None if parsing fails.
    """
    u = url.rstrip('/')
    if u.endswith('.git'):
        u = u[:-4]
    if u.startswith('git@github.com:'):
        return u.replace('git@github.com:', '')
    if u.startswith('https://github.com/'):
        return u.replace('https://github.com/', '')
    return None

def run_subprocess(cmd_list, cwd=None):
    """
    Helper to run a subprocess, returning (exit_code, stdout, stderr).
    We'll decode stdout/stderr as UTF-8.
    """
    try:
        completed = subprocess.run(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            timeout=30
        )
        return (completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    except Exception as e:
        return (1, "", f"Exception: {e}")

def get_all_commit_shas(local_path, branch="main"):
    """
    Return a list of all commit SHAs in chronological order (oldest→newest).
    Equivalent to: git rev-list --reverse HEAD
    """
    ret, out, err = run_subprocess(['git', 'rev-list', '--topo-order', '--reverse', branch], cwd=local_path)
    if ret != 0:
        return None
    # Each line is one SHA
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_commit_message(local_path, sha):
    """
    Return the commit message for a given SHA:
      git log -1 --pretty=format:%B <sha>
    """
    ret, out, err = run_subprocess(['git', 'log', '-1', '--pretty=format:%B', sha], cwd=local_path)
    if ret != 0:
        return None
    return out.strip()

def get_commit_count(local_path, sha):
    """
    Return the total number of commits up to (and including) this SHA:
      git rev-list --count <sha>
    """
    ret, out, err = run_subprocess(['git', 'rev-list', '--count', sha], cwd=local_path)
    if ret != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def fetch_new_commits(local_path, last_commit, branch='main'):
    """
    If last_commit is None:
      - Return a list of ALL commit SHAs in chronological order.
    Else:
      - Return a list of new commit SHAs (chronological) from last_commit..HEAD.

    Returns None on error, or [] if no new commits, or a list of SHAs.
    """
    if last_commit == '':
        # All commits:
        shas = get_all_commit_shas(local_path, branch)
        return shas  # may be [] if empty repo
    else:
        # Shas from last_commit (exclusive) to HEAD (inclusive), in chronological order
        ret, out, err = run_subprocess(
            ['git', 'rev-list',  '--topo-order', '--reverse', f'{last_commit}..HEAD'],
            cwd=local_path
        )
        if ret != 0:
            return None
        return [line.strip() for line in out.splitlines() if line.strip()]

def initialize_or_pull_repo(game_id, player_id, player_data):
    """
    Ensure that the repo is cloned under BASE_CLONE_DIR/game_id/player_id.
    If not yet cloned, do 'git clone'. Then always attempt 'git pull' to bring it up to date.

    Finally:
      - Compute new HEAD commit hash
      - Compute commit_count for the new HEAD
      - Return (new_head, commit_count, list_of_new_shas) or (None, None, None) on error.
    """

    game = get_game(game_id)
    if not game:
        abort(404,description="Game not found")

    player = get_player(game_id, player_id)
    if not player:
        abort(404, description="Player not found")

    local_path = player['repo_path']
    os.makedirs(local_path, exist_ok=True)


    # 1) If the directory doesn't exist, do a 'git clone <url> <local_path>'
    if not os.path.isdir(os.path.join(local_path, '.git')) and not player.get('is_local', False):
        clone_url = f"https://github.com/{player_data['repo_full_name']}.git"
        print(f"cloning {clone_url} into {local_path}")
        ret, out, err = run_subprocess(['git', 'clone', clone_url, local_path])
        if ret != 0:
            print("Error", ret, out, err)
            update_player_field(game_id, player_id,
                                'latest_feedback',
                                f"Error cloning: {err}")
            return (None, None, None)

    ret, out, err = run_subprocess(['git', 'checkout', 'main'], cwd=local_path)
    if ret != 0:
        update_player_field(game_id, player_id,
                            'latest_feedback',
                            f"Error checking out: {err}")
        return (None, None, None)

    # 2) Do 'git pull' inside local_path
    if not player_data.get('is_local', False):

        ret, out, err = run_subprocess(['git', 'pull'], cwd=local_path)
        if ret != 0:
            update_player_field(game_id, player_id,
                            'latest_feedback',
                                f"Error pulling: {err}")
            return (None, None, None)

    # 3) Get current HEAD commit hash: `git rev-parse HEAD`
    ret, head_hash, err = run_subprocess(['git', 'rev-parse', 'HEAD'], cwd=local_path)
    if ret != 0:
        update_player_field(game_id, player_id,
                            'latest_feedback',
                            f"Error rev-parse: {err}")
        return (None, None, None)
    head_hash = head_hash.strip()

    # 4) Get total commit count: `git rev-list --count HEAD`
    commit_count = get_commit_count(local_path, head_hash)

    # 5) Determine which SHAs are new (relative to player_data['last_commit'])
    new_shas = fetch_new_commits(local_path, player_data.get('last_commit'))
    if new_shas is None:
        update_player_field(game_id, player_id,
                            'latest_feedback',
                            "Error retrieving commit SHAs.")
        return (None, None, None)

    return (head_hash, commit_count, new_shas)


def poll_repos_loop():
    """
    Background thread that every 5 seconds:
    - Iterates over all RUNNING games
    - For each player in each game:
        * Clones (if missing) or pulls their repo
        * Checks HEAD vs. last_commit
        * For each new commit SHA (in chronological order):
            - Compute its commit_count
            - Get its commit message
            - Call the LLM to get per‐commit feedback
            - Append {commit, score, feedback} to player_data['history']
        * Update player_data['last_commit'] to the newest SHA
        * Keep player_data['score'] and player_data['latest_feedback'] in sync
    """
    while True:
        time.sleep(5)
        app.logger.info("Polling...")
        for game_id in list_games():
            game_data = load_game_with_histories(game_id)
            if game_data['status'] != 'running':
                continue
            for player_id in list_players(game_id):
                player_data = game_data['players'].get(player_id)
                # If the game was paused/stopped in the meantime, skip
                if game_data['status'] != 'running':
                    continue

                new_head, _, new_shas = initialize_or_pull_repo(game_id, player_id, player_data)
                print(new_shas)
                if new_head is None:
                    # Error message is already in player_data['latest_feedback']
                    continue

                #classify_commits(player_data.get('repo_path'))
                last_head = player_data.get('last_commit')
                print('last head: ', last_head)
                if not new_shas:
                    # No new commits → skip
                    continue

                # Process each new commit SHA in chronological order (only for main)
                new_entries = []
                while len(new_shas) > 0:
                    sha = new_shas.pop(0)
                    # Compute commit_count for this SHA
                    count = get_commit_count(player_data['repo_path'], sha) or 0

                    # Retrieve the commit message for this SHA
                    msg = get_commit_message(player_data['repo_path'], sha) or "(no commit message)"

                    if is_merge_commit(player_data['repo_path'], sha):
                        app.logger.info(f'commmit {sha} is merge')
                        analysis = classify_commit(player_data['repo_path'], sha)
                        # we call analysis because we need the other fields
                        # but we rewrite classify, because we know it is a merge
                        # TODO: clean this, merge detection should be inside classify
                        analysis['commit_classify'] = 'merge'
                        #other = get_commit_other_parents(player_data['repo_path'],
                        #                                 sha)
                        # app.logger.info(f'retrieving other commits from {other}')

                        # for otherbranch in other[::-1]:
                        #     other_commits = get_all_commit_shas(
                        #         player_data['repo_path'], branch=otherbranch)
                        #     app.logger.info(f'added other commmits {other_commits} from {otherbranch}')
                        #     for othersha in other_commits:
                        #         if othersha not in processed_shas:
                        #             new_shas.insert(0, othersha)
                        #             other_branch_shas[othersha] = otherbranch
                        # app.logger.info(f'commmit {sha} merge from {other}')

                    else:
                        analysis = classify_commit(player_data['repo_path'], sha)


                    # Append to history
                    entry = {
                        "commit": sha,
                        "branches": get_commit_other_parents(
                            player_data['repo_path'],sha),
                        "feedback": '',
                        "analysis": analysis,
                        "is_merge": False,
                    }
                    new_entries.append(entry)

                # Finally, set last_commit to the newest SHA (the last element of new_shas)
                update_player_field(game_id, player_id, 'last_commit', new_head)
                print(f'player {player_id} last commit {new_head}')

                # detect merges
                find_merge_commits(new_entries)

                # scores are computed last
                score = score_all(player_data['history'] + new_entries)

                # Call LLM to analyze single commits

                feedback = analyze_commits_with_llm(new_entries)
                print(feedback)

                pc_feedback = feedback['per_commit_feedback']
                for i in range(len(new_entries)):
                    new_entries[i]['feedback'] = pc_feedback[i]['feedback']
                    if new_entries[i]['commit'] != pc_feedback[i]['commit']:
                        print('WARNING: commit sha does not match in feedback')


                for entry in new_entries:
                    append_history_entry(game_id, player_id, entry)


                #print('score: ', score)

                # update scores in the history
                #for sha in score['per_commit']:


                # Update top‐level score & latest_feedback for admin
                update_player_field(game_id, player_id, 'score',
                                    score['overall_score'])
                player_data['latest_feedback'] = feedback['overall_feedback']
                update_player_field(game_id, player_id,
                                    'latest_feedback',
                                    player_data['latest_feedback'])








@app.route('/')
def index():
    """Home page: let user create a new game or join an existing one."""
    # Build a dict of all games we know about
    games = {}
    for game_id in list_games():
        game = get_game(game_id)
        # meta is a dict with 'name' and 'status' strings
        games[game_id] = {
            'name': game.get('name', ''),
            'status': game.get('status', '')
        }
    return render_template('index.html', games=games)
    return render_template('index.html')


@app.route('/create_game', methods=['POST'])
def create_game():
    """Handle game creation: user provides a game name."""
    game_name = request.form.get('game_name', '').strip()
    if not game_name:
        return "Game name is required", 400

    # Generate a unique game ID
    while True:
        game_id = generate_id(6)
        if game_id not in games:
            break

    create_game_entry(game_id, game_name, status='running')
    # # Initialize the game in memory
    # games[game_id] = {
    #     'name': game_name,
    #     'status': 'running',
    #     'players': {}
    # }

    # Redirect to the admin dashboard for this new game
    return redirect(url_for('admin_dashboard', game_id=game_id))


@app.route('/admin/<game_id>')
def admin_dashboard(game_id):
    """Show admin dashboard for a given game."""
    game = load_game_with_histories(game_id)
    print(game)
    if game is None:
        abort(404, description="Game not found")

    return render_template('admin.html', game_id=game_id, game=game)


@app.route('/pause_game/<game_id>', methods=['POST'])
def pause_game(game_id):
    """Pause the game."""
    game = get_game(game_id)
    if not game:
        abort(404)
    update_game_status(game_id, 'paused')
    return redirect(url_for('admin_dashboard', game_id=game_id))


@app.route('/resume_game/<game_id>', methods=['POST'])
def resume_game(game_id):
    """Resume (unpause) the game."""
    game = get_game(game_id)
    if not game:
        abort(404,description="Game not found")
    update_game_status(game_id, 'running')
    return redirect(url_for('admin_dashboard', game_id=game_id))


@app.route('/stop_game/<game_id>', methods=['POST'])
def stop_game(game_id):
    """Stop the game."""
    game = get_game(game_id)
    if not game:
        abort(404,description="Game not found")

    update_game_status(game_id, 'stopped')
    return redirect(url_for('admin_dashboard', game_id=game_id))


@app.route('/join/<game_id>')
def join_form(game_id):
    """
    Show the form to join an existing game by ID.
    """
    game = get_game(game_id)
    if not game:
        abort(404,description="Game not found")

    if game['status'] != 'running':
        return "Cannot join: game is not running", 400
    return render_template('join.html', game_id=game_id, game=game)

@app.route('/join_game', methods=['POST'])
def join_game():
    """Handle user joining a game: form fields → game_id, player_name, repo_url."""
    game_id = request.form.get('game_id', '').strip().upper()
    player_name = request.form.get('player_name', '').strip()
    repo_url = request.form.get('repo_url', '').strip()

    if not game_id or not player_name or not repo_url:
        return "Missing game ID, player name, or repo URL", 400

    game = get_game(game_id)
    if not game:
        abort(404,description="Game not found")

    if game['status'] != 'running':
        return "Cannot join: game is not running", 400

    repo_full_name = parse_full_name(repo_url)
    if repo_full_name is None or '/' not in repo_full_name:
        return "Invalid GitHub repo URL", 400

    # Check if this repo is already registered in this game
    players = list_players(game_id)
    for pid in players:
        p = get_player(game_id, pid)      # dict with name, score, etc.
        if p['repo_full_name'].lower() == repo_full_name.lower():
            return "That repository is already registered by another player in this game", 400

    # Create a new player ID
    while True:
        player_id = generate_id(6)
        if player_id not in players:
            break

    # Set up initial player data (no commits yet)
    player_data = {
        'name': player_name,
        'repo_full_name': repo_full_name,
        'score': 0,
        'latest_feedback': 'Waiting for initial pull…',
        'last_commit': None,
        'repo_path': os.path.join(BASE_CLONE_DIR, game_id, player_id),
        'history': []
    }
    create_player_entry(game_id, player_id, player_data)
    return redirect(url_for('player_view', game_id=game_id, player_id=player_id))

@app.route('/player/<game_id>/<player_id>')
def player_view(game_id, player_id):

    """Show the player's dashboard: their score, feedback, and a global scoreboard."""
    game = load_game_with_histories(game_id)
    if not game:
        abort(404,description="Game not found")

    player = game['players'].get(player_id)
    if not player:
        abort(404, description="Player not found")


    # Pass the entire players dict for the scoreboard
    return render_template(
        'player.html',
        game_id=game_id,
        player_id=player_id,
        player=player,
        players=game['players']
    )

@app.route('/admin/<game_id>/<player_id>')
def admin_player_view(game_id, player_id):
    game = get_game(game_id)
    if not game:
        abort(404, "Game not found")

    player = get_player(game_id, player_id)
    if not player:
        abort(404, "Player not found")

    # Load full commit history
    player['history'] = get_history(game_id, player_id)
    # Ensure a paused flag is available
    player['paused'] = player.get('paused', False)

    return render_template(
        'admin_player.html',
        game_id=game_id,
        player_id=player_id,
        player=player
    )
@app.route('/admin/<game_id>/<player_id>/pause', methods=['POST'])
def pause_player(game_id, player_id):
    player = get_player(game_id, player_id)
    if not player:
        abort(404, "Player not found")

    # Toggle the paused state
    new_state = not player.get('paused', False)
    update_player_field(game_id, player_id, 'paused', 1 if new_state else 0)
    return redirect(url_for('admin_player_view', game_id=game_id, player_id=player_id))

@app.route('/admin/<game_id>/<player_id>/reset_history', methods=['POST'])
def reset_history(game_id, player_id):
    player = get_player(game_id, player_id)
    if not player:
        abort(404, "Player not found")

    # Clear the Redis list that holds their history
    reset_player(game_id, player_id)


    return redirect(url_for('admin_player_view', game_id=game_id, player_id=player_id))

@app.route('/score/<game_id>/<player_id>')
def get_score(game_id, player_id):
    """
    Return JSON with the player's latest status, score, and latest feedback.
    Clients will poll this endpoint every few seconds.
    """
    game = get_game(game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404
    player = get_player(game_id, player_id)      # dict with name, score, etc.
    if not player:
        return jsonify({'error': 'Player not found'}), 404

    return jsonify({
        'status': game['status'],
        'score': player['score'],
        'message': player['latest_feedback']
    })


from flask import jsonify

# Scoreboard view
@app.route('/player/<game_id>')
def scoreboard_view(game_id):
    game = get_game(game_id)
    if not game:
        abort(404, "Game not found")

    # Load all players and their scores
    players = {}
    for pid in list_players(game_id):
        pdata = get_player(game_id, pid)
        players[pid] = { 'name': pdata['name'], 'score': float(pdata.get('score', 0)) }

    return render_template(
        'scoreboard.html',
        game_id=game_id,
        game=game,
        players=players
    )

# JSON endpoint for dynamic updates\@app.route('/player/<game_id>/scores')
def scoreboard_scores(game_id):
    if not get_game(game_id):
        abort(404, "Game not found")

    data = []
    for pid in list_players(game_id):
        pdata = get_player(game_id, pid)
        data.append({ 'id': pid, 'name': pdata['name'], 'score': float(pdata.get('score', 0)) })

    # sort descending by score
    data.sort(key=lambda x: x['score'], reverse=True)
    return jsonify(players=data)

@app.route('/history/<game_id>/<player_id>')
def history_for_player(game_id, player_id):
    """
    Show commit history for a specific game and player.
    """
    game = get_game(game_id)
    if not game:
        abort(404, description="Game not found")
    player = get_player(game_id, player_id)
    if not player:
        abort(404, description="Player not found")

    player['history'] = get_history(game_id, player_id)
    # Render a page showing this player's full commit history
    return render_template('history.html', game_id=game_id, player=player)


# -----------------------------------------------------------------------------
# Launch the background polling thread once, before first request
# -----------------------------------------------------------------------------
def start_polling_thread():
    app.logger.info("Starting polling thread...")
    thread = threading.Thread(target=poll_repos_loop, daemon=True)
    thread.start()


# -----------------------------------------------------------------------------
# Run the Flask app
# -----------------------------------------------------------------------------

populate_db()

start_polling_thread()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    start_polling_thread()
    app.run(host='0.0.0.0', port=port, debug=True)
