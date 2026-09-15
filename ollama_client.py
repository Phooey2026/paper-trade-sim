#!/usr/bin/env python3
"""
ollama_client.py — Direct Ollama API Client
Replaces Hermes -z subprocess calls for Mac Mini deployment.
Loads SOUL.md from the appropriate profile directory as system prompt.

Usage (from other scripts):
    from ollama_client import call_llm
    response = call_llm("neptune", "Your prompt here")
    response = call_llm("venus", "Your prompt here")
    response = call_llm("orbit", "Your prompt here")
    response = call_llm("fa", "Your prompt here")

Standalone test:
    python3 ollama_client.py --profile neptune --prompt "Reply with exactly: NEPTUNE_ONLINE"
"""

import json
import os
import urllib.request
import urllib.error
import argparse
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma4:12b-it-qat"
DEFAULT_CTX   = 65536
DEFAULT_TIMEOUT = 420  # seconds — generous for longer prompts on 12b model

# Soul files live in ~/paper_trade/agents/<profile>/SOUL.md
# (Previously ~/.hermes/profiles/ — Hermes not installed on Mac)
HERMES_HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")

# Fallback system prompts if SOUL.md not found
FALLBACK_SOULS = {
    "neptune": (
        "You are Neptune, the paper trading risk manager for Obsidian Capital "
        "(account OC-CLIENT-001). Evaluate trade orders and respond ONLY in this format:\n"
        "DECISION: APPROVE or REJECT\n"
        "RATIONALE: [2-4 sentences]\n"
        "CONFIDENCE: HIGH, MEDIUM, or LOW"
    ),
    "venus": (
        "You are Venus, an AI sales assistant for Obsidian Capital. "
        "Draft short personalized outreach emails to financial advisors. "
        "Goal: get a face-to-face meeting for Jay. "
        "Style: casual but professional, 4-6 sentences max. "
        "Sign off as: — Venus | Obsidian Capital"
    ),
    "orbit": (
        "You are Orbit, client services representative at Obsidian Capital. "
        "You are warm, professional, and knowledgeable. "
        "POLICY: Only disclose top 5 holdings. Address/beneficiary changes require written authorization. "
        "Never disclose internal rebalancing. "
        "Sign as: — Orbit | Obsidian Capital Client Services"
    ),
    "fa": (
        "You follow the instructions given in each prompt exactly. "
        "When playing a financial advisor or their assistant, be realistic: "
        "busy advisors receive many pitches and typically decline most. "
        "Only agree to meet if the pitch is genuinely personal, relevant, and compelling. "
        "A cold generic pitch should get a polite no."
    ),
}


def load_soul(profile):
    """Load SOUL.md for the given profile, fall back to defaults."""
    soul_path = os.path.join(HERMES_HOME, profile, "SOUL.md")
    if os.path.exists(soul_path):
        with open(soul_path) as f:
            return f.read().strip()
    return FALLBACK_SOULS.get(profile, "You are a helpful assistant.")


def call_llm(profile, prompt, model=None, verbose=False, timeout=DEFAULT_TIMEOUT):
    """
    Call Ollama directly with the given profile's system prompt.
    
    Args:
        profile: one of 'neptune', 'venus', 'orbit', 'fa'
        prompt:  the user message
        model:   override model (default: gemma4:12b-it-qat)
        verbose: print timing info
        timeout: seconds before giving up
    
    Returns:
        str: the model's response text
    """
    model     = model or DEFAULT_MODEL
    system    = load_soul(profile)
    start     = datetime.now()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "options": {
            "num_ctx":     DEFAULT_CTX,
            "num_predict": 2048,
            "temperature": 0.7,
        }
    }

    if verbose:
        print(f"  [Ollama] {profile} → {model} (ctx {DEFAULT_CTX})")

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())

        response = result.get("message", {}).get("content", "").strip()
        elapsed  = (datetime.now() - start).total_seconds()

        if verbose:
            tokens = result.get("eval_count", 0)
            speed  = tokens / elapsed if elapsed > 0 else 0
            print(f"  [Ollama] {elapsed:.1f}s | {tokens} tokens | {speed:.1f} tok/s")

        return response

    except urllib.error.URLError as e:
        print(f"  ❌ Ollama connection error: {e}")
        print(f"     Is Ollama running? Try: ollama serve")
        return ""
    except TimeoutError:
        print(f"  ❌ Ollama timeout after {timeout}s")
        return ""
    except Exception as e:
        print(f"  ❌ Ollama error: {e}")
        return ""


def call_llm_hermes(profile, prompt, verbose=False):
    """
    Try Hermes -z first, fall back to direct Ollama if it fails.
    Allows the same code to work on both Jetson (Hermes) and Mac (Ollama direct).
    """
    import subprocess

    cmd = [profile, "-z", prompt, "--ignore-rules"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        # Hermes failed — fall back to direct Ollama
        if verbose:
            print(f"  ⚠️  Hermes -z failed, falling back to direct Ollama")
        return call_llm(profile, prompt, verbose=verbose)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return call_llm(profile, prompt, verbose=verbose)


def main():
    parser = argparse.ArgumentParser(description="Ollama LLM Client")
    parser.add_argument("--profile", default="neptune",
                        choices=["neptune","venus","orbit","fa"],
                        help="Agent profile to use")
    parser.add_argument("--prompt", required=True, help="Prompt to send")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--hermes-fallback", action="store_true",
                        help="Try Hermes -z first, fall back to direct Ollama")
    args = parser.parse_args()

    if args.hermes_fallback:
        response = call_llm_hermes(args.profile, args.prompt, verbose=args.verbose)
    else:
        response = call_llm(args.profile, args.prompt,
                           model=args.model, verbose=args.verbose)

    print(response)


if __name__ == "__main__":
    main()
