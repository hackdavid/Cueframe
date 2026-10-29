"""Sample videos on the public site: real Cueframe runs (newest first), in R2 under `samples/`."""

SAMPLES = [
    {
        "slug": "reel-google-trends",
        "title": "Reel: a 1M-search trend you can make content about today",
        "site": "trends.google.com · reel, 60 s",
        "request": "Give me a trending topic to make content about",
        "steps": [
            "Open Trending Now",
            "Filter past day",
            "Open top trend",
            "Compare smaller trends",
            "Open export menu",
            "Save this trend",
        ],
        "length": "0:48",
        "made_in": "6.3 min",
        "fixes": 1,
    },
    {
        "slug": "tutorial-text-classifier-paced",
        "title": "Tutorial: train and tune a text classifier, installs fast-forwarded",
        "site": "Linux terminal · tutorial, 90 s",
        "request": (
            "Make a video showing how to train a small text classifier in Python with scikit-learn"
        ),
        "steps": [
            "Install sklearn",
            "Write classifier script",
            "Show the code",
            "Train and predict",
            "Write tuning script",
            "Tune hyperparameters",
            "Show final results",
        ],
        "length": "1:26",
        "made_in": "11.0 min",
        "fixes": 2,
    },
    {
        "slug": "explainer-chronoreplay",
        "title": "Explainer: today's #1 AI paper on Hugging Face, in a minute",
        "site": "huggingface.co · explainer, 60 s",
        "request": "Make an explainer video about today's top trending AI paper",
        "steps": [
            "Top paper today",
            "One side not enough",
            "Open project page",
            "See Overview fix",
            "Time order matters",
            "All three rise",
            "Smarter frames win",
        ],
        "length": "0:59",
        "made_in": "15.4 min",
        "fixes": 7,
    },
    {
        "slug": "trending-repo-thinkrail",
        "title": "Influencer video: today's #1 trending GitHub repo",
        "site": "github.com · influencer mode",
        "request": "Make an influencer video about the top trending GitHub repo today",
        "steps": [
            "Open trending repo",
            "See what you get",
            "Check install section",
            "See under the hood",
            "Open official site",
        ],
        "length": "0:57",
        "made_in": "4.8 min",
        "fixes": 0,
    },
    {
        "slug": "trending-paper-trinity",
        "title": "Influencer video: today's #1 AI paper on Hugging Face",
        "site": "huggingface.co · influencer mode",
        "request": "Make an influencer video about today's top trending AI paper",
        "steps": [
            "Open top paper",
            "Read the abstract",
            "Open code repo",
            "Open project page",
            "See the results",
        ],
        "length": "0:48",
        "made_in": "6.5 min",
        "fixes": 1,
    },
    {
        "slug": "text-classifier-sklearn",
        "title": "Train a small text classifier with scikit-learn",
        "site": "Linux terminal · tutorial mode",
        "request": (
            "Make a tutorial: how to train a small text classifier in Python with scikit-learn"
            " in the terminal, then test it on a new sentence"
        ),
        "steps": [
            "Install scikit-learn",
            "Verify install",
            "Create training script",
            "Show the code",
            "Train and predict",
        ],
        "length": "1:00",
        "made_in": "5.7 min",
        "fixes": 0,
    },
    {
        "slug": "python-docs-asyncio",
        "title": "Search the Python docs for asyncio",
        "site": "docs.python.org",
        "request": (
            "Search the Python docs for asyncio using the search box and open the asyncio page"
        ),
        "steps": ["Open Python docs", "Type asyncio", "Run search", "Open asyncio page"],
        "length": "1:03",
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

# Math board lessons: drawn from a SymPy-checked board plan, not recorded. `fixes` counts
# plans sent back by the checks or the reviewer.
MATH_SAMPLES = [
    {
        "slug": "math-quadratic-formula",
        "title": "Solve x² − 5x + 6 = 0 with the quadratic formula, like a teacher",
        "site": "Math board · high school, 73 s",
        "request": "Teach me how to solve x^2 - 5x + 6 = 0 using the quadratic formula, and show "
        "the parabola crossing the x-axis at the answers.",
        "steps": [
            "Let's talk about the question",
            "What we're given",
            "How we'll think",
            "The formula, on the right",
            "The discriminant",
            "Into the formula",
            "Two paths: 3 and 2",
            "The parabola",
            "Common mistake",
            "Check and box the answer",
        ],  # fmt: skip
        "length": "1:13",
        "made_in": "2.9 min",
        "fixes": 1,
    },
    {
        "slug": "math-linear-equation",
        "title": "Solve 3x − 5 = 16, the way a teacher would",
        "site": "Math board · high school, 45 s",
        "request": "Teach me how to solve 3x - 5 = 16 step by step",
        "steps": [
            "What we know, what we want",
            "Plan: undo −5, then ×3",
            "Add 5 to both sides",
            "Divide both sides by 3",
            "See it on a graph",
            "Check x = 7",
        ],  # fmt: skip
        "length": "0:45",
        "made_in": "2.8 min",
        "fixes": 2,
    },
    {
        "slug": "math-fractions-child",
        "title": "3/4 + 1/8 with cake slices, for a primary school child",
        "site": "Math board · primary school, 63 s",
        "request": "For a primary school child: what is 3/4 + 1/8? Draw it.",
        "steps": [
            "The cake problem",
            "Cut quarters into eighths",
            "Three quarters coloured",
            "3/4 = 6/8",
            "Eight little slices",
            "6/8 + 1/8 = 7/8",
            "Common mistake, check",
        ],  # fmt: skip
        "length": "1:03",
        "made_in": "4.2 min",
        "fixes": 2,
    },
    {
        "slug": "math-derivative-tangent",
        "title": "Derivative of x² sin x with the product rule, plus its tangent",
        "site": "Math board · undergrad, 45 s",
        "request": "Explain step by step how to find the derivative of x^2 sin(x) and show the "
        "tangent at x = 1 on a graph",
        "steps": [
            "What we know, what we want",
            "Plan: the product rule",
            "Name the parts",
            "Apply the product rule",
            "Value and slope at 1",
            "Curve and tangent",
            "Traps and check",
        ],  # fmt: skip
        "length": "0:45",
        "made_in": "2.4 min",
        "fixes": 1,
    },
    {
        "slug": "math-integration-by-parts",
        "title": "Area under x eˣ from 0 to 2: integration by parts and a Riemann sum",
        "site": "Math board · undergrad, 60 s",
        "request": "Find the area under y = x e^x from x = 0 to x = 2. Use integration by "
        "parts, show the area on a graph with a Riemann-sum estimate, and check the answer.",
        "steps": [
            "What we want",
            "Plan: integration by parts",
            "The antiderivative",
            "Evaluate from 0 to 2",
            "Shaded area and rectangles",
            "Common trap",
            "Check by differentiating",
        ],  # fmt: skip
        "length": "1:00",
        "made_in": "1.9 min",
        "fixes": 0,
    },
    {
        "slug": "math-pythagoras-proof",
        "title": "Why a² + b² = c²: a geometric proof from a 3-4-5 triangle",
        "site": "Math board · high school, 80 s",
        "request": "Prove the Pythagorean theorem with a geometric derivation on a drawing, "
        "using a 3-4-5 right triangle as the example.",
        "steps": [
            "What we know",
            "Plan: one square, two ways",
            "Draw the 7 × 7 square",
            "Why the inner shape is a square",
            "Area from the side: 49",
            "Area from the pieces: 24 + 25",
            "Any right triangle",
            "Expand and cancel 2ab",
            "Check and common mistake",
        ],  # fmt: skip
        "length": "1:20",
        "made_in": "5.4 min",
        "fixes": 3,
    },
]

BY_SLUG = {s["slug"]: s for s in [*SAMPLES, *MATH_SAMPLES]}
for s in BY_SLUG.values():
    s["video"] = f"/samples/media/{s['slug']}.mp4"
    s["poster"] = f"/samples/media/{s['slug']}.jpg"
