"""Adapters talk to external systems: Ollama (HTTP), the GPU (NVML), the operating system (psutil).

Rules:
- adapters know nothing about modules, routes or API schemas; they return plain dataclasses
- every adapter has a fake twin that replays a recorded scenario from samples/<name>/
- modules get the configured set via ctx.adapters (see registry.py), never by building their own
"""
