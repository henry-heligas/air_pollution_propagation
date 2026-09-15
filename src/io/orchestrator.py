import json
import logging
from typing import Any, Dict
from pydantic import BaseModel, create_model, ValidationError

logger = logging.getLogger(__name__)

class QueryOrchestrator:
    """
    Central template manager. Loads JSON SQL templates into memory, 
    compiles dynamic Pydantic models from their schemas, and hydrates strings.
    """
    def __init__(self, manifest_path: str = "knowledge_tasks/queries.json"):
        self.manifest_path = manifest_path
        self._models: Dict[str, type[BaseModel]] = {}
        
        try:
            with open(manifest_path, "r") as f:
                self.manifest = json.load(f)
            self._compile_schemas()
        except FileNotFoundError:
            logger.warning(f"Manifest not found at {manifest_path}. Run json_query_builder.py first.")
            self.manifest = {}

    def _compile_schemas(self):
        """Reconstructs live Pydantic models from the JSON schema metadata."""
        for query_id, data in self.manifest.items():
            schema = data.get("schema", {})
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            
            fields = {}
            for field_name, field_meta in properties.items():
                # Map JSON types back to Python types
                if field_meta.get("type") == "string":
                    field_type = str
                elif field_meta.get("type") in ["number", "integer"]:
                    field_type = float
                elif field_meta.get("type") == "boolean":
                    field_type = bool
                else:
                    field_type = Any
                
                # Determine requirement status
                default = ... if field_name in required else None
                fields[field_name] = (field_type, default)
                
            self._models[query_id] = create_model(f"{query_id}_DynamicModel", **fields)

    def format_query(self, query_id: str, params: Dict[str, Any]) -> str:
        """
        Validates the raw dictionary parameters against the query's schema,
        then injects them into the raw SQL string.
        """
        if query_id not in self.manifest:
            raise ValueError(f"Query ID '{query_id}' not found in knowledge base.")
            
        try:
            # 1. Strict Validation
            model_class = self._models[query_id]
            validated_params = model_class(**params)
        except ValidationError as e:
            logger.error(f"Validation failed for query '{query_id}': {e}")
            raise ValueError(f"Security/Validation error formatting {query_id}: {e}")
            
        # 2. String Hydration
        template = self.manifest[query_id]["template"]
        return template.format(**validated_params.model_dump())

# Instantiate the singleton so it only loads into memory once when imported
orchestrator = QueryOrchestrator()