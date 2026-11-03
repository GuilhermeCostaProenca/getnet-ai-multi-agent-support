"""OpenAIClient response parsing, with the SDK call stubbed (no network)."""

from types import SimpleNamespace as NS

from app.config import Settings
from app.llm import OpenAIClient


def client_returning(response) -> OpenAIClient:
    client = OpenAIClient(Settings(_env_file=None, openai_api_key="test"))
    client._client = NS(responses=NS(create=lambda **kwargs: response))
    return client


def test_web_search_prefers_cited_urls():
    msg = NS(type="message", content=[NS(annotations=[NS(type="url_citation", url="https://bcb.gov.br", title="BCB")])])
    call = NS(type="web_search_call", action=NS(sources=[NS(type="url", url="https://other.com")]))
    _, sources = client_returning(NS(output=[call, msg], output_text="rate")).web_search("i", "q")
    assert [s.url for s in sources] == ["https://bcb.gov.br"]


def test_ac27_web_search_falls_back_to_consulted_sources_including_data_apis():
    call = NS(type="web_search_call", action=NS(sources=[NS(type="api", name="oai-weather"),
                                                         NS(type="url", url="https://climatempo.com.br")]))
    msg = NS(type="message", content=[NS(annotations=[])])
    _, sources = client_returning(NS(output=[call, msg], output_text="sunny")).web_search("i", "q")
    assert [s.url for s in sources] == ["api:oai-weather", "https://climatempo.com.br"]
