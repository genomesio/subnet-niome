"""
Protocol definitions for the Drug Response Prediction Subnet.

This module defines the communication protocols between validators and miners
for drug response prediction tasks using synthetic genomic data.
"""
from typing import Optional
from pydantic import BaseModel
from niome_subnet.genomics.model import Task


class GenomicsTaskSynapse(BaseModel):
    """Protocol for genomics simulation tasks."""

    task: Optional[Task] = None
    presigned_url: str = ""
    timeout: Optional[float] = None
