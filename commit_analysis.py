import git
import os
import re
from pathlib import Path
import subprocess
from refactor_check import detect_refactoring

def run_tests(repo_path: str) -> bool:
    """
    Placeholder function.
    Should run the test suite at the current HEAD (e.g., via pytest or unittest)
    and return True if all tests pass, False otherwise.
    """
    # Example implementation (uncomment and adapt if using pytest):
    # import subprocess

    repo_dir = Path(repo_path)
    if not repo_dir.is_dir():
        raise ValueError(f"Invalid repo path: {repo_path!r}")

    try:
        result = subprocess.run(
            ["pytest", "--maxfail=1", "--disable-warnings", "-q"],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return (result.returncode == 0)
    except:
        raise NotImplementedError("Error")


def count_pytest_tests(repo_path: str) -> int:
    """
    Returns the number of tests that pytest is able to collect in the given repository.
    If pytest is not found or no tests are collected, returns 0.
    """
    repo_dir = Path(repo_path)
    if not repo_dir.is_dir():
        raise ValueError(f"Invalid repo path: {repo_path!r}")

    # Run pytest in "collect-only" mode, quiet output gives a summary line like "collected 12 items"
    try:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
    except FileNotFoundError:
        # pytest command not found
        return 0
    output = result.stdout.strip().lower()

    # 1) Handle the "no tests collected" case
    if re.search(r'\bno tests collected\b', output):
        return 0

    # 2) Handle "N test(s) collected"
    match = re.search(r'\b(\d+)\s+tests?\s+collected\b', output)
    if match:
        return int(match.group(1))

    # Fallback
    return 0


def get_commit_other_parents(repo_path: str, commit_sha: str) -> list:
    repo = git.Repo(repo_path)
    commit = repo.commit(commit_sha)
     # Only consider parents beyond the first (parent[0] is the “mainline”)
    other_parents = commit.parents[1:]
    result = []

    for parent in other_parents:
        sha = parent.hexsha
        # `git branch --contains <sha>` lists all branches whose history includes the SHA
        branches_output = repo.git.branch('--contains', sha)

        # Parse lines like:
        #   * main
        #     feature/foo
        #     remotes/origin/feature/foo
        names = []
        for line in branches_output.splitlines():
            # strip leading '*' (current HEAD) and whitespace
            name = line.lstrip('* ').strip()
            if name and name != 'main':
                result.append(name)
    return result

def is_merge_commit(repo_path: str, commit_sha: str) -> bool:
    repo = git.Repo(repo_path)
    commit = repo.commit(commit_sha)
    return len(commit.parents) > 1

def classify_commit(repo_path: str, commit_sha: str) -> dict:
    """
    Return a dict with:
      - classification : "red" | "green" | "refactor" | "unknown"
      - tests_passed   : bool
      - refactoring    : bool  (True if detect_refactoring==True)
      - merge          : None or the SHA of the merged-in parent (i.e. parents[1])

    Assumes:
      * tests live under "tests/"
      * prod code is "string_calculator.py"
      * run_tests() and detect_refactoring() are implemented elsewhere.
    """
    # 1) Open the repo and locate the commit
    repo = git.Repo(repo_path)
    commit = repo.commit(commit_sha)

    # 1) Check out this commit
    repo.git.checkout(commit.hexsha)

    # 2) Determine which files were modified in this commit
    if commit.parents:
        parent = commit.parents[0]
        diff_entries = commit.diff(parent)
    else:
        # Root commit: compare against an empty tree
        diff_entries = commit.diff(git.NULL_TREE)

    modified_files = [entry.a_path or entry.b_path for entry in diff_entries]

    # 3) Check if tests or production code changed
    TEST_DIR      = "test_"
    PROD_FILENAME = "fizzbuzz.py"
    tests_changed = any(path.startswith(TEST_DIR) for path in modified_files)
    code_changed  = any(path.endswith(PROD_FILENAME) for path in modified_files)

    print(commit.hexsha[:6], tests_changed, code_changed)
    ntests = count_pytest_tests(repo_path)
    print("Number of tests", ntests)
    # 4) Run the tests at this commit
    try:
        tests_passed = run_tests(repo_path)
        print("ok", tests_passed)
    except NotImplementedError:
        # If run_tests is not implemented, assume unknown
        tests_passed = False

 # 4) detect refactoring (only if code changed & tests still pass & no test changes)
    is_refactor = False
    if code_changed and tests_passed and not tests_changed and commit.parents:
        parent = commit.parents[0]
        old_path = os.path.join(repo_path, "__old_"+PROD_FILENAME)
        with open(old_path, "wb") as f:
            f.write((parent.tree / PROD_FILENAME).data_stream.read())
        new_path = os.path.join(repo_path, PROD_FILENAME)
        try:
            is_refactor = detect_refactoring(old_path, new_path)
        except NotImplementedError:
            is_refactor = False
        finally:
            os.remove(old_path)


        repo.git.checkout(commit.parents[0])
        try:
            old_tests_passed = run_tests(repo_path)
        except NotImplementedError:
            # If run_tests is not implemented, assume unknown
            old_tests_passed = None
        is_refactor = is_refactor and old_tests_passed
    # Check out this commit, again
    repo.git.checkout(commit.hexsha)


    # 5) Classify based on the combination of (tests_changed, code_changed, tests_pass)
    if tests_changed and not code_changed and not tests_passed and ntests > 0:
        classification = "red"
    elif is_refactor:
        classification = 'refactor'
    elif code_changed and not tests_changed and tests_passed:
        classification = "green"
    else:
        classification = 'unknown'

    return {
        "commit_classify": classification,
        "tests_passed":   tests_passed,
        "is_refactoring":    is_refactor,
        }

def classify_commits(repo_path: str) -> dict:
    """
    Walks all commits (chronologically) on the repo's default branch (assumed "main"),
    classifies each commit, and returns a dictionary mapping commit.hash -> classification.

    Usage:
        result = classify_commits("/path/to/stringCalculator-kata")
        # e.g. result == { "a1b2c3": "red", "d4e5f6": "green", ... }
    """
    repo = git.Repo(repo_path)
    commits = list(repo.iter_commits("main", reverse=True))
    classification = {}

    for commit in commits:
        print(commit.hexsha[-5:])
        cls = classify_commit(repo, commit)
        classification[commit.hexsha] = cls

    # (Optionally) check out back to the HEAD of "main" at the end:
    repo.git.checkout("main")
    print(classification)
    return classification
