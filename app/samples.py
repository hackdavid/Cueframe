"""Sample tutorials shown on the public site: real Cueframe runs, stored in R2 under `samples/`."""

SAMPLES = [
    {
        "slug": "python-docs-asyncio",
        "title": "Search the Python docs for asyncio",
        "site": "docs.python.org",
        "request": (
            "Search the Python docs for asyncio using the search box and open the asyncio page"
        ),
        "steps": ["Open Python docs", "Type asyncio", "Run search", "Open asyncio page"],
        "length": "1:04",
        "made_in": "5.5 min",
        "fixes": 0,
    },
    {
        "slug": "wikipedia-alan-turing",
        "title": "Find Alan Turing's early life on Wikipedia",
        "site": "wikipedia.org",
        "request": (
            "On Wikipedia, search for Alan Turing and open the Early life section of his article"
        ),
        "steps": [
            "Open Wikipedia",
            "Open English edition",
            "Search Alan Turing",
            "Open Early life",
        ],
        "length": "0:58",
        "made_in": "4.5 min",
        "fixes": 0,
    },
    {
        "slug": "mdn-fetch-api",
        "title": "Find the Fetch API docs on MDN",
        "site": "developer.mozilla.org",
        "request": "On MDN Web Docs, search for fetch and open the Fetch API page",
        "steps": ["Open MDN Docs", "Find search box", "Search for fetch", "Open Fetch API"],
        "length": "0:50",
        "made_in": "7.3 min",
        "fixes": 2,
    },
    {
        "slug": "openstreetmap-eiffel-tower",
        "title": "Find the Eiffel Tower on OpenStreetMap",
        "site": "openstreetmap.org",
        "request": "On OpenStreetMap, search for the Eiffel Tower and show it on the map",
        "steps": [
            "Open OpenStreetMap",
            "Search Eiffel Tower",
            "Return to results",
            "Open Eiffel Tower result",
            "Zoom to tower",
        ],
        "length": "0:53",
        "made_in": "7.1 min",
        "fixes": 3,
    },
    {
        "slug": "excalidraw-rectangle",
        "title": "Draw a rectangle with Hello in Excalidraw",
        "site": "excalidraw.com",
        "request": "In Excalidraw, draw a rectangle on the canvas and write Hello inside it",
        "steps": [
            "Open Excalidraw",
            "Select rectangle tool",
            "Draw rectangle",
            "Select text tool",
            "Click inside rectangle",
            "Type Hello",
            "Finish editing",
        ],
        "length": "0:58",
        "made_in": "5.0 min",
        "fixes": 0,
    },
]

BY_SLUG = {s["slug"]: s for s in SAMPLES}
for s in SAMPLES:
    s["video"] = f"/samples/media/{s['slug']}.mp4"
    s["poster"] = f"/samples/media/{s['slug']}.jpg"
