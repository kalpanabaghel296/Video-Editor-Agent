"""Pydantic models for post-render quality validation."""

from pydantic import BaseModel, Field


class ValidationCheck(BaseModel):
    """Result of an individual quality check."""

    name: str = Field(..., description="Name of the check performed.")
    passed: bool = Field(..., description="Whether the check passed.")
    details: str = Field(..., description="Human-readable check findings.")


class ValidationReport(BaseModel):
    """Comprehensive quality validation report for a rendered video."""

    job_id: str = Field(..., description="UUID of the job.")
    is_valid: bool = Field(..., description="Overall validation status.")
    checks: list[ValidationCheck] = Field(
        default_factory=list, description="List of individual checks."
    )
    rendered_duration: float = Field(
        ..., description="Duration of rendered video in seconds."
    )
    expected_duration: float = Field(
        ..., description="Expected duration from EDL in seconds."
    )
    file_size_bytes: int = Field(
        ..., description="Size of rendered file in bytes."
    )
    created_at: str = Field(..., description="ISO timestamp of validation.")
