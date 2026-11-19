import os

# Poll interval in minutes. Configurable (user asked for "every nth min").
POLL_MINUTES = int(os.environ.get("POLL_MINUTES", "2"))

# Topics. keywords are lowercase substrings matched against title+summary.
TOPICS = {
    "ai": {
        "label": "AI / local LLMs",
        "keywords": ["llm", "gpt", "claude", "gemini", "qwen", "llama", "mistral", "deepseek",
                     "openai", "anthropic", "gguf", "ollama", "vllm", "fine-tun", "diffusion",
                     "transformer", "language model", "ai model", "ai agent", "local ai",
                     "grok", "kimi", "glm-", "nvidia ai", "ai chip", "inference"],
    },
    "hw": {
        "label": "GPU / homelab hardware deals",
        "keywords": ["gpu", "rtx", "radeon", "graphics card", "nvme", "ssd", "ddr5", "ddr4",
                     "nas", "mini pc", "minipc", "ryzen", "intel arc", "motherboard", "psu",
                     "hard drive", "hdd", "server", "homelab", "proxmox", "10gbe", "switch",
                     "raspberry pi", "bambu", "3d printer"],
    },
    "sg": {
        "label": "Singapore",
        "keywords": ["singapore", "sg", "mrt", "hdb", "cpf", "mas ", "changi", "johor"],
    },
    "security": {
        "label": "Security / OSINT",
        "keywords": ["cve-", "vulnerability", "exploit", "ransomware", "malware", "phishing",
                     "breach", "osint", "backdoor", "zero-day", "0-day", "spyware", "apt",
                     "infostealer", "botnet", "ddos", "encryption", "security"],
    },
    "natdis": {
        "label": "Natural disasters",
        "keywords": ["earthquake", "tsunami", "typhoon", "cyclone", "hurricane", "volcano",
                     "eruption", "flood", "landslide", "wildfire", "storm surge", "drought",
                     "haze", "heatwave", "tornado", "avalanche", "monsoon"],
    },
    "manmade": {
        "label": "Man-made disasters",
        "keywords": ["explosion", "blast", "building collapse", "bridge collapse", "derail",
                     "oil spill", "chemical leak", "gas leak", "blackout", "power grid",
                     "refinery", "factory fire", "warehouse fire", "mine collapse", "sinks",
                     "train crash", "plane crash", "ship collision"],
    },
    "disruptions": {
        "label": "Major disruptions / hacks / airline faults",
        "keywords": ["outage", "major incident", "grounded", "grounding", "airline", "boeing",
                     "airbus", " faa", "easa", "air traffic", "airport", "runway",
                     "flight cancel", "flights cancel", "it failure", "cloud outage",
                     "aws down", "azure down", "cloudflare", "google cloud", "starlink",
                     "hacked", "cyber attack", "data breach", "service disruption",
                     "bank outage", "payment outage", "undersea cable"],
    },
    "space": {
        "label": "Space",
        "keywords": ["spacex", "starship", "rocket", "satellite", " iss", "moon", "mars",
                     "nasa", " esa ", "asteroid", "comet", "telescope", "artemis", "falcon",
                     "soyuz", "launch vehicle", "orbit", "astronaut", "space station",
                     "blue origin", "rocket lab", "super heavy"],
    },
}

# Regexes (case-insensitive) that mark a matched item as high-signal -> ping-worthy.
ALERT_PATTERNS = [
    r"tsunami",
    r"magnitude\s*[6-9]",
    r"\bm[6-9]\.\d",                      # quake magnitude shorthand
    r"red alert",                          # GDACS red
    r"zero[- ]day|0[- ]day",
    r"actively exploited",
    r"data breach|breach of .{0,20}(million|billion|records)",
    r"ransomware (attack|gang|outbreak)",
    r"\bgrounded\b|grounding of|global grounding",
    r"(worldwide|global|major|mass) (outage|disruption)|outage (hits|affect)",
    r"(aws|azure|cloudflare|google cloud|starlink) (is |experience|hit by|down|outage)",
    r"(gpt|claude|gemini|qwen|llama|deepseek|mistral|grok|kimi)[- ]?\d?[\w.]* (is )?(released|launches|launched|unveiled|now available|open[- ]source)",
    r"(5090|5080|5070 ti|4090|rx 9070).{0,60}(sale|off|deal|\$|s\$)",
    r"starship.{0,40}(launch|flight test|explod|booster catch)",
    r"(building|bridge|mine) collapse",
    r"(mrt|lrt).{0,40}(disrupt|delay|fault|breakdown|suspend)",
    r"flash flood.{0,40}singapore|singapore.{0,40}flash flood",
    r"emergency landing",
    r"plane crash|aircraft crash",
]

# Feeds. mode "all" = every item counts for the listed topics (the source IS the filter).
# mode "filtered" = item must also match the topic keywords.
# NOTE: Reddit is deliberately NOT here (user: browser automation only for Reddit,
# every 10 min, logged out). Reddit items arrive via POST /api/ingest.
FEEDS = [
    # AI / local LLM
    {"name": "HN front page",  "url": "https://news.ycombinator.com/rss",              "topics": ["ai", "hw", "disruptions", "space", "security"], "mode": "filtered"},
    {"name": "HN search: LLM", "url": "https://hnrss.org/newest?q=LLM&points=20",      "topics": ["ai"], "mode": "all"},
    {"name": "Lobsters",       "url": "https://lobste.rs/rss",                         "topics": ["ai", "security", "hw"], "mode": "filtered"},
    # GPU / homelab / deals
    {"name": "ServeTheHome",    "url": "https://www.servethehome.com/feed/",              "topics": ["hw"], "mode": "all"},
    {"name": "TechPowerUp",     "url": "https://www.techpowerup.com/rss/",                "topics": ["hw"], "mode": "all"},
    # Singapore
    {"name": "Straits Times SG", "url": "https://www.straitstimes.com/news/singapore/rss.xml", "topics": ["sg"], "mode": "all"},
    {"name": "CNA Singapore",    "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511636", "topics": ["sg"], "mode": "all"},
    {"name": "Mothership",       "url": "https://mothership.sg/feed/",                     "topics": ["sg"], "mode": "all"},
    # Security / OSINT
    {"name": "The Hacker News",  "url": "https://feeds.feedburner.com/TheHackersNews",  "topics": ["security", "disruptions"], "mode": "all"},
    {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/",       "topics": ["security"], "mode": "all"},
    {"name": "Schneier",         "url": "https://www.schneier.com/feed/",               "topics": ["security"], "mode": "all"},
    {"name": "Dark Reading",     "url": "https://www.darkreading.com/rss.xml",          "topics": ["security"], "mode": "all"},
    {"name": "The Register",     "url": "https://www.theregister.com/headlines.atom",   "topics": ["security", "disruptions", "ai", "space"], "mode": "filtered"},
    {"name": "Ars Technica",     "url": "https://arstechnica.com/feed/",                "topics": ["security", "ai", "space"], "mode": "filtered"},
    {"name": "IntelTechniques",  "url": "https://inteltechniques.com/blog/feed/",       "topics": ["security"], "mode": "all"},
    {"name": "NORAD",            "url": "https://www.norad.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1003&max=20", "topics": ["security", "space"], "mode": "all"},
    {"name": "GDELT blog",       "url": "https://blog.gdeltproject.org/feed/",          "topics": ["security"], "mode": "all"},
    {"name": "Bellingcat",       "url": "https://www.bellingcat.com/feed/",             "topics": ["security", "disruptions", "manmade", "natdis"], "mode": "filtered"},
    # Disasters / incidents
    {"name": "GDACS",             "url": "https://www.gdacs.org/xml/rss.xml",                 "topics": ["natdis"], "mode": "all"},
    {"name": "USGS significant",  "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.atom", "topics": ["natdis"], "mode": "all", "always_alert": True},
    {"name": "ReliefWeb",         "url": "https://reliefweb.int/disasters/rss.xml",           "topics": ["natdis"], "mode": "all"},
    # Space
    {"name": "NASA breaking", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss", "topics": ["space"], "mode": "all"},
    {"name": "SpaceNews",     "url": "https://spacenews.com/feed/",                    "topics": ["space"], "mode": "all"},
    {"name": "Space.com",     "url": "https://www.space.com/feeds/all",                "topics": ["space"], "mode": "all"},
]

# Cross-feed safety net: these topic keyword sets are also applied to every feed,
# so e.g. an earthquake headline on CNA gets tagged natdis even though CNA is "sg".
CROSS_MATCH_TOPICS = ["natdis", "manmade", "disruptions"]

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
