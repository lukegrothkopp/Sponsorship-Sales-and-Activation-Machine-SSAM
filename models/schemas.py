from pydantic import BaseModel, Field
from typing import Optional, List, Dict

class Prospect(BaseModel):
    company: str
    industry: Optional[str] = None
    hq_city: Optional[str] = None
    notes: Optional[str] = None
    contacts: Optional[List[Dict]] = None

class PitchInsight(BaseModel):
    opener: str
    rationale: str
    matching_assets: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)

class ActivationItem(BaseModel):
    partner: str
    title: str
    kpi: Optional[str] = None
    media_path: Optional[str] = None
    notes: Optional[str] = None

class ContractQA(BaseModel):
    question: str
    answer: str
    pages: Optional[List[int]] = None
