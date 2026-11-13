import asyncio
from types import SimpleNamespace

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.youtube_api import YouTubeAPIPlugin, _youtube_image


class _Instances:
    """Piped says "not found" (as it does for some real channels); Invidious
    resolves the handle and returns the channel."""

    def __init__(self):
        self.urls = []

    async def get(self, source, url, **kwargs):
        self.urls.append(url)
        if "piped" in url:
            return SimpleNamespace(status=500, text='{"error":"ContentNotAvailableException: Not found (\\"404 null\\")"}',
                                   error=None, json=lambda: None)
        if "resolveurl" in url:
            return SimpleNamespace(status=200, text="", error=None, json=lambda: {"ucid": "UCabc"})
        return SimpleNamespace(status=200, text="", error=None, json=lambda: {
            "author": "Some Channel", "authorId": "UCabc", "subCount": 1234,
            "authorThumbnails": [{"url": "https://inv.example/ggpht/AVATAR=s48-c", "width": 48},
                                 {"url": "https://inv.example/ggpht/AVATAR=s900-c", "width": 900}],
            "authorBanners": [{"url": "https://inv.example/ggpht/BANNER=w2560-fcrop64", "width": 2560}]})


def test_without_a_key_the_channel_comes_from_an_instance_with_youtube_image_links():
    http = _Instances()
    plugin = YouTubeAPIPlugin(config={"piped_instances": ["https://piped.example"],
                                      "invidious_instances": ["https://inv.example"]}, http=http)
    [finding] = asyncio.run(plugin.run(Identifier(value="somechannel", type=IdentifierType.USERNAME)))
    assert finding.status is MatchStatus.CONFIRMED
    assert finding.source_url == "https://www.youtube.com/@somechannel"
    assert finding.metadata["avatar_url"] == "https://yt3.ggpht.com/AVATAR=s240-c"
    assert finding.metadata["banner_url"] == "https://yt3.ggpht.com/BANNER=w1060-fcrop64"
    assert finding.metadata["subscriber_count"] == 1234
    assert any("piped.example" in u for u in http.urls)  # asked first, its "not found" not taken as final


def test_youtube_image_links_never_point_elsewhere():
    assert _youtube_image("https://proxy.example/X=s160-c?host=yt3.googleusercontent.com", "s240") == \
        "https://yt3.googleusercontent.com/X=s240-c"
    assert _youtube_image("https://proxy.example/X=s160?host=evil.example") is None
    assert _youtube_image("https://evil.example/pic.jpg") is None
    assert _youtube_image(None) is None
