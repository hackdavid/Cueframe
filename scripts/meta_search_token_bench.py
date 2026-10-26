import asyncio
import html
import math
import os
import re
import time
from collections import Counter

import httpx

H = {"Authorization": f"Bearer {os.environ['MODEL_API_KEY']}"}
B = "https://api.meta.ai/v1"
Q = "How do I export a vertical 9:16 video in Shotcut? Give 3 short steps and cite the official URL."


def summ(d):
    calls = [o for o in d.get("output", []) if o["type"] == "web_search_call"]
    txt = " ".join(
        c.get("text", "")
        for o in d.get("output", [])
        if o["type"] == "message"
        for c in o["content"]
    )
    u = d.get("usage", {})
    return calls, txt, u


async def resp(c, body):
    r = await c.post(B + "/responses", json=body, timeout=300)
    if r.status_code != 200:
        return {"error": r.text[:200]}
    return r.json()


async def variant(c, name, body):
    t = time.time()
    d = await resp(c, body)
    if d.get("error"):
        print(name, "ERR", d["error"])
        return
    calls, txt, u = summ(d)
    print(
        f"## {name}: in={u['input_tokens']} out={u['output_tokens']} (reason {u['output_tokens_details']['reasoning_tokens']}) searches={sum(1 for x in calls if x['action']['type'] == 'search')} pageops={sum(1 for x in calls if x['action']['type'] != 'search')} {time.time() - t:.0f}s\n   {txt[-260:]!r}",
        flush=True,
    )


def clean(h):
    h = re.sub(r"(?s)<(script|style|nav|header|footer)[^>]*>.*?</\1>", "", h)
    h = re.sub(r"<br\s*/?>|</(p|li|h\d|tr|div|pre)>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t))


def top_chunks(text, q, k=3, size=900):
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 30]
    chunks = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) > size:
            chunks.append(cur)
            cur = ""
        cur += p + "\n"
    chunks.append(cur)
    tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
    qt = set(tok(q)) - {
        "how",
        "do",
        "i",
        "a",
        "in",
        "the",
        "and",
        "give",
        "short",
        "steps",
        "cite",
        "official",
        "url",
    }
    N = len(chunks)
    df = Counter(w for ch in chunks for w in set(tok(ch)))
    sc = lambda ch: sum(
        math.log(1 + N / (1 + df[w])) * min(tf, 3) for w, tf in Counter(tok(ch)).items() if w in qt
    )
    return sorted(chunks, key=sc, reverse=True)[:k]


async def own_pipeline(c):
    t = time.time()
    # 1) find official URLs with one cheap search, no answer synthesis
    d = await resp(
        c,
        {
            "model": "muse-spark-1.3",
            "reasoning": {"effort": "low"},
            "tools": [{"type": "web_search", "search_context_size": "low"}],
            "max_tool_calls": 1,
            "instructions": "Do exactly one web search restricted to the official docs site. Reply ONLY with a JSON list of up to 3 official documentation URLs. No prose.",
            "input": "Official Shotcut documentation pages about video mode / vertical resolution and exporting.",
        },
    )
    if d.get("error"):
        print("own ERR", d["error"])
        return
    _, txt, u1 = summ(d)
    urls = re.findall(r"https?://[^\s\"'\]]+", txt)[:3]
    # 2) fetch + retrieve locally (0 model tokens)
    texts = []
    for u in urls:
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
    ex = "\n\n".join(f"[source {u}]\n" + "\n---\n".join(top_chunks(tx, Q)) for u, tx in texts)
    # 3) answer from excerpts, no web tool
    d2 = await resp(
        c,
        {
            "model": "muse-spark-1.3",
            "reasoning": {"effort": "low"},
            "instructions": "Answer only from the provided documentation excerpts; cite the source URL. If excerpts are insufficient, say so.",
            "input": f"Documentation excerpts:\n{ex}\n\nQuestion: {Q}",
        },
    )
    _, txt2, u2 = summ(d2)
    print(
        f"## own_pipeline: search step in={u1['input_tokens']} out={u1['output_tokens']} | answer step in={u2['input_tokens']} out={u2['output_tokens']} | excerpt chars={len(ex)} urls={urls} {time.time() - t:.0f}s\n   {txt2[-300:]!r}",
        flush=True,
    )
    open("excerpts.txt", "w").write(ex)


async def main():
    async with httpx.AsyncClient(headers=H) as c:
        base = {"model": "muse-spark-1.3", "input": Q}
        await asyncio.gather(
            variant(c, "A_default", {**base, "tools": [{"type": "web_search"}]}),
            variant(
                c,
                "B_low_ctx_low_effort",
                {
                    **base,
                    "reasoning": {"effort": "low"},
                    "tools": [{"type": "web_search", "search_context_size": "low"}],
                },
            ),
            variant(
                c,
                "C_B+max_tool_calls2+instr",
                {
                    **base,
                    "reasoning": {"effort": "low"},
                    "max_tool_calls": 2,
                    "instructions": "Use at most 2 searches. Prefer the official documentation domain. Do not open GitHub.",
                    "tools": [{"type": "web_search", "search_context_size": "low"}],
                },
            ),
            variant(
                c,
                "D_C_contributor",
                {
                    **base,
                    "model": "muse-spark-1.3-contributor",
                    "reasoning": {"effort": "low"},
                    "max_tool_calls": 2,
                    "instructions": "Use at most 2 searches. Prefer the official documentation domain.",
                    "tools": [{"type": "web_search", "search_context_size": "low"}],
                },
            ),
            own_pipeline(c),
        )


asyncio.run(main())
