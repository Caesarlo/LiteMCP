"""Argon2id password storage contract (M2-AUTH-002).

New passwords are hashed with configurable Argon2id parameters, stored as
a self-describing PHC string, and verified through a single non-branching
failure path (docs/architecture/02-admin-auth.md §5.2):

- Hashing produces a ``$argon2id$...`` PHC string embedding ``m=``, ``t=``,
  and ``p=`` parameters, never the plaintext.
- Default configuration meets the baseline floor ``m>=19456, t>=2, p>=1``.
- Each call generates an independent random salt, so hashing the same
  password twice yields two different hashes, each independently
  verifiable.
- Cost parameters are genuinely configurable (not hardcoded): an explicit
  override is reflected in the produced PHC string.
- Verification succeeds for the correct password and fails uniformly --
  through the same outcome/exception -- for a wrong password and for a
  malformed/garbage hash string, so a caller cannot distinguish *why*
  verification failed from the exception type alone.

Out of scope for this slice (see M2-AUTH-003 / M2-AUTH-004): the
``needs_rehash``-on-login flow, bcrypt-to-Argon2id migration, the
pepper/Secret-Manager requirement, password length/blocklist rules, and
any HTTP/login/database wiring. This is pure, DB-free hashing logic.

A missing ``litemcp.auth.password`` module is the expected RED failure --
the hashing/verification functions do not exist yet.
"""

from __future__ import annotations

import re

import pytest

from litemcp.auth.password import (
    Argon2Parameters,
    PasswordVerificationError,
    hash_password,
    verify_password,
)

_PHC_PARAM_RE = re.compile(r"\$argon2id\$v=\d+\$m=(\d+),t=(\d+),p=(\d+)\$")

BASELINE_MEMORY_KIB = 19456
BASELINE_TIME_COST = 2
BASELINE_PARALLELISM = 1


def _parse_phc_params(phc_hash: str) -> tuple[int, int, int]:
    """Extract (m, t, p) from a ``$argon2id$...`` PHC string."""
    match = _PHC_PARAM_RE.search(phc_hash)
    assert match is not None, f"hash is not a well-formed argon2id PHC string: {phc_hash!r}"
    m, t, p = (int(group) for group in match.groups())
    return m, t, p


class TestHashFormat:
    def test_hash_is_argon2id_phc_string(self) -> None:
        result = hash_password("correct horse battery staple")
        assert result.startswith("$argon2id$")

    def test_hash_embeds_m_t_p_parameters(self) -> None:
        result = hash_password("correct horse battery staple")
        # will raise via assert inside helper if params are absent/malformed
        _parse_phc_params(result)

    def test_hash_is_not_plaintext(self) -> None:
        password = "correct horse battery staple"
        result = hash_password(password)
        assert password not in result


class TestBaselineParameterFloor:
    def test_default_memory_cost_meets_floor(self) -> None:
        result = hash_password("correct horse battery staple")
        m, _t, _p = _parse_phc_params(result)
        assert m >= BASELINE_MEMORY_KIB

    def test_default_time_cost_meets_floor(self) -> None:
        result = hash_password("correct horse battery staple")
        _m, t, _p = _parse_phc_params(result)
        assert t >= BASELINE_TIME_COST

    def test_default_parallelism_meets_floor(self) -> None:
        result = hash_password("correct horse battery staple")
        _m, _t, p = _parse_phc_params(result)
        assert p >= BASELINE_PARALLELISM


class TestIndependentRandomSalt:
    def test_same_password_hashed_twice_yields_different_hashes(self) -> None:
        password = "correct horse battery staple"
        first = hash_password(password)
        second = hash_password(password)
        assert first != second

    def test_both_independently_verify(self) -> None:
        password = "correct horse battery staple"
        first = hash_password(password)
        second = hash_password(password)
        assert verify_password(first, password) is True
        assert verify_password(second, password) is True


class TestConfigurableParameters:
    def test_explicit_time_cost_override_is_reflected_in_hash(self) -> None:
        overridden_time_cost = BASELINE_TIME_COST + 3
        params = Argon2Parameters(
            memory_cost_kib=BASELINE_MEMORY_KIB,
            time_cost=overridden_time_cost,
            parallelism=BASELINE_PARALLELISM,
        )
        result = hash_password("correct horse battery staple", parameters=params)
        _m, t, _p = _parse_phc_params(result)
        assert t == overridden_time_cost

    def test_explicit_memory_cost_override_is_reflected_in_hash(self) -> None:
        overridden_memory_kib = BASELINE_MEMORY_KIB + 12288
        params = Argon2Parameters(
            memory_cost_kib=overridden_memory_kib,
            time_cost=BASELINE_TIME_COST,
            parallelism=BASELINE_PARALLELISM,
        )
        result = hash_password("correct horse battery staple", parameters=params)
        m, _t, _p = _parse_phc_params(result)
        assert m == overridden_memory_kib

    def test_override_differs_from_default(self) -> None:
        default_result = hash_password("correct horse battery staple")
        overridden_result = hash_password(
            "correct horse battery staple",
            parameters=Argon2Parameters(
                memory_cost_kib=BASELINE_MEMORY_KIB,
                time_cost=BASELINE_TIME_COST + 5,
                parallelism=BASELINE_PARALLELISM,
            ),
        )
        _m1, t1, _p1 = _parse_phc_params(default_result)
        _m2, t2, _p2 = _parse_phc_params(overridden_result)
        assert t1 != t2


class TestVerificationCorrectness:
    def test_correct_password_verifies_true(self) -> None:
        password = "correct horse battery staple"
        stored = hash_password(password)
        assert verify_password(stored, password) is True

    def test_wrong_password_fails_verification(self) -> None:
        stored = hash_password("correct horse battery staple")
        with pytest.raises(PasswordVerificationError):
            verify_password(stored, "wrong password entirely")

    def test_malformed_hash_fails_through_same_exception_type(self) -> None:
        garbage_hash = "not-a-real-argon2-hash-at-all"
        with pytest.raises(PasswordVerificationError):
            verify_password(garbage_hash, "any password")

    def test_wrong_algorithm_hash_fails_through_same_exception_type(self) -> None:
        # A well-formed PHC string, but not argon2id -- e.g. a bcrypt-shaped
        # hash -- must fail through the identical PasswordVerificationError,
        # not leak a different, more revealing exception type.
        bcrypt_shaped_hash = "$2b$12$KIXQ2Z8N9y8yZ0y8yZ0y8u8yZ0y8yZ0y8yZ0y8yZ0y8yZ0y8yZ0y"
        with pytest.raises(PasswordVerificationError):
            verify_password(bcrypt_shaped_hash, "any password")

    def test_malformed_hash_and_wrong_password_raise_identical_exception_type(
        self,
    ) -> None:
        stored = hash_password("correct horse battery staple")

        mismatch_exc_type = None
        try:
            verify_password(stored, "wrong password entirely")
        except PasswordVerificationError as exc:
            mismatch_exc_type = type(exc)

        malformed_exc_type = None
        try:
            verify_password("garbage-not-a-hash", "correct horse battery staple")
        except PasswordVerificationError as exc:
            malformed_exc_type = type(exc)

        assert mismatch_exc_type is not None
        assert malformed_exc_type is not None
        assert mismatch_exc_type is malformed_exc_type

    def test_empty_hash_string_fails_through_same_exception_type(self) -> None:
        with pytest.raises(PasswordVerificationError):
            verify_password("", "any password")
