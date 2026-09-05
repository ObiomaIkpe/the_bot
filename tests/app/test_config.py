"""
Tests for app.core.config.Settings -- specifically the exact failure
mode that took down BOTH api and shadow_runner in production
2026-09-05: Settings() rejects any env var it doesn't declare as a
field (pydantic-settings' `extra="forbid"` default), and
docker-compose.yml's `env_file: ./.env` loads .env WHOLESALE into every
service's real OS environment regardless of which service actually
uses a given var. HEALTHCHECKS_PING_URL_API/HEALTHCHECKS_PING_URL_SHADOW_RUNNER
were added to .env.example (for the same-day healthchecks.io setup)
without a matching Settings field.

TWO mechanism details, both confirmed by direct experiment while
writing this test -- either one alone made an earlier draft of this
test a false positive (it passed against the OLD, broken config.py,
which would have shipped this test without it ever actually guarding
anything):

1. The crash only happens when the extra variable is read from an
   actual `.env` FILE via pydantic-settings' `env_file` mechanism -- a
   dotenv-file source dumps every key it finds into the candidate input
   dict, all subject to `extra="forbid"`. Plain OS environment variables
   (e.g. `monkeypatch.setenv`, or what Docker's `environment:` block
   sets) behave differently: pydantic-settings' env-var source only
   extracts values for fields it already knows about and silently
   ignores anything else -- an unrecognized OS env var alone does NOT
   trigger extra_forbidden.

2. An EMPTY-valued dotenv entry (`KEY=`, nothing after the `=` --
   exactly what .env.example itself has for these two vars, as
   placeholders) does NOT reproduce the crash either, only a
   non-empty one does (matching what the real production .env actually
   had: real ping URLs, not blank placeholders). So this test builds
   its own temp .env with synthetic NON-EMPTY values for every
   .env.example-declared name, rather than pointing Settings at
   .env.example directly.
"""
import re
from pathlib import Path

from pydantic_settings import SettingsConfigDict

from app.core.config import Settings

_ENV_EXAMPLE = Path("/home/youngtee/the_bot/.env.example")


def _env_example_var_names() -> list[str]:
    names = []
    for line in _ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.append(match.group(1))
    return names


def test_env_example_vars_with_real_values_do_not_crash_settings(tmp_path):
    """The real repro: every .env.example-declared name, given a REAL
    (non-empty) value, in an actual dotenv file Settings loads via its
    `env_file` mechanism -- not env-var injection, and not
    .env.example's own (empty-valued) placeholders, neither of which
    actually exercises this failure mode (see module docstring)."""
    var_names = _env_example_var_names()
    assert len(var_names) > 5, "sanity check -- the parser above should find a real, non-trivial list"

    # jwt_access_token_expire_minutes is int-typed -- give it something
    # parseable so this test fails for the RIGHT reason if it ever does.
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "".join(
            f"{name}=60\n" if name == "JWT_ACCESS_TOKEN_EXPIRE_MINUTES" else f"{name}=non-empty-dummy-value\n"
            for name in var_names
        )
    )

    class SettingsUsingFakeEnv(Settings):
        model_config = SettingsConfigDict(env_file=str(fake_env), env_file_encoding="utf-8")

    # Must not raise pydantic's extra_forbidden ValidationError -- this
    # is the exact crash that took production down 2026-09-05.
    SettingsUsingFakeEnv()


def test_settings_still_rejects_a_genuinely_unknown_dotenv_entry(tmp_path):
    """The other half of the same contract, using the SAME real dotenv-
    file + non-empty-value mechanism as the test above -- confirms that
    test is exercising real validation, not silently no-op'ing (e.g. if
    BaseSettings' extra-handling default ever changed to "ignore", or if
    this test regressed back to a false positive the way an earlier
    draft did)."""
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "DATABASE_URL=postgresql://x:x@localhost/x\n"
        "JWT_SECRET_KEY=x\n"
        "CREDENTIALS_ENCRYPTION_KEY=x\n"
        "SOME_VAR_NOBODY_HAS_EVER_DECLARED_ANYWHERE=a-real-non-empty-value\n"
    )

    class SettingsUsingFakeEnv(Settings):
        model_config = SettingsConfigDict(env_file=str(fake_env), env_file_encoding="utf-8")

    try:
        SettingsUsingFakeEnv()
    except Exception:
        return
    raise AssertionError(
        "Settings() did not reject an unknown, non-empty .env entry -- "
        "either the extra_forbidden behavior changed, or this test "
        "stopped actually proving anything."
    )
