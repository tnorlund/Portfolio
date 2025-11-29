# async_nested_demo.py ---------------------------------------------------------
#
# Demonstrates a parent async function that calls a child async function.
# All runs appear under a single LangSmith trace, even on Python <3.11.
#
# ---------------------------------------------------------------

import os
import asyncio
import langsmith as ls   # pip install -U langsmith

# -------------------------------------------------------------------------
# 1️⃣  Global LangSmith configuration (you can also set these in your env)
# -------------------------------------------------------------------------
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
os.environ["LANGCHAIN_TRACING_V2"] = "true"          # enable tracing
os.environ["LANGCHAIN_PROJECT"] = "async‑nested‑demo"   # optional project name
# ---------------------------------------------------------

# -------------------------------------------------------------------------
# 2️⃣  Child async function – a generator that yields two chunks
# -------------------------------------------------------------------------
@ls.traceable                     # the decorator creates a RunTree for this call
async def child_generator(name: str):
    """Yield a couple of messages – each yield becomes a “chunk” in the same run."""
    await asyncio.sleep(0.1)                # simulate I/O (e.g. an HTTP request)
    yield f"{name} – first chunk"
    await asyncio.sleep(0.1)
    yield f"{name} – second chunk"


# -------------------------------------------------------------------------
# 3️⃣  Parent async function – calls the child and **propagates** the run tree
# -------------------------------------------------------------------------
@ls.traceable
async def parent_task(task_id: int, run_tree: ls.RunTree):
    """
    `run_tree` is injected automatically by the decorator.
    We pass it to the child via `langsmith_extra={"parent": run_tree}` so the
    child run is attached to this parent.
    """
    async for chunk in child_generator(f"task‑{task_id}",
                                      langsmith_extra={"parent": run_tree}):
        # do something with the chunk – here we just print it
        print(">>>", chunk)


# -------------------------------------------------------------------------
# 4️⃣  Top‑level trace – the whole program lives under this run
# -------------------------------------------------------------------------
async def main():
    async with ls.trace("overall‑async‑flow", run_type="chain") as root:
        # Launch several parent tasks in parallel to demonstrate nesting + async
        await asyncio.gather(
            parent_task(1, run_tree=root),
            parent_task(2, run_tree=root),
        )
        # Optionally add metadata to the root run before it ends
        root.metadata["python_version"] = f"{os.sys.version_info.major}.{os.sys.version_info.minor}"
        root.end(outputs={"status": "finished"})


if __name__ == "__main__":
    asyncio.run(main())
