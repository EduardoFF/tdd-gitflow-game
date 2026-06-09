import os
from typing import List, Dict

import openai
from openai import AzureOpenAI
from dotenv import load_dotenv
import json
load_dotenv()   # reads .env into os.environ

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure")

# Azure config
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")


def _get_client():
    if LLM_PROVIDER == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise EnvironmentError("OPENROUTER_API_KEY must be set when LLM_PROVIDER=openrouter")
        return openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
        )
    else:
        if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]):
            raise EnvironmentError(
                "Please set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME"
            )
        return AzureOpenAI(
            api_version="2024-12-01-preview",
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
        )


def _get_model() -> str:
    if LLM_PROVIDER == "openrouter":
        model = os.getenv("OPENROUTER_MODEL")
        if not model:
            raise EnvironmentError("OPENROUTER_MODEL must be set when LLM_PROVIDER=openrouter")
        return model
    return AZURE_OPENAI_DEPLOYMENT


def test_api_call() -> str:
    client = _get_client()
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say hello in one word."}
    ]
    response = client.chat.completions.create(
        messages=messages,
        max_tokens=4096,
        temperature=1.0,
        top_p=1.0,
        model=_get_model()
    )
    content = response.choices[0].message.content.strip()
    print(f"API Test Success: {content}")
    return content

def extract_json(text: str) -> str:
    # Find the first "{" and the last "}"
    start = text.find('{')
    end   = text.rfind('}') + 1
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM output")
    return text[start:end]


def analyze_commits_with_llm(entries: List[Dict]) -> Dict:
    """
    Calls the LLM with a system prompt (loaded from prompt_path) and the list of commit entries.
    Parses the JSON response, validates its structure, and retries up to 3 times if invalid.

    Args:
        entries: List of commit entry dicts as defined in the game schema.

    Returns:
        Parsed JSON dict with keys 'per_commit_feedback' and 'overall_feedback'.

    Raises:
        RuntimeError: If a valid response isn't obtained after 3 attempts.
    """
    prompt_path = 'prompt_fizzbuzz.txt'
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    with open(prompt_path, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    client = _get_client()
    model = _get_model()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({"commits": entries})}
    ]

    last_error = None
    for attempt in range(1, 4):
        response = client.chat.completions.create(
            messages=messages,
            max_tokens=4096,
            top_p=1.0,
            model=model,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        clean = extract_json(content)
        print(clean)

        try:
            result = json.loads(clean)
        except json.JSONDecodeError as e:
            last_error = f"Attempt {attempt}: JSON decode error: {e}"
            messages.append({"role": "user", "content": last_error})
            continue

        per_commit = result.get("per_commit_feedback")
        overall    = result.get("overall_feedback")
        if not isinstance(per_commit, list) or not isinstance(overall, str):
            last_error = f"Attempt {attempt}: Missing or invalid keys in response.\nResponse keys: {list(result.keys())}"  # noqa: E501
            messages.append({"role": "user", "content": last_error})
            continue

        if len(per_commit) != len(entries):
            last_error = f"Attempt {attempt}: Expected {len(entries)} feedback entries, got {len(per_commit)}"
            messages.append({"role": "user", "content": last_error})
            continue

        return result

    raise RuntimeError(f"Failed to get valid LLM response after 3 attempts. Last error: {last_error}")


if __name__ == "__main__":
    test_api_call()
