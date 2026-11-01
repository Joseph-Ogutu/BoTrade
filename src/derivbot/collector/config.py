"""Collector configuration. Contains no secrets and refuses any URL that could carry credentials."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from derivbot.deriv.public_stream import PUBLIC_WS_URL


class SymbolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[A-Za-z0-9_]{1,20}$")
    # 1 or 2 for the 1s and 2s synthetic indices (gap detection); 0 means irregular, no gap check.
    expected_interval: int = Field(ge=0, le=60)
    # True for markets that tick around the clock; only these count towards the healthcheck.
    always_open: bool


class CollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = PUBLIC_WS_URL
    symbols: list[SymbolSpec] = Field(min_length=1)
    db_path: str = "/data/ticks.sqlite"
    heartbeat_path: str = "/data/heartbeat.json"
    heartbeat_interval_s: float = Field(default=10.0, gt=0)
    flush_interval_s: float = Field(default=2.0, gt=0)
    flush_every: int = Field(default=200, ge=1)
    stale_after_s: float = Field(default=30.0, gt=0)
    backoff_base_s: float = Field(default=1.0, gt=0)
    backoff_cap_s: float = Field(default=60.0, gt=0)
    health_max_age_s: float = Field(default=120.0, gt=0)
    max_silence_exit_s: float = Field(default=600.0, gt=0)

    @field_validator("url")
    @classmethod
    def _public_endpoint_only(cls, v: str) -> str:
        if v != PUBLIC_WS_URL:
            raise ValueError(f"collector may only use the public endpoint {PUBLIC_WS_URL}")
        return v

    @model_validator(mode="after")
    def _unique_symbols(self) -> CollectorConfig:
        names = [s.symbol for s in self.symbols]
        if len(set(names)) != len(names):
            raise ValueError("duplicate symbols")
        return self

    @property
    def symbol_names(self) -> list[str]:
        return [s.symbol for s in self.symbols]

    @property
    def required_symbols(self) -> list[str]:
        return [s.symbol for s in self.symbols if s.always_open]

    def spec(self, symbol: str) -> SymbolSpec:
        return next(s for s in self.symbols if s.symbol == symbol)
