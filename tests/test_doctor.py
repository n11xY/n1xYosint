from osintrecon.core.doctor import _extract_domain


def test_extract_domain_from_template_url():
    assert _extract_domain("https://github.com/{}") == "github.com"


def test_extract_domain_with_subdomain_template():
    assert _extract_domain("https://{}.bandcamp.com") == "x.bandcamp.com"


def test_extract_domain_no_placeholder():
    assert _extract_domain("https://api.mojang.com/users/profiles/minecraft/{}") == "api.mojang.com"
