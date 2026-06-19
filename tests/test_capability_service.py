"""
Unit tests for app/services/capability_service.py.

Covers:
- NULL product_mode → FREE (fail-closed, P0 security requirement)
- Unknown product_mode → FREE
- All known product_modes map to the correct SKU
- has_capability returns correct bool for each SKU
- Organization SKU takes priority over user.product_mode
- capabilities_override adds/removes individual capabilities
- Invalid JSON in capabilities_override is silently ignored
"""

import json
import pytest
from unittest.mock import MagicMock

from services.capability_service import (
    _resolve_sku,
    get_capabilities,
    has_capability,
    effective_product_mode,
    SKU_CAPABILITIES,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_user(product_mode=None, org=None):
    """Create a mock User with the given product_mode and optional organization."""
    user = MagicMock()
    user.product_mode = product_mode
    user.organization = org
    return user


def make_org(sku=None, capabilities_override=None):
    """Create a mock Organization."""
    org = MagicMock()
    org.sku = sku
    org.capabilities_override = capabilities_override
    return org


# ─── _resolve_sku ─────────────────────────────────────────────────────────────


class TestResolveSku:
    def test_null_product_mode_returns_free(self):
        """Critical: NULL product_mode must not grant any access (fail-closed)."""
        user = make_user(product_mode=None)
        assert _resolve_sku(user) == "FREE"

    def test_unknown_product_mode_returns_free(self):
        """Unrecognised strings must default to FREE, not raise."""
        user = make_user(product_mode="unknown_sku")
        assert _resolve_sku(user) == "FREE"

    def test_empty_string_product_mode_returns_free(self):
        user = make_user(product_mode="")
        assert _resolve_sku(user) == "FREE"

    def test_full_mode_maps_to_full_sku(self):
        user = make_user(product_mode="full")
        assert _resolve_sku(user) == "FULL"

    def test_train_mode_maps_to_train_ru(self):
        user = make_user(product_mode="train")
        assert _resolve_sku(user) == "TRAIN_RU"

    def test_free_mode_maps_to_free_sku(self):
        user = make_user(product_mode="free")
        assert _resolve_sku(user) == "FREE"

    def test_org_sku_takes_priority_over_product_mode(self):
        """Organization SKU must override user.product_mode."""
        org = make_org(sku="TRAIN_GLOBAL", capabilities_override=None)
        user = make_user(product_mode="full", org=org)
        assert _resolve_sku(user) == "TRAIN_GLOBAL"

    def test_org_none_falls_back_to_product_mode(self):
        user = make_user(product_mode="full", org=None)
        assert _resolve_sku(user) == "FULL"

    def test_org_sku_none_falls_back_to_product_mode(self):
        org = make_org(sku=None, capabilities_override=None)
        user = make_user(product_mode="full", org=org)
        assert _resolve_sku(user) == "FULL"


# ─── has_capability ───────────────────────────────────────────────────────────


class TestHasCapability:
    # --- FREE / NULL users ---

    def test_null_user_no_call_analysis(self):
        """NULL product_mode must not grant call_analysis."""
        assert not has_capability(make_user(product_mode=None), "call_analysis")

    def test_null_user_no_voice_training(self):
        assert not has_capability(make_user(product_mode=None), "voice_training")

    def test_free_user_has_no_capabilities(self):
        user = make_user(product_mode="free")
        for cap in SKU_CAPABILITIES["FULL"]:
            assert not has_capability(user, cap), f"FREE user should not have '{cap}'"

    # --- FULL users ---

    def test_full_user_has_call_analysis(self):
        assert has_capability(make_user(product_mode="full"), "call_analysis")

    def test_full_user_has_crm(self):
        assert has_capability(make_user(product_mode="full"), "crm")

    def test_full_user_has_voice_training(self):
        assert has_capability(make_user(product_mode="full"), "voice_training")

    def test_full_user_all_capabilities(self):
        user = make_user(product_mode="full")
        for cap in SKU_CAPABILITIES["FULL"]:
            assert has_capability(user, cap), f"FULL user missing '{cap}'"

    # --- TRAIN_RU users ---

    def test_train_user_has_voice_training(self):
        assert has_capability(make_user(product_mode="train"), "voice_training")

    def test_train_user_has_training_catalog(self):
        assert has_capability(make_user(product_mode="train"), "training_catalog")

    def test_train_user_no_call_analysis(self):
        assert not has_capability(make_user(product_mode="train"), "call_analysis")

    def test_train_user_no_crm(self):
        assert not has_capability(make_user(product_mode="train"), "crm")

    def test_train_user_no_owner_dashboard(self):
        assert not has_capability(make_user(product_mode="train"), "owner_dashboard")

    # --- Unknown capability keys ---

    def test_unknown_capability_returns_false_for_full_user(self):
        assert not has_capability(make_user(product_mode="full"), "nonexistent_feature")

    def test_unknown_capability_returns_false_for_free_user(self):
        assert not has_capability(make_user(product_mode=None), "nonexistent_feature")


# ─── get_capabilities with override ──────────────────────────────────────────


class TestCapabilitiesOverride:
    def test_override_adds_capability_to_train_org(self):
        """An org can grant a capability not in the base SKU."""
        org = make_org(sku="TRAIN_RU", capabilities_override='{"call_analysis": true}')
        user = make_user(product_mode="train", org=org)
        assert "call_analysis" in get_capabilities(user)

    def test_override_removes_capability_from_full_org(self):
        """An org can restrict a capability from the base SKU."""
        org = make_org(sku="FULL", capabilities_override='{"call_analysis": false}')
        user = make_user(product_mode="full", org=org)
        assert "call_analysis" not in get_capabilities(user)

    def test_override_does_not_affect_other_caps(self):
        """Overriding one capability must not change others."""
        org = make_org(sku="FULL", capabilities_override='{"call_analysis": false}')
        user = make_user(product_mode="full", org=org)
        caps = get_capabilities(user)
        assert "crm" in caps
        assert "voice_training" in caps

    def test_invalid_json_override_is_ignored(self):
        """Malformed JSON must not crash — fall through to base SKU caps."""
        org = make_org(sku="TRAIN_RU", capabilities_override="not-json{{")
        user = make_user(product_mode="train", org=org)
        caps = get_capabilities(user)
        assert "voice_training" in caps
        assert "call_analysis" not in caps

    def test_empty_override_is_ignored(self):
        """Empty capabilities_override string must not crash."""
        org = make_org(sku="TRAIN_RU", capabilities_override="")
        user = make_user(product_mode="train", org=org)
        assert "voice_training" in get_capabilities(user)

    def test_none_override_is_ignored(self):
        org = make_org(sku="TRAIN_RU", capabilities_override=None)
        user = make_user(product_mode="train", org=org)
        assert "voice_training" in get_capabilities(user)


# ─── effective_product_mode ───────────────────────────────────────────────────


class TestEffectiveProductMode:
    def test_full_user_is_full_mode(self):
        assert effective_product_mode(make_user(product_mode="full")) == "full"

    def test_train_user_is_train_mode(self):
        assert effective_product_mode(make_user(product_mode="train")) == "train"

    def test_free_user_is_train_mode(self):
        # FREE has no call_analysis → falls to "train" branch
        assert effective_product_mode(make_user(product_mode="free")) == "train"

    def test_null_user_is_train_mode(self):
        assert effective_product_mode(make_user(product_mode=None)) == "train"
