from typing import Dict, List, Optional
from .base import Skill, Envelope, SkillJob

class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        """Register a new skill instance."""
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """Retrieve a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> List[str]:
        """List all registered skill names."""
        return list(self._skills.keys())

    def get_definitions(self) -> List[Dict]:
        """
        Get JSON-safe definitions of all skills (useful for LLM context).
        Includes async capability flag and job/output schema metadata for discovery.
        """
        definitions = []
        for skill in self._skills.values():
            definitions.append({
                "name": skill.name,  # name@semver per convention
                "description": skill.description,
                "supports_async": getattr(skill, "supports_async", False),
                "parameters": skill.input_schema.model_json_schema(),
                # Inline schema refs to simplify discovery; can be externalized later
                "job_schema": SkillJob.model_json_schema(),
                "output_schema": Envelope.model_json_schema(),
            })
        return definitions
