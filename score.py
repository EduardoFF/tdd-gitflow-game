import re

# 0) TDD‐step base scores
TDD_STEP_SCORES = {
    "red":      0.5,
    "green":    1.0,
    "refactor": 0.75,
    "unknown":  0.0,
}

# 1) Message‐quality (0–1)
def message_quality_score(msg: str) -> float:
    score = 0.0
    if re.match(r"^(Add|Fix|Refactor|Remove|Update|Implement)\b", msg):
        score += 0.5
    if 15 <= len(msg) <= 50:
        score += 0.5
    return score

# 2) Git‐flow branch scoring (0.5–1.0, minus merge penalty moved to merge‐non‐green logic)
def gitflow_score(branches: list) -> float:
    for branch in branches:
        if branch.startswith("feature/"):
            return 1.0
    for branch in branches:
        if branch.startswith(("develop", "release/")):
            return 0.8
    return 0.0 if len(branches) == 0 else 0.5

# 3) Transition bonuses / penalties
TRANSITION_BONUSES = {
    ("red",    "green"):     +0.5,
    ("green",  "refactor"):  +0.5,
    ("refactor","red"):      +0.5,
    ("green",  "red"):       +0.0,   # allowed
}
REPEAT_BONUS     = +0.2
INVALID_PENALTY  = -0.5
REFACTOR_BONUS   = +0.3
MERGE_NONGREEN_PENALTY = -0.5
MERGE_BONUS = 1.0

def transition_score(prev: str, curr: str) -> float:
    if (prev, curr) in TRANSITION_BONUSES:
        return TRANSITION_BONUSES[(prev, curr)]
    if prev == curr:
        return REPEAT_BONUS
    return INVALID_PENALTY

# 4) Score a single commit_info dict
def score_commit_info(ci: dict) -> float:
    # skip is already scored
    if 'score' in ci:
        return ci['score']
    cls = ci["analysis"]["commit_classify"]
    base   = 0.5 if cls in TDD_STEP_SCORES else -0.1
    if base < 0:
        base *= (1.05**ci['bad_in_row'])
    msg_sc = message_quality_score(ci["feedback"])
    tdd_sc = TDD_STEP_SCORES.get(cls, 0.0)
    flow_sc = ci['num_merges']
    #flow_sc= gitflow_score(ci["branches"])

    total = base + msg_sc + tdd_sc + flow_sc
    if ci["analysis"]["is_refactoring"]:
        total += REFACTOR_BONUS
    # if not scored before, add it to the dictionary
    ci['score'] = round(total,2)
    ci['base'] = base
    return ci['score']

# 5) Aggregate over the sequence, applying transition bonuses and merge‐non‐green penalty
def score_all(commits_info: list) -> dict:

    # score gitflow by number of merges before the commit number
    for i in range(len(commits_info)):
        ci = commits_info[i]
        if 'num_merges' in ci:
            continue
        nmerges = 0
        for j in range(i, 0, -1):
            if commits_info[j]['is_merge']:
                nmerges+=1
        ci['num_merges'] =  (nmerges / i) if i > 0 else 0
        if ci['analysis']['commit_classify'] == 'unknown':
            nbad = 0
            for j in range(i-1, 0, -1):
                if commits_info[j]['analysis']['commit_classify'] == 'unknown':
                    nbad+=1
                else:
                    break
            ci['bad_in_row'] = nbad
        else:
            ci['bad_in_row'] = 0



    # 5a) per‐commit base scores
    bases = [score_commit_info(ci) for ci in commits_info]
    # 5b) per‐commit transition bonuses (first commit has none)
    trans = [0.0] + [
        transition_score(
            commits_info[i-1]["analysis"]["commit_classify"],
            commits_info[i]["analysis"]["commit_classify"]
        )
        for i in range(1, len(commits_info))
    ]

    trans_desc = ['first'] + [
        (
            commits_info[i-1]["analysis"]["commit_classify"],
            commits_info[i]["analysis"]["commit_classify"]
        )
        for i in range(1, len(commits_info))
    ]

    detailed = []
    overall  = 0.0



    for ci, b, t, tdesc in zip(commits_info, bases, trans, trans_desc):
        # 5c) merge‐non‐green penalty
        mp = 0.0
        if ci["is_merge"] and ci["analysis"]["commit_classify"] != "green":
            mp = MERGE_NONGREEN_PENALTY
        elif ci['is_merge']:
            mp = MERGE_BONUS

        total_sc = round(b + t + mp, 2)
        overall += total_sc

        detailed.append({
            "commit":           ci["commit"],
            "branches":           ci["branches"],
            "base_score":       b,
            "transition_bonus": t,
            "transition":       tdesc,
            "merge_score":      mp,
            "total_score":      total_sc
        })

    return {
        "per_commit":    detailed,
        "overall_score": round(overall, 2)
    }
