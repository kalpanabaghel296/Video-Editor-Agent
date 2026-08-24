# Project Architecture

This repository is organized for a modular AI video editing workflow with clear separation of concerns:

- `backend/app/api`: HTTP endpoints and request handling.
- `backend/app/analysis`: media understanding modules (metadata, transcription, scenes, silence).
- `backend/app/agents`: orchestration roles for directing, planning, and validation.
- `backend/app/editing`: future editing operations and render pipeline.
- `backend/app/models`: shared data models used across modules.
- `backend/app/services`: application-level service layer.
- `backend/app/utils`: shared helper utilities.
- `frontend/src`: UI components, pages, hooks, and service clients.
- `jobs/`: job artifacts and temporary execution metadata.
- `test_videos/`: sample input videos for local testing.
- `outputs/`: generated export outputs.
