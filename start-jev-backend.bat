@echo off
rem Jev mode backend for Strata: llama-server from the parallel-decision branch (github.com/thecodacus/llama.cpp).
rem Strata's /v1/decision proxies here ("decision_url" in strata-<model>.json, port 8096). See docs\JEV_MODE.md.
"D:\AI\codacus_llamacpp\llama.cpp\build\bin\Release\llama-server.exe" ^
  -m "D:\AI\llama_cpp\llama-b9444-bin-win-cuda-12.4-x64\Qwen3.5-2B-Q6_K.gguf" ^
  --jinja -ngl 99 -fa on -c 16384 -np 1 --decision-seqs 24 --host 127.0.0.1 --port 8096
