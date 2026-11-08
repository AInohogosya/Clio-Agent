"""Offline harness for MISSION.md T1-T4 (§4-§6).

Drives the real components (ContextLayer, GeneralSolverLoop, MemoryStore,
Workspace, StopsLedger, ContinuityManager) through the same code paths the
life loop uses, with a scripted gateway in place of the model. No network,
no live agent, no real services. Every run writes a timestamped bundle.
"""
