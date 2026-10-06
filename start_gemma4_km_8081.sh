#!/bin/bash
# start_gemma4_text_8081.sh
# Text-only mode — no --mmproj vision/audio encoder.
# Saves ~590MB VRAM, prevents OOM crash on Jetson Orin Nano.
# Use this for Neptune and Venus (text-only agents).
# Use start_gemma4_km_8081.sh (with --mmproj) only when vision is needed.

KM_SNAP=$(find ~/models/hf_cache -name "gemma-4-E2B-it-Q4_K_M.gguf" | head -1 | xargs dirname)
KM_DOCKER=/data/models/huggingface/$(realpath --relative-to=$HOME/models/hf_cache $KM_SNAP)

echo "Starting gemma4:e2b Q4_K_M TEXT-ONLY on port 8081 (CUDA)..."
echo "Model: $KM_DOCKER/gemma-4-E2B-it-Q4_K_M.gguf"
echo "Vision encoder: DISABLED (text-only mode)"

sudo docker run -it --rm --runtime=nvidia --network host \
  -v ~/models/hf_cache:/data/models/huggingface \
  ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin \
  llama-server \
  -m $KM_DOCKER/gemma-4-E2B-it-Q4_K_M.gguf \
  -c 65536 --parallel 1 --port 8081 --host 0.0.0.0 \
  -ngl 99 -fit off

# No --mmproj  → saves ~590MB VRAM
# No --webui-mcp-proxy → not needed for text agents
# -ngl 99     → all layers on GPU
# -fit off    → disable auto-fit (prevents GGML_ASSERT crash)
# Port 8081   → clear of SearXNG on 8080

# OpenAI-compatible endpoint will be at:
#   http://localhost:8081/v1/chat/completions
# Model name to use in Hermes: "local-model" (llama.cpp default)
