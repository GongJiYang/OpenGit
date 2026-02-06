from typing import Dict, List, Optional
from .base import Skill

class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        """Register a new skill instance."""
        if skill.name in self._skills:
            print(f"Warning: Overwriting skill '{skill.name}'")
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
        """
        definitions = []
        for skill in self._skills.values():
            definitions.append({
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.input_schema.model_json_schema()
            })
        return definitions
