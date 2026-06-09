# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

Requires Redis running locally on port 6379.

```bash
source venv/bin/activate
python app.py          # runs on port 5000 by default
```

Or via gunicorn (production):
```bash
gunicorn app:app
```

The `.env` file must define `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, and `AZURE_OPENAI_DEPLOYMENT_NAME`. The `dotenv` package loads it automatically at startup via `llm_analysis.py`.

There are no automated tests in this repo.

## Architecture

This is a single-process Flask app backed by Redis. A background daemon thread (`poll_repos_loop`) runs every 5 seconds to pull student repos and process new commits.

**Data flow for a new commit:**
1. `poll_repos_loop` (app.py) clones/pulls each player's GitHub repo into `cloned_repos/<game_id>/<player_id>/`
2. `classify_commit` (commit_analysis.py) checks out each new SHA, runs `pytest`, inspects diffs, and classifies the commit as `red | green | refactor | unknown`
3. `detect_refactoring` (refactor_check.py) compares old vs new production file by AST structure (ignoring docstrings/comments)
4. `find_merge_commits` (commit_analysis.py) labels entries where a feature branch was merged
5. `score_all` (score.py) computes per-commit and overall scores based on TDD step scores + transition bonuses + merge bonuses
6. `analyze_commits_with_llm` (llm_analysis.py) calls Azure OpenAI (gpt-4o) with the structured commit data and the FizzBuzz prompt (`prompt_fizzbuzz.txt`) to generate per-commit and overall feedback
7. Results are stored in Redis and returned via the player/admin views

**Redis key schema** (defined in db.py):
- `tddgame:games` — set of all game IDs
- `tddgame:game:<game_id>` — hash: `name`, `status`
- `tddgame:game:<game_id>:players` — set of player IDs
- `tddgame:game:<game_id>:player:<player_id>` — hash: `name`, `repo_full_name`, `score`, `latest_feedback`, `last_commit`, `repo_path`
- `tddgame:game:<game_id>:player:<player_id>:history` — list of JSON-serialized commit entry dicts

**Commit entry dict structure** (passed between modules and stored in Redis history):
```python
{
    "commit": "<sha>",
    "branches": ["feature/foo"],   # non-main branches this commit appears on
    "feedback": "<llm feedback>",
    "analysis": {
        "commit_classify": "red|green|refactor|unknown",
        "tests_passed": bool,
        "is_refactoring": bool,
    },
    "is_merge": bool,
    "score": float,           # added by score_all
    "num_merges": float,      # added by score_all
    "bad_in_row": int,        # added by score_all
}
```

**Scoring weights** (score.py):
- Base: red=0.5, green=1.0, refactor=0.75, unknown=0.0
- Transition bonuses: red→green, green→refactor, refactor→red = +0.5; same step = +0.2; invalid = −0.5
- Refactor bonus: +0.3
- Merge of green commit: +1.0; merge of non-green: −0.5
- Gitflow score: proportional to merges seen before this commit

**Kata support:** Currently only FizzBuzz. The commit classifier looks for `test_` prefix files and `calc.py` as the production file. `prompt_fizzbuzz.txt` is the LLM system prompt. To add a new kata, both the classifier constants and the prompt file would need to change.

**URL prefix:** `app.py` installs a `PrefixRule` that prepends `tdd-game` to all `url_for`-generated paths. This is intended for reverse-proxy deployments where the app is served under `/tdd-game/`. When running standalone, all routes are still registered under `/` and matched normally—only the generated redirect URLs are affected.

**`populate_db()`** is called at module load and seeds a default game `TDD-GAME` if it doesn't already exist in Redis.
