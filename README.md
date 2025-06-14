# TDD-Gitflow Game

A lightweight Flask webapp that turns TDD katas into a team competition, teaching students GitFlow basics and test-driven development through an engaging “score-and-feedback” game.

---

## 🚀 Features

- **Admin**
  - Create a new game for a chosen TDD kata
  - Pause, resume or stop an active game
  - View each team’s latest score & feedback

- **Team Registration**
  - Join an existing game by entering a Game ID and your GitHub repo URL
  - Back-end polls your repo, analyzes each commit, and assigns per-commit feedback

- **Player Dashboard**
  - Live scoreboard & feedback for your latest commit
  - Full history table with:
    - Commit SHA
    - Branch name
    - Classification (red/green/refactor/unknown)
    - Passed tests, refactoring & merge flags
    - Per-commit score (total commits)
    - LLM-generated TDD feedback

- **Commit History**
  - `/history/<game_id>/<player_id>` shows the above table in one dedicated page

---

## 🎯 Supported Katas

| Kata      | Description                     |
|-----------|---------------------------------|
| FizzBuzz  | Classic “print Fizz/Buzz” kata  |
| *(future)*| *add more TDD kata entries*     |

---

## 📄 Pages

| Route                                      | Purpose                                                |
|--------------------------------------------|--------------------------------------------------------|
| `/`                                        | Home: create a game or enter a Game ID to join         |
| `/create_game` (POST)                      | Admin: submit a new game name → Game ID generated      |
| `/admin/<game_id>`                         | Admin dashboard: control & monitor an active game      |
| `/join/<game_id>`                          | Join form: enter your name & GitHub repo to register   |
| `/player/<game_id>/<player_id>`            | Team view: live score, latest feedback, and history    |
| `/history/<game_id>/<player_id>`           | Full commit history table with per-commit analysis     |

---

## ⚙️ Installation & Running

1. **Clone & install**
   ```bash
   git clone https://github.com/your-org/tdd-gitflow-game.git
   cd tdd-gitflow-game
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt``
