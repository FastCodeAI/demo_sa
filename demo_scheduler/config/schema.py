from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class Holiday(BaseModel):
    week: int
    hours_lost: int


class MaintenanceBlock(BaseModel):
    machine: str
    week: int
    hours_lost: int


class Calendar(BaseModel):
    shifts_per_day: int = 3
    days_per_week: int = 5
    working_hours_per_shift: int = 8
    holidays: list[Holiday] = Field(default_factory=list)
    maintenance_blocks: list[MaintenanceBlock] = Field(default_factory=list)

    @property
    def hours_per_week(self) -> int:
        return self.shifts_per_day * self.days_per_week * self.working_hours_per_shift


class Machine(BaseModel):
    eligible_formats: list[str]
    container: str = "glass"


class ChangeoverDefaults(BaseModel):
    same_format: float = 0
    minor_volume_change: float = 2
    major_volume_change: float = 6
    container_change: float = 12


class ChangeoverHours(BaseModel):
    default: ChangeoverDefaults = Field(default_factory=ChangeoverDefaults)
    per_machine_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)


class Band(BaseModel):
    monthly_min: float
    monthly_max: float


class Lags(BaseModel):
    prod_to_pack_min_days: int = 4
    prod_to_label_min_days: int = 3


class Production(BaseModel):
    shelf_life_months_default: int = 24
    shelf_life_months_long: int = 36
    long_sl_skus: list[int] = Field(default_factory=list)
    campaign_coverage_months_24mo: int = 6
    campaign_coverage_months_36mo: int = 9
    scrap_rate_default: float = 0.02
    evaton_min_gap_weeks: int = 3
    evaton_skus: list[int] = Field(default_factory=list)


class GlassVolumeThreshold(BaseModel):
    min_vol: float | None = None
    prefer_above: float | None = None
    max_vol: float | None = None


class ObjectiveWeights(BaseModel):
    fulfilment: float = 1.0
    changeover: float = 0.1
    idle: float = 0.05
    tie_split: float = 0.2
    late: float = 0.5


class Rating(BaseModel):
    vip_multiplier: float = 10_000
    delay_step: float = 1


class SolverConfig(BaseModel):
    time_limit_seconds: int = 300
    mip_gap: float = 0.01


class Horizon(BaseModel):
    quarter: str = "Q1"
    include_backorder: bool = True

    @model_validator(mode="after")
    def _validate_quarter(self) -> "Horizon":
        if self.quarter not in {"Q1", "Q2", "Q3", "Q4"}:
            raise ValueError(f"quarter must be one of Q1..Q4, got {self.quarter!r}")
        return self


class Config(BaseModel):
    horizon: Horizon = Field(default_factory=Horizon)
    calendar: Calendar = Field(default_factory=Calendar)
    machines: dict[str, Machine]
    throughput_units_per_hour: dict[str, dict[str, float]]
    changeover_hours: ChangeoverHours = Field(default_factory=ChangeoverHours)
    bands: dict[str, Band] = Field(default_factory=dict)
    lags: Lags = Field(default_factory=Lags)
    production: Production = Field(default_factory=Production)
    orgs_no_split: list[str] = Field(default_factory=list)
    glass_volume_thresholds: dict[str, GlassVolumeThreshold] = Field(default_factory=dict)
    objective_weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    rating: Rating = Field(default_factory=Rating)
    solver: SolverConfig = Field(default_factory=SolverConfig)

    @model_validator(mode="after")
    def _check_throughput_coverage(self) -> "Config":
        for m, machine in self.machines.items():
            if m not in self.throughput_units_per_hour:
                raise ValueError(f"throughput_units_per_hour missing for machine {m!r}")
            tp = self.throughput_units_per_hour[m]
            for fmt in machine.eligible_formats:
                if fmt not in tp:
                    raise ValueError(
                        f"throughput_units_per_hour[{m!r}] missing format {fmt!r}"
                    )
        return self
