"""Rhythm pipeline stages (ARCHITECTURE_PLAN.md §4.2), each a pure function on DataFrames.

Every stage can be switched off from `config/rhythm.yaml` (`stages:`) so the ablation can
measure what it adds. `rhythm.pipeline.analyse` chains them.
"""
from rhythm.stages.activity import activity
from rhythm.stages.clean import clean
from rhythm.stages.discover import discover
from rhythm.stages.habit import habit

__all__ = ["clean", "activity", "discover", "habit"]
