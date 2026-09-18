from pydantic import BaseModel, Field
from typing import List

class ReportSection(BaseModel):
    narrative: str = Field(
        ..., 
        description="Objective, scientific analysis in 2-3 paragraphs. No alarmist language."
    )
    key_takeaways: List[str] = Field(
        ..., 
        description="Exactly 3 bullet points summarizing the most critical data.",
        max_length=3
    )

class AssessmentReport(BaseModel):
    executive_summary: ReportSection
    baseline_conditions: ReportSection
    proposed_impact: ReportSection
    regulatory_recommendations: str = Field(
        ..., 
        description="Clear next steps based on EPA guidelines (e.g., 'Requires Title V Review' or 'Below Action Threshold')."
    )