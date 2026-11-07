"""Display names for source modules, shared by the terminal output and
the evidence descriptions."""
from __future__ import annotations

SOURCE_NAMES = {
    "cert_transparency": "Certificate transparency", "rdap": "RDAP",
    "abstractapi_phone": "AbstractAPI Phone", "anilist": "AniList", "bluesky": "Bluesky",
    "chess_com": "Chess.com", "codeforces": "Codeforces", "codewars": "Codewars",
    "crossref": "Crossref", "devto": "DEV", "discord": "Discord", "dockerhub": "Docker Hub",
    "dork_links": "Search dorks", "emailrep": "EmailRep", "github": "GitHub",
    "github_commit_email": "GitHub commit search", "github_name_search": "GitHub name search",
    "gitlab": "GitLab", "gravatar": "Gravatar", "hackernews": "Hacker News",
    "hibp": "Have I Been Pwned", "holehe": "holehe", "hunter_io": "Hunter.io",
    "instagram_graph": "Instagram (official API)",
    "keybase": "Keybase", "lichess": "Lichess", "mastodon_social": "Mastodon",
    "minecraft": "Minecraft", "openalex": "OpenAlex", "orcid": "ORCID",
    "pastebin_search": "Pastebin search", "phone_lookup": "Phone number parser",
    "roblox": "Roblox", "scratch": "Scratch", "search_api": "Web search",
    "speedrun_com": "Speedrun.com", "steam_api": "Steam", "telegram_api": "Telegram",
    "twitch_api": "Twitch", "twitter_api": "X (Twitter)", "twitter_email": "X (Twitter) email check",
    "wayback": "Wayback Machine", "wikipedia": "Wikipedia", "xposedornot": "XposedOrNot",
    "youtube_api": "YouTube",
}


def source_name(source: str) -> str:
    """'username_sites:GitHub' -> 'GitHub'; 'chess_com' -> 'Chess.com'."""
    if source.startswith("username_sites:"):
        return source.split(":", 1)[1]
    return SOURCE_NAMES.get(source, source)
