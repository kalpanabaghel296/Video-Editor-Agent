"""
Planning router — endpoints to generate and inspect AI Director edit decision lists (EDL).
"""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.agents.director import load_edl, plan_edit
from app.models.edl import EditDecisionList

router = APIRouter(prefix="/jobs", tags=["AI Planning"])


@router.post(
    "/{job_id}/plan",
    response_model=EditDecisionList,
    status_code=status.HTTP_200_OK,
    summary="Generate an Edit Decision List (EDL) using AI Director",
    description=(
        "Analyzes user instruction, transcript, silence intervals, and scene cuts "
        "to formulate the optimal sequence of cuts. Persists to ``jobs/{job_id}/planning/edl.json``."
    ),
)
def generate_edit_plan(
    job_id: uuid.UUID,
    instruction: Optional[str] = Query(
        default=None,
        description="Optional prompt override for directing the edit.",
    ),
) -> EditDecisionList:
    try:
        return plan_edit(job_id, instruction_override=instruction)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI Edit Planning failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/plan",
    response_model=EditDecisionList,
    status_code=status.HTTP_200_OK,
    summary="Retrieve previously generated Edit Decision List",
    description="Returns the EDL stored in ``jobs/{job_id}/planning/edl.json``.",
)
def get_edit_plan(job_id: uuid.UUID) -> EditDecisionList:
    try:
        return load_edl(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error loading EDL: {exc}",
        ) from exc
