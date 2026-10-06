#!/usr/bin/env python3
"""
hermes_profile.py — Read a Hermes Agent profile's model, provider and API key.

Why this exists: form_agent (and Orbit's second-pass review) need to send IMAGES,
which `hermes -z "<text prompt>"` can't do. So they call OpenRouter directly — but
they still take the model from the Hermes profile, so `hermes model` (run for that
profile) stays the one place you change it:

    form_agent model            # or:  hermes -p form_agent model

Layout (per the Hermes docs):
    ~/.hermes/config.yaml, .env, SOUL.md                     <- "default" profile
    ~/.hermes/profiles/<name>/config.yaml, .env, SOUL.md     <- named profiles

config.yaml stores the model as:
    model:
      provider: openrouter
      default: qwen/qwen-image-3-pro
(an older config may hold a bare string:  model: some/model)

Resolution order for the model:  env <PROFILE>_MODEL  ->  profile config.yaml
For the API key:  env OPENROUTER_API_KEY  ->  profile .env  ->  ~/.hermes/.env
The key is never printed or logged (only its last 4 characters in --show-config).
"""

import os
import re

try:
    import yaml           # PyYAML — optional
except Exception:         # pragma: no cover
    yaml = None


def hermes_home():
    return os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")


def profile_dir(profile):
    if profile in (None, "", "default"):
        return hermes_home()
    return os.path.join(hermes_home(), "profiles", profile)


def read_env_file(path):
    """Parse KEY=VALUE lines (quotes stripped, comments ignored)."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.lower().startswith("export "):
                k = k[7:].strip()
            out[k] = v.strip().strip('"').strip("'")
    return out


def _model_from_yaml_text(text):
    """Return (model, provider, base_url) from config.yaml text."""
    if yaml is not None:
        try:
            cfg = yaml.safe_load(text) or {}
            m = cfg.get("model")
            if isinstance(m, dict):
                return (m.get("default") or m.get("model") or m.get("name"),
                        m.get("provider"), m.get("base_url") or None)
            if isinstance(m, str):
                return m, None, None
            return None, None, None
        except Exception:
            pass
    # Regex fallback: handles both `model: x` and a nested `model:` block.
    m = re.search(r"(?m)^model:\s*(\S[^#\n]*)$", text)
    if m:
        return m.group(1).strip().strip('"').strip("'"), None, None
    block = re.search(r"(?ms)^model:\s*\n((?:[ \t]+.*\n?)+)", text)
    if block:
        b = block.group(1)
        def grab(key):
            g = re.search(r"(?m)^[ \t]+{}:\s*(\S[^#\n]*)$".format(key), b)
            return g.group(1).strip().strip('"').strip("'") if g else None
        return grab("default") or grab("model") or grab("name"), grab("provider"), grab("base_url")
    return None, None, None


def profile_settings(profile):
    """
    Returns dict: model, provider, base_url, api_key, model_source, key_source, dir.
    Any value can be None if it could not be found.
    """
    pdir = profile_dir(profile)
    out = {"profile": profile, "dir": pdir, "model": None, "provider": None,
           "base_url": None, "api_key": None, "model_source": None, "key_source": None}

    env_model = os.environ.get("{}_MODEL".format(str(profile).upper().replace("-", "_")))
    if env_model:
        out["model"], out["model_source"] = env_model, "env {}_MODEL".format(str(profile).upper())
    cfg_path = os.path.join(pdir, "config.yaml")
    if os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            model, provider, base = _model_from_yaml_text(f.read())
        out["provider"], out["base_url"] = provider, base
        if not out["model"] and model:
            out["model"], out["model_source"] = model, cfg_path

    if os.environ.get("OPENROUTER_API_KEY"):
        out["api_key"], out["key_source"] = os.environ["OPENROUTER_API_KEY"], "env OPENROUTER_API_KEY"
    else:
        for path in (os.path.join(pdir, ".env"), os.path.join(hermes_home(), ".env")):
            k = read_env_file(path).get("OPENROUTER_API_KEY")
            if k:
                out["api_key"], out["key_source"] = k, path
                break
    return out


def soul_text(profile):
    """The profile's SOUL.md (empty string if it doesn't exist)."""
    path = os.path.join(profile_dir(profile), "SOUL.md")
    if os.path.isfile(path):
        with open(path) as f:
            return f.read().strip()
    return ""


def describe(profile):
    """Human-readable, key-masked summary."""
    s = profile_settings(profile)
    key = s["api_key"]
    masked = ("…" + key[-4:]) if key else "NOT FOUND"
    return ("profile: {p}\n  dir:      {d}\n  model:    {m}   (from {ms})\n"
            "  provider: {pr}\n  api key:  {k}   (from {ks})").format(
        p=profile, d=s["dir"], m=s["model"] or "NOT FOUND", ms=s["model_source"] or "-",
        pr=s["provider"] or "unknown", k=masked, ks=s["key_source"] or "-")


if __name__ == "__main__":
    import sys
    print(describe(sys.argv[1] if len(sys.argv) > 1 else "form_agent"))
