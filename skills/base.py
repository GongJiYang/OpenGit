from abc import ABC, abstractmethod
from typing import Type, Any
from pydantic import BaseModel

class Skill(ABC):
    """
    Abstract Base Class for all Skills.
    Enforces Strict Inputs via Pydantic.
    """
    name: str = "base_skill"
    description: str = "Base skill description"
    input_schema: Type[BaseModel] # The Pydantic model class for arguments

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """
        Execute the skill with validated arguments.
        """
        pass

    def validate_and_execute(self, **kwargs) -> Any:
        """
        Validates input against input_schema then executes.
        """
        # Pydantic validation
        validated_args = self.input_schema(**kwargs)
        # Execute with dict values
        return self.execute(**validated_args.model_dump())
