"""Numeric settings survive the quoting a dashboard or a TOML habit adds."""
import pytest

from aria_edge import config as edge_config
from orbital_cloud.config import env_float, env_int


@pytest.mark.parametrize("raw", ['10000', '"10000"', "'10000'", ' "10000" ', "10000\n"])
def test_quoted_numbers_are_read(monkeypatch, raw):
    monkeypatch.setenv("ORBITAL_TEST_N", raw)
    assert env_int("ORBITAL_TEST_N", 5) == 10000
    assert edge_config.env_float("ORBITAL_TEST_N", 5.0) == 10000.0


@pytest.mark.parametrize("raw", ["", '""', "ten thousand", "10k"])
def test_unusable_values_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("ORBITAL_TEST_N", raw)
    assert env_int("ORBITAL_TEST_N", 20000) == 20000
    assert env_float("ORBITAL_TEST_N", 0.5) == 0.5
    assert isinstance(env_float("ORBITAL_TEST_N", 5), float)
