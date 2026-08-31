from types import SimpleNamespace

from app.services.asset_display_identity import (
    display_asset_text,
    display_market_label,
    learn_candidate_identity,
    learn_verified_identity,
    resolve_asset_identity,
)


def test_verified_pair_identity_renders_name_ticker_and_pair(tmp_path):
    path = tmp_path / "asset_identity.json"
    assert learn_verified_identity(
        base_asset="VVV",
        pair="VVVUSD",
        display_name="Venice Token",
        source="COINGECKO",
        source_id="venice-token-vvv",
        path=path,
    )

    identity = resolve_asset_identity(
        base_asset="VVV",
        pair="VVVUSD",
        path=path,
    )
    assert identity.display_name == "Venice Token"
    assert identity.asset_text == "Venice Token (VVV)"
    assert identity.label == "Venice Token (VVV) — VVVUSD"
    assert display_asset_text("VVVUSD", base_asset="VVV", pair="VVVUSD", path=path) == "Venice Token (VVV)"
    assert display_market_label("VVVUSD", base_asset="VVV", pair="VVVUSD", path=path) == "Venice Token (VVV) — VVVUSD"


def test_unknown_asset_never_guesses_name(tmp_path):
    path = tmp_path / "asset_identity.json"
    assert display_market_label("FOOUSD", path=path) == "FOOUSD"
    assert display_asset_text("FOO", path=path) == "FOO"


def test_same_ticker_conflict_makes_base_lookup_ambiguous_but_preserves_pair_identity(tmp_path):
    path = tmp_path / "asset_identity.json"
    assert learn_verified_identity(
        base_asset="ABC",
        pair="ABCUSD",
        display_name="Alpha Beta Coin",
        source="COINGECKO",
        source_id="alpha-beta-coin",
        path=path,
    )
    assert learn_verified_identity(
        base_asset="ABC",
        pair="ABCUSDT",
        display_name="Another Blockchain Coin",
        source="COINGECKO",
        source_id="another-blockchain-coin",
        path=path,
    )

    assert display_market_label("ABCUSD", path=path) == "Alpha Beta Coin (ABC) — ABCUSD"
    assert display_market_label("ABCUSDT", path=path) == "Another Blockchain Coin (ABC) — ABCUSDT"
    # A bare ticker is unsafe once pair-specific external identities conflict.
    assert display_asset_text("ABC", path=path) == "ABC"


def test_same_pair_cannot_be_silently_reassigned_to_different_external_identity(tmp_path):
    path = tmp_path / "asset_identity.json"
    assert learn_verified_identity(
        base_asset="XYZ",
        pair="XYZUSD",
        display_name="Original XYZ",
        source="COINGECKO",
        source_id="original-xyz",
        path=path,
    )
    assert not learn_verified_identity(
        base_asset="XYZ",
        pair="XYZUSD",
        display_name="Wrong XYZ",
        source="COINGECKO",
        source_id="wrong-xyz",
        path=path,
    )
    assert display_market_label("XYZUSD", path=path) == "Original XYZ (XYZ) — XYZUSD"


def test_corrupt_registry_fails_safe_to_ticker_pair(tmp_path):
    path = tmp_path / "asset_identity.json"
    path.write_text("{not-json", encoding="utf-8")

    # load_json quarantines malformed state; alert rendering must still succeed.
    assert display_market_label("BTCUSD", path=path) == "BTCUSD"
    quarantined = list(tmp_path.glob("asset_identity.json.corrupt-*"))
    assert quarantined


def test_candidate_identity_learns_only_identity_safe_reference(tmp_path):
    path = tmp_path / "asset_identity.json"
    safe = SimpleNamespace(
        underlying_asset="SOL",
        primary_pair="SOLUSD",
        symbol="SOLUSD",
        independent_market_reference=SimpleNamespace(
            mapping_status="UNIQUE",
            coingecko_name="Solana",
            coingecko_id="solana",
        ),
    )
    assert learn_candidate_identity(safe, path=path)
    assert display_market_label("SOLUSD", path=path) == "Solana (SOL) — SOLUSD"

    unsafe = SimpleNamespace(
        underlying_asset="DUP",
        primary_pair="DUPUSD",
        symbol="DUPUSD",
        independent_market_reference=SimpleNamespace(
            mapping_status="AMBIGUOUS",
            coingecko_name="Do Not Trust",
            coingecko_id="ambiguous",
        ),
    )
    assert not learn_candidate_identity(unsafe, path=path)
    assert display_market_label("DUPUSD", path=path) == "DUPUSD"
