# Code Style Rules

- Format with `black` (default config)
- Lint with `pylint --errors-only golem/`
- No f-strings in logging: use `logger.info("msg %s", val)` not `logger.info(f"msg {val}")`
- Dataclasses: use `field(default_factory=list)` for mutable defaults, never `field(default=[])`
- TypedDict contracts in `golem/types.py` - import from there, don't redefine
- No empty exception handlers (`except: pass`) - always log or handle
- Prefer `pathlib.Path` over `os.path`
