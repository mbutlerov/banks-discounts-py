"""Opt-in historical experiments, separate from canonical bank ingestion.

The worker uses deterministic bank adapters. Experimental AI code is imported
only by an explicit consumer and requires ENABLE_AI_ENRICHMENT for execution.
"""
