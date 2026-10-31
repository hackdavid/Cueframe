"""Overlay components: registry validation, fact binding and resolution."""

import pytest

from app.tutorial import components
from app.tutorial.build import plan_overlay_step
from app.tutorial.contracts import Narration, OverlaySpec, Word
from app.tutorial.facts import Fact, Facts


def _facts() -> Facts:
    return Facts(subject="o/r", items={
        "github.stars": Fact(key="github.stars", label="GitHub stars", value="12,345",
                             number=12345),
        "github.commit_activity": Fact(key="github.commit_activity", label="weekly commits",
                                       value="640", number=640, values=[3.0, 5.0, 8.0]),
    })  # fmt: skip


def _ctx(brief: str = "") -> components.Context:
    return components.Context(facts=_facts(), brief=brief)


def _overlay(component: str, props: dict) -> OverlaySpec:
    return OverlaySpec(id="o1", component=component, props=props)


def test_registry_has_the_initial_components():
    assert set(components.REGISTRY) == {
        "stat_chip", "star_counter", "contrib_graph", "logo_wall", "claim_card"}


def test_prompt_doc_lists_every_component():
    doc = components.prompt_doc()
    for key in components.REGISTRY:
        assert f"- {key}: " in doc


def test_unknown_component_is_rejected():
    assert "unknown component" in components.validate_overlay(_overlay("pie_chart", {}), _ctx())[0]


def test_hallucinated_props_are_rejected():
    out = components.validate_overlay(_overlay("stat_chip", {"fact_key": "github.stars", "color": "red"}), _ctx())
    assert out and "stat_chip" in out[0]


def test_unknown_fact_key_is_rejected():
    out = components.validate_overlay(_overlay("stat_chip", {"fact_key": "github.unicorns"}), _ctx())
    assert out and "not in the FACTS" in out[0]


def test_star_counter_needs_a_number():
    out = components.validate_overlay(
        _overlay("star_counter", {"fact_key": "github.commit_activity"}), _ctx())
    assert not out  # the series fact carries a total number too
    out = components.validate_overlay(_overlay("star_counter", {}), _ctx())
    assert out == []


def test_contrib_graph_needs_a_series():
    out = components.validate_overlay(_overlay("contrib_graph", {"fact_key": "github.stars"}), _ctx())
    assert out and "no series" in out[0]


def test_logo_wall_validation():
    assert components.validate_overlay(
        _overlay("logo_wall", {"orgs": ["only-one"]}), _ctx("only-one and another-org use it"))
    assert components.validate_overlay(
        _overlay("logo_wall", {"orgs": ["bad login!", "ok-org"]}), _ctx(""))
    assert "not named in the source" in components.validate_overlay(
        _overlay("logo_wall", {"orgs": ["acme", "globex"]}), _ctx("acme uses it"))[0]
    assert components.validate_overlay(
        _overlay("logo_wall", {"orgs": ["acme", "globex"]}), _ctx("Acme and Globex use it")) == []


def test_claim_card_quote_must_be_in_the_source():
    brief = "The paper reports a 2x speedup over the baseline."
    assert components.validate_overlay(
        _overlay("claim_card", {"quote": "a 2x speedup over the baseline."}), _ctx(brief)) == []
    assert components.validate_overlay(
        _overlay("claim_card", {"quote": "a 10x speedup"}), _ctx(brief))


def test_resolve_inlines_the_fact():
    out = components.resolve("star_counter", {"fact_key": "github.stars", "sub": "and growing"}, _facts())
    assert out["value"] == "12,345" and out["number"] == 12345
    assert out["fact_label"] == "GitHub stars" and out["sub"] == "and growing"
    # the schema's default fact_key resolves too
    assert components.resolve("star_counter", {}, _facts())["number"] == 12345
    with pytest.raises(ValueError):
        components.resolve("stat_chip", {"fact_key": "github.unicorns"}, _facts())


def _narr() -> list[Narration]:
    return [Narration(text="Look at this.", audio_url="b/audio/x.mp3", duration=1.5,
                      words=[Word(text="Look", t=0, end=0.5), Word(text="at", t=0.5, end=0.8),
                             Word(text="this.", t=0.8, end=1.5)])]  # fmt: skip


def test_plan_overlay_step_builds_a_footage_free_state():
    step = {"id": "stars", "label": "Star count", "badge": None,
            "overlay": {"component": "star_counter", "props": {"fact_key": "github.stars"}}}
    st = plan_overlay_step(step, _narr(), _facts())
    assert st.source is None
    assert st.overlays[0].component == "star_counter"
    assert st.overlays[0].props["number"] == 12345
    assert st.duration > 1.5 + 0.8  # narration end plus padding
    assert st.sfx and st.sfx[0].kind == "pop"
