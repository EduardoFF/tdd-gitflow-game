import os
from typing import List, Dict

from openai import AzureOpenAI
from dotenv import load_dotenv
import json
load_dotenv()   # reads .env into os.environ

# Configuration: Azure OpenAI credentials and deployment name loaded from environment variables
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")


def _get_client():
    """
    Internal helper to instantiate the Azure OpenAI client.
    Raises if any required environment variable is missing.
    """
    if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]):
        raise EnvironmentError(
            "Please set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY_OPENAI_DEPLOYMENT_NAME environment variables"
        )

    endpoint = AZURE_OPENAI_ENDPOINT
    #model_name = "gpt-35-turbo"
    #deployment = "gpt-35-turbo"
    model_name = "gpt-4o"
    deployment = "gpt-4o"

    subscription_key = AZURE_OPENAI_KEY
    api_version = "2024-12-01-preview"

    client = AzureOpenAI(
        api_version=api_version,
        azure_endpoint=endpoint,
        api_key=subscription_key,
    )
    return client


def test_api_call() -> str:
    """
    Test function to verify that the Azure OpenAI API is reachable and returns a valid response.

    Sends a simple prompt and prints the model's reply.
    Returns the content of the reply.
    """
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
        model=AZURE_OPENAI_DEPLOYMENT
    )

    # Extract the first choice
    content = response.choices[0].message.content.strip()
    print(f"API Test Success: {content}")
    return content

def extract_json(text: str) -> str:
    # Find the first “{” and the last “}”
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
        prompt_path: Path to the .txt file containing the LLM prompt.

    Returns:
        Parsed JSON dict with keys 'per_commit_feedback' and 'overall_feedback'.

    Raises:
        RuntimeError: If a valid response isn't obtained after 3 attempts.
    """
    # Load the prompt
    prompt_path = 'prompt_fizzbuzz.txt'
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    with open(prompt_path, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    client: OpenAIClient = _get_client()
    # Initial messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({"commits": entries})}
    ]

    last_error = None
    # Retry loop
    for attempt in range(1, 4):
        response = client.chat.completions.create(
            messages=messages,
            max_tokens=4096,
            top_p=1.0,
            model=AZURE_OPENAI_DEPLOYMENT,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        clean = extract_json(content)
        print(clean)


        # Attempt to parse JSON
        try:
            result = json.loads(clean)
        except json.JSONDecodeError as e:
            last_error = f"Attempt {attempt}: JSON decode error: {e}"
            messages.append({"role": "user", "content": last_error})
            continue

        # Validate required fields
        per_commit = result.get("per_commit_feedback")
        overall    = result.get("overall_feedback")
        if not isinstance(per_commit, list) or not isinstance(overall, str):
            last_error = f"Attempt {attempt}: Missing or invalid keys in response.\nResponse keys: {list(result.keys())}"  # noqa: E501
            messages.append({"role": "user", "content": last_error})
            continue

        # Validate list length
        if len(per_commit) != len(entries):
            last_error = f"Attempt {attempt}: Expected {len(entries)} feedback entries, got {len(per_commit)}"
            messages.append({"role": "user", "content": last_error})
            continue

        # Passed all checks
        return result

    # If we reach here, all attempts failed
    raise RuntimeError(f"Failed to get valid LLM response after 3 attempts. Last error: {last_error}")


if __name__ == "__main__":
    test_api_call()
