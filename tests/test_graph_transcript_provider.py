"""
GraphTranscriptProvider parity test (Plan 3 §11 verification #6).

Mocks graph_get / graph_get_stream (and the calendar-derived discovery) to feed
the SAME Graph-shape-faithful fixtures MockTranscriptProvider uses, then asserts
GraphTranscriptProvider produces structurally identical Transcript objects — the
guarantee that flipping TRANSCRIPT_PROVIDER=graph changes nothing downstream.

No DB and no network: discovery is patched, so SessionLocal/CalendarEvent are
never touched. Heavy imports are done INSIDE the tests (not at module top level)
so merely collecting this module has no import side effects — importing the graph
provider pulls in utils.config, which would otherwise load .env and flip
LLM_PROVIDER for provider-sensitive sibling tests depending on collection order.
"""


class _FakeResp:
    def __init__(self, text):
        self.text = text


def _load():
    from services.graph.connectors import graph_transcript_provider as gtp_mod
    from services.graph.connectors.graph_transcript_provider import GraphTranscriptProvider
    from services.graph.connectors.transcript_connector import MockTranscriptProvider
    from services.graph.connectors.mock_transcript_fixtures import ALL_FIXTURES
    return gtp_mod, GraphTranscriptProvider, MockTranscriptProvider, ALL_FIXTURES


def _install_fakes(monkeypatch, gtp_mod, GraphTranscriptProvider, ALL_FIXTURES):
    by_join = {f["meeting_metadata"]["joinWebUrl"]: f for f in ALL_FIXTURES}
    by_meeting = {f["meeting_metadata"]["id"]: f for f in ALL_FIXTURES}

    def fake_graph_get(path, params=None):
        params = params or {}
        if path.endswith("/onlineMeetings"):
            filt = params.get("$filter", "")
            for url, f in by_join.items():
                if url in filt:
                    return {"value": [f["meeting_metadata"]]}
            return {"value": []}
        if path.endswith("/transcripts"):
            mid = path.split("/onlineMeetings/")[1].split("/transcripts")[0]
            f = by_meeting.get(mid)
            return {"value": [f["transcript_metadata"]] if f else []}
        raise AssertionError(f"unexpected graph_get path: {path}")

    def fake_graph_get_stream(path, params=None):
        mid = path.split("/onlineMeetings/")[1].split("/transcripts/")[0]
        f = by_meeting.get(mid)
        return _FakeResp(f["vtt_content"] if f else "")

    monkeypatch.setattr(gtp_mod, "graph_get", fake_graph_get)
    monkeypatch.setattr(gtp_mod, "graph_get_stream", fake_graph_get_stream)
    monkeypatch.setattr(GraphTranscriptProvider, "_candidate_join_urls", lambda self: list(by_join.keys()))


def test_graph_provider_matches_mock(monkeypatch):
    gtp_mod, GraphTranscriptProvider, MockTranscriptProvider, ALL_FIXTURES = _load()
    _install_fakes(monkeypatch, gtp_mod, GraphTranscriptProvider, ALL_FIXTURES)

    g_transcripts, _ = GraphTranscriptProvider(upn="svc@contoso.com").fetch_since(None)
    m_transcripts, _ = MockTranscriptProvider(upn="svc@contoso.com").fetch_since(None)

    assert len(g_transcripts) == len(m_transcripts) == len(ALL_FIXTURES)

    g_by = {t.metadata.subject: t for t in g_transcripts}
    m_by = {t.metadata.subject: t for t in m_transcripts}
    assert set(g_by) == set(m_by)

    for subj in g_by:
        gt, mt = g_by[subj], m_by[subj]
        assert [p.display_name for p in gt.participants] == [p.display_name for p in mt.participants]
        assert [u.text for u in gt.utterances] == [u.text for u in mt.utterances]
        assert (
            [(u.speaker.display_name if u.speaker else None) for u in gt.utterances]
            == [(u.speaker.display_name if u.speaker else None) for u in mt.utterances]
        )


def test_graph_provider_respects_since_cursor(monkeypatch):
    gtp_mod, GraphTranscriptProvider, MockTranscriptProvider, ALL_FIXTURES = _load()
    _install_fakes(monkeypatch, gtp_mod, GraphTranscriptProvider, ALL_FIXTURES)

    # A future cursor excludes every fixture (all createdDateTime are in the past).
    transcripts, cursor = GraphTranscriptProvider(upn="svc@contoso.com").fetch_since("2999-01-01T00:00:00Z")
    assert transcripts == []
    assert cursor == "2999-01-01T00:00:00Z"
