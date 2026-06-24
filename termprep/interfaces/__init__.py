"""Abstract interfaces for TermPrep extension points."""

from termprep.interfaces.translator import Translator, TranslationRequest, TranslationResponse
from termprep.interfaces.pipeline_step import PipelineStep, StepContext

__all__ = [
    "Translator",
    "TranslationRequest",
    "TranslationResponse",
    "PipelineStep",
    "StepContext",
]
