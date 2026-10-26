import asyncio
import importlib.util
import os
import time

import httpx

spec = importlib.util.spec_from_file_location("t7", "scripts/meta_search_token_bench.py")
src = open("scripts/meta_search_token_bench.py").read().split("async def main")[0]
ns = {}
exec(src, ns)
resp, summ, clean, top_chunks, Q = ns["resp"], ns["summ"], ns["clean"], ns["top_chunks"], ns["Q"]
H = {"Authorization": f"Bearer {os.environ['MODEL_API_KEY']}"}


async def main():
    async with httpx.AsyncClient(headers=H) as c:
        t = time.time()
        d = await resp(
            c,
            {
                "model": "muse-spark-1.3",
                "reasoning": {"effort": "low"},
                "max_tool_calls": 1,
                "include": ["web_search_call.results"],
                "tools": [{"type": "web_search", "search_context_size": "low"}],
                "instructions": "Run exactly one web search, then reply with the word OK.",
                "input": "site:shotcut.org vertical video mode export 9:16",
            },
        )
        calls, _, u1 = summ(d)
        res = [r for x in calls for r in (x.get("results") or [])]
        urls = [r["url"] for r in res if "url" in r]
        off = [u for u in urls if "shotcut.org" in u and "forum." not in u][:2] + [
            u for u in urls if "forum.shotcut.org/t/" in u
        ][:1]
        print("search results", len(res), "picked", off)
        texts = []
        for u in off:
            try:
                texts.append(
                    (
                        u,
                        clean(
                            (
                                await c.get(
                                    u,
                                    timeout=30,
                                    follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0"},
                                )
                            ).text
                        ),
                    )
                )
            except Exception as e:
                print("fetch fail", u, e)
        ex = "\n\n".join(
            f"[source {u}]\n" + "\n---\n".join(top_chunks(tx, Q, k=3)) for u, tx in texts
        )
        d2 = await resp(
            c,
            {
                "model": "muse-spark-1.3",
                "reasoning": {"effort": "low"},
                "instructions": "Answer only from the documentation excerpts; cite the source URL. If insufficient, say so.",
                "input": f"Documentation excerpts:\n{ex}\n\nQuestion: {Q}",
            },
        )
        _, txt, u2 = summ(d2)
        print(
            f"search step in={u1['input_tokens']} out={u1['output_tokens']} | answer in={u2['input_tokens']} out={u2['output_tokens']} | excerpt chars={len(ex)} | {time.time() - t:.0f}s\n{txt[-500:]}"
        )


asyncio.run(main())
