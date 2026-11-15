# DeepSeek Shell and the AI engine (Ollama)
Keywords: ai, deepseek, qwen, ollama, model, change model, switch model, other model, slow ai, ai not working, engine offline, gpu not used, out of memory, context, download model

- Engine status: `systemctl status ollama --no-pager`; start (admin): `systemctl restart ollama`
- Logs (admin): `journalctl -u ollama -b --no-pager | tail -n 60`
- Installed models: `ollama list`; loaded right now: `ollama ps` (shows CPU/GPU split).
- Download another: Settings > AI model, or `ollama pull MODEL`. Any model from ollama.com/library
  works, not only DeepSeek. Qwen: `qwen3.5:9b` (about 7 GB, 10–16 GB graphics card) or
  `qwen3.8:27b` (18 GB, 24 GB card). Small PCs: `qwen3.5:4b`.
- Switch models: the dropdown at the top of DeepSeek Shell, or Settings > AI model > Model in use.
  You can't switch your own model with a tool; tell the user where to click.
- Remove a model to free disk space: Settings > AI model > Delete model, or `ollama rm MODEL`.

## Slow answers
- `ollama ps` shows "100% CPU": the GPU is not used. NVIDIA: install the driver (NVIDIA guide).
  AMD: add ROCm (AMD guide). Intel graphics: AI runs on the CPU.
- Model too big for the graphics memory: use a smaller one (deepseek-r1:8b) or lower the memory
  (context) in Settings > AI model > Advanced.
- Thinking takes long: switch off "Think before answering" in Settings > AI model.

## Errors
- "out of memory": choose a smaller model or context size.
- Engine offline after an update: `systemctl restart ollama` (admin).
- Text mode when the desktop is broken: Ctrl+Alt+F3, log in, `deepseek-shell --cli`.
